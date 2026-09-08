"""Enterprise Context Agent acquisition controller and public contribution contract."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.core.single_agent import AgentCard, ReActAgent
from pydantic import Field, model_validator

from jindiao.application.errors import AgentExecutionError, JindiaoError
from jindiao.contracts.acquisition import SubmoduleAvailability, SubmoduleContext
from jindiao.contracts.acquisition_catalog import AcquisitionCatalog
from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject
from jindiao.contracts.evidence import CoverageCompleteness, Evidence
from jindiao.contracts.investigation import FactEvidenceRef
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.orchestration.react_model import build_react_agent_config
from jindiao.prompts import PromptBundle, PromptInvocation
from jindiao.tianyancha import (
    ENTERPRISE_CONTEXT_AGENT_ID,
    GatewayInvocation,
    GatewaySubmoduleObservation,
    TianyanchaMcpGateway,
)

if TYPE_CHECKING:
    from jindiao.orchestration.agent_runtime import (
        AgentExecutionEvent,
        AgentExecutionRuntime,
    )
from jindiao.orchestration.base import CancellationToken, check_cancellation


class CollectEnterpriseContextInput(ContractModel):
    submodule_ids: tuple[str, ...] = Field(min_length=1)


class SubmitEnterpriseContextInput(ContractModel):
    pass


@dataclass(frozen=True, slots=True)
class EnterpriseContextAgentRun:
    acquisition: EnterpriseContextAcquisitionResult
    events: tuple[AgentExecutionEvent, ...]


@dataclass(slots=True)
class _SubmissionState:
    acquisition: EnterpriseContextAcquisitionResult | None = None
    submitted: bool = False
    collection_attempts: int = 0
    collection_error_type: str | None = None
    collection_error_code: str | None = None


class EnterpriseContextAcquisitionResult(ContractModel):
    subject: ResolvedSubject
    report_as_of: date
    acquisition_catalog_version: str = Field(min_length=1)
    planned_submodule_ids: tuple[str, ...] = Field(min_length=1)
    source_manifest_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_names: tuple[str, ...]
    submodules: tuple[SubmoduleContext, ...]
    evidence: tuple[Evidence, ...]
    invocations: tuple[GatewayInvocation, ...]
    agent_result: AgentInvestigationResult
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_acquisition(self) -> EnterpriseContextAcquisitionResult:
        submodule_ids = tuple(item.submodule_id for item in self.submodules)
        if submodule_ids != self.planned_submodule_ids:
            raise ValueError("acquisition submodules must preserve the planned id order")
        if len(submodule_ids) != len(set(submodule_ids)):
            raise ValueError("enterprise context acquisition ids must be unique")
        if self.agent_result.phase is not AgentResultPhase.ACQUISITION:
            raise ValueError("enterprise context contribution must be acquisition phase")
        if self.agent_result.risk_items or self.agent_result.check_results:
            raise ValueError("enterprise context acquisition cannot contain risk conclusions")
        evidence_ids = {item.evidence_id for item in self.evidence}
        self.agent_result.require_known_evidence(evidence_ids)
        referenced = {
            evidence_id for submodule in self.submodules for evidence_id in submodule.evidence_ids
        }
        if not referenced <= evidence_ids:
            raise ValueError("acquisition submodule references unknown Evidence")
        return self


class EnterpriseContextAgent:
    """Own subject resolution, discovery, and full-catalog evidence acquisition."""

    agent_id = ENTERPRISE_CONTEXT_AGENT_ID
    role = "enterprise-context"

    def __init__(
        self,
        *,
        gateway: TianyanchaMcpGateway,
        prompt_bundle: PromptBundle,
        acquisition_catalog: AcquisitionCatalog,
    ) -> None:
        if gateway.agent_id != self.agent_id:
            raise AgentExecutionError("Context Agent requires its own bound MCP gateway")
        self._gateway = gateway
        self._prompt_bundle = prompt_bundle
        self._acquisition_catalog = acquisition_catalog
        self._plan_ids = acquisition_catalog.default_plan_ids
        self._enterprise: EnterpriseInput | None = None
        self._acquire_lock = asyncio.Lock()
        self._acquire_task: asyncio.Task[EnterpriseContextAcquisitionResult] | None = None

    async def acquire(
        self,
        enterprise: EnterpriseInput,
    ) -> EnterpriseContextAcquisitionResult:
        async with self._acquire_lock:
            if self._enterprise is not None and self._enterprise != enterprise:
                raise AgentExecutionError(
                    "Enterprise Context Agent is already bound to another enterprise"
                )
            self._enterprise = enterprise
            if self._acquire_task is None:
                self._acquire_task = asyncio.create_task(self._acquire(enterprise))
            task = self._acquire_task
        return await task

    async def run(
        self,
        enterprise: EnterpriseInput,
        *,
        runtime: AgentExecutionRuntime,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        timeout_seconds: float,
        model_temperature: float = 0,
        max_iterations: int = 4,
        event_sink: Callable[[AgentExecutionEvent], Awaitable[None]] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> EnterpriseContextAgentRun:
        """Let a ReAct model plan the bounded collection and explicit submission."""

        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        invocation = self._prompt_invocation(enterprise)
        check_cancellation(cancellation_token)
        self._gateway.set_cancellation_token(cancellation_token)
        react_agent, state = self.build_react_agent(
            enterprise,
            model_name=model_name,
            model_provider=model_provider,
            model=model,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            model_temperature=model_temperature,
            model_timeout_seconds=timeout_seconds,
            max_iterations=max_iterations,
        )
        request = AgentExecutionRequest(
            run_id=self._gateway.run_id,
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.ACQUISITION,
            session_id=f"{self._gateway.run_id}:{self.agent_id}",
            query=invocation.user_payload_json,
            task_ids=tuple(f"acquire:{item}" for item in self._plan_ids),
            prompt_version=invocation.prompt_version,
            prompt_sha256=invocation.prompt_sha256,
            timeout_seconds=timeout_seconds,
        )
        collected_events: list[AgentExecutionEvent] = []
        stream_kwargs: dict[str, object] = {}
        if "cancellation_token" in inspect.signature(runtime.stream).parameters:
            stream_kwargs["cancellation_token"] = cancellation_token
        async for event in runtime.stream(react_agent, request, **stream_kwargs):  # type: ignore[arg-type]
            check_cancellation(cancellation_token)
            collected_events.append(event)
            if event_sink is not None:
                await event_sink(event)
        events = tuple(collected_events)
        if state.acquisition is None or not state.submitted:
            event_type_counts: dict[str, int] = {}
            for event in events:
                event_name = event.event_type.value
                event_type_counts[event_name] = event_type_counts.get(event_name, 0) + 1
            raise AgentExecutionError(
                "Enterprise Context Agent ended without a valid coverage submission",
                details={
                    "run_id": self._gateway.run_id,
                    "acquisition_collected": state.acquisition is not None,
                    "submitted": state.submitted,
                    "collection_attempts": state.collection_attempts,
                    "collection_error_type": state.collection_error_type,
                    "collection_error_code": state.collection_error_code,
                    "subject_resolved": self._gateway.subject is not None,
                    "manifest_discovered": self._gateway.manifest is not None,
                    "mcp_call_count": self._gateway.mcp_calls,
                    "event_type_counts": event_type_counts,
                },
            )
        return EnterpriseContextAgentRun(acquisition=state.acquisition, events=events)

    def build_react_agent(
        self,
        enterprise: EnterpriseInput,
        *,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        model_temperature: float = 0,
        model_timeout_seconds: float = 300,
        max_iterations: int = 4,
    ) -> tuple[ReActAgent, _SubmissionState]:
        """Build one Context Agent with only collect and submit capabilities."""

        if max_iterations < 2:
            raise ValueError("Context Agent requires at least two iterations")
        invocation = self._prompt_invocation(enterprise)
        state = _SubmissionState()

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self._gateway.run_id}.context.collect",
                name="collect_enterprise_context",
                description=(
                    "Collect the exact requested canonical report submodules through the "
                    "run-bound Tianyancha gateway."
                ),
                input_params=CollectEnterpriseContextInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def collect_enterprise_context(
            submodule_ids: list[str],
        ) -> dict[str, object]:
            requested = tuple(submodule_ids)
            if requested != self._plan_ids:
                missing = sorted(set(self._plan_ids) - set(requested))
                extra = sorted(set(requested) - set(self._plan_ids))
                raise AgentExecutionError(
                    "Context Agent must request the frozen acquisition plan",
                    details={"missing": missing, "extra": extra},
                )
            state.collection_attempts += 1
            try:
                state.acquisition = await self.acquire(enterprise)
            except Exception as error:
                state.collection_error_type = type(error).__name__
                state.collection_error_code = (
                    error.code.value if isinstance(error, JindiaoError) else None
                )
                raise
            return {
                "status": "ready_for_submission",
                "subject_id": state.acquisition.subject.subject_id,
                "coverage_count": len(state.acquisition.submodules),
                "evidence_ids": [item.evidence_id for item in state.acquisition.evidence],
            }

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self._gateway.run_id}.context.submit",
                name="submit_enterprise_context",
                description=(
                    "Submit the already collected acquisition Evidence and coverage manifest."
                ),
                input_params=SubmitEnterpriseContextInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_enterprise_context() -> dict[str, object]:
            if state.acquisition is None:
                raise AgentExecutionError(
                    "Context Agent cannot submit before collecting the report catalog"
                )
            if tuple(item.submodule_id for item in state.acquisition.submodules) != self._plan_ids:
                raise AgentExecutionError("Context Agent submission coverage is incomplete")
            state.submitted = True
            return {
                "status": "accepted",
                "coverage_count": len(self._plan_ids),
                "evidence_count": len(state.acquisition.evidence),
            }

        react_agent = ReActAgent(
            AgentCard(id=self.agent_id, name="Enterprise Context Agent")
        ).configure(
            build_react_agent_config(
                model_name=model_name,
                model_provider=model_provider,
                model_api_key=model_api_key,
                model_base_url=model_base_url,
                model_temperature=model_temperature,
                model_timeout_seconds=model_timeout_seconds,
                system_prompt=invocation.system_prompt,
                max_iterations=max_iterations,
                parallel_tool_calls=False,
            )
        )
        if not isinstance(react_agent, ReActAgent):
            raise RuntimeError("openJiuwen did not build a ReActAgent")
        if model is not None:
            react_agent.set_llm(cast(Any, model))
        registrations = (
            react_agent.ability_manager.add_ability(
                collect_enterprise_context.card,
                collect_enterprise_context,
            ),
            react_agent.ability_manager.add_ability(
                submit_enterprise_context.card,
                submit_enterprise_context,
            ),
        )
        if not all(item.added for item in registrations):
            react_agent.ability_manager.teardown_tools()
            raise AgentExecutionError("Context Agent tool registration failed")
        return react_agent, state

    async def _acquire(
        self,
        enterprise: EnterpriseInput,
    ) -> EnterpriseContextAcquisitionResult:
        invocation = self._prompt_invocation(enterprise)
        subject, manifest = await self._gateway.initialize(enterprise)
        observations = await asyncio.gather(
            *(self._collect_submodule(subject, submodule_id) for submodule_id in self._plan_ids)
        )
        submodules = tuple(
            item if isinstance(item, SubmoduleContext) else item.to_submodule_context()
            for item in observations
        )
        evidence = self._unique_evidence(
            tuple(
                evidence_item
                for item in observations
                if isinstance(item, GatewaySubmoduleObservation)
                for evidence_item in item.evidence
            )
        )
        facts = tuple(
            FactEvidenceRef(
                evidence_id=item.evidence_id,
                fact_path=self._fact_path(item, submodules),
                summary=f"{item.source_tool or 'source'} 事实记录",
            )
            for item in evidence
        )
        task_ids = tuple(f"acquire:{item}" for item in self._plan_ids)
        contribution = AgentInvestigationResult(
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.ACQUISITION,
            status=AgentStatus.COMPLETED,
            task_ids=task_ids,
            fact_evidence_refs=facts,
            prompt_version=invocation.prompt_version,
        )
        return EnterpriseContextAcquisitionResult(
            subject=subject,
            report_as_of=self._gateway.report_as_of,
            acquisition_catalog_version=self._acquisition_catalog.catalog_version,
            planned_submodule_ids=self._plan_ids,
            source_manifest_version=manifest.fingerprint,
            capability_names=manifest.tool_names,
            submodules=submodules,
            evidence=evidence,
            invocations=self._gateway.invocations,
            agent_result=contribution,
            prompt_sha256=invocation.prompt_sha256,
        )

    def _prompt_invocation(self, enterprise: EnterpriseInput) -> PromptInvocation:
        return self._prompt_bundle.build_invocation(
            phase=AgentResultPhase.ACQUISITION,
            role=self.role,
            runtime_data={
                "run_id": self._gateway.run_id,
                "report_as_of": self._gateway.report_as_of.isoformat(),
                "enterprise": enterprise.model_dump(mode="json"),
                "acquisition_catalog_version": self._acquisition_catalog.catalog_version,
                "submodules": list(self._plan_ids),
            },
        )

    async def _collect_submodule(
        self,
        subject: ResolvedSubject,
        submodule_id: str,
    ) -> SubmoduleContext | GatewaySubmoduleObservation:
        try:
            observation = await self._gateway.acquire_submodule(
                submodule_id,
                subject_id=subject.subject_id,
            )
        except AgentExecutionError as error:
            if "budget exhausted" not in error.message:
                raise
            return SubmoduleContext(
                submodule_id=submodule_id,
                availability=SubmoduleAvailability.NOT_REQUESTED,
                completeness=CoverageCompleteness.UNKNOWN,
                facts={"reason": "acquisition_budget_exhausted"},
                unresolved_gap_ids=(f"gap:{submodule_id}:not_requested",),
            )
        return observation

    @staticmethod
    def _unique_evidence(items: Sequence[Evidence]) -> tuple[Evidence, ...]:
        by_id: dict[str, Evidence] = {}
        for item in items:
            by_id.setdefault(item.evidence_id, item)
        return tuple(by_id.values())

    @staticmethod
    def _fact_path(
        evidence: Evidence,
        submodules: tuple[SubmoduleContext, ...],
    ) -> str:
        for submodule in submodules:
            if evidence.evidence_id in submodule.evidence_ids:
                return f"submodules.{submodule.submodule_id}"
        return "evidence"


__all__ = [
    "CollectEnterpriseContextInput",
    "EnterpriseContextAcquisitionResult",
    "EnterpriseContextAgent",
    "EnterpriseContextAgentRun",
    "SubmitEnterpriseContextInput",
]
