"""Formal prompt-driven single investigator over one frozen snapshot."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from openjiuwen.core.single_agent import AgentCard, ReActAgent
from openjiuwen.core.single_agent.rail import AgentCallbackContext, AgentRail

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.base import ContractModel
from jindiao.contracts.execution import RunTermination, RunTerminationReason
from jindiao.contracts.investigation import (
    DueDiligenceCheckCatalog,
    FactEvidenceRef,
    RiskItem,
)
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.investigation import (
    CHECK_CATALOG,
    SnapshotReadGrant,
    SnapshotReadToolset,
    SubmissionBlackboard,
    SubmissionGrant,
)
from jindiao.orchestration.base import BudgetLedger, CancellationToken, check_cancellation
from jindiao.orchestration.budgeted_model import BudgetedModel
from jindiao.orchestration.react_model import build_react_agent_config
from jindiao.prompts import PromptBundle, PromptInvocation

if TYPE_CHECKING:
    from jindiao.contracts.execution import ExecutionCost
    from jindiao.orchestration.agent_runtime import (
        AgentExecutionEvent,
        AgentExecutionRuntime,
    )


class SubmitInvestigationSelfCheckInput(ContractModel):
    """Empty input because the fixed catalog and accepted results are Run-bound."""


@dataclass(slots=True)
class _SelfCheckState:
    completed: bool = False


class _FinishAfterSubmissionRail(AgentRail):  # type: ignore[misc]  # Upstream has no typing marker.
    def __init__(self, state: _SelfCheckState) -> None:
        self._state = state

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self._state.completed:
            ctx.request_force_finish(
                {
                    "output": "All fixed checks submitted and completeness verified",
                    "result_type": "answer",
                }
            )


@dataclass(frozen=True, slots=True)
class SingleInvestigatorBindings:
    agent: ReActAgent
    reader: SnapshotReadToolset
    blackboard: SubmissionBlackboard
    self_check: _SelfCheckState
    invocation: PromptInvocation


@dataclass(frozen=True, slots=True)
class SingleInvestigatorRun:
    agent_result: AgentInvestigationResult
    events: tuple[AgentExecutionEvent, ...]
    self_check_completed: bool
    snapshot_reads: tuple[object, ...]
    investigation_cost: ExecutionCost
    termination: RunTermination


class SingleInvestigatorAgent:
    """One actual ReActAgent owns every enabled fixed investigation check."""

    agent_id = "single-investigator"
    role = "single-investigator"

    def __init__(
        self,
        *,
        prompt_bundle: PromptBundle,
        check_catalog: DueDiligenceCheckCatalog = CHECK_CATALOG,
    ) -> None:
        self._prompt_bundle = prompt_bundle
        self._check_catalog = check_catalog

    async def run(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        timeout_seconds: float,
        model_temperature: float = 0,
        max_iterations: int = 40,
        event_sink: Callable[[AgentExecutionEvent], Awaitable[None]] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> SingleInvestigatorRun:
        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        check_cancellation(cancellation_token)

        bindings = self.build_react_agent(
            snapshot=snapshot,
            budget_ledger=budget_ledger,
            run_id=run_id,
            model_name=model_name,
            model_provider=model_provider,
            model=model,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            model_temperature=model_temperature,
            model_timeout_seconds=timeout_seconds,
            max_iterations=max_iterations,
        )
        await bindings.agent.register_rail(_FinishAfterSubmissionRail(bindings.self_check))
        task_ids = self._task_ids()
        request = AgentExecutionRequest(
            run_id=run_id,
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.INVESTIGATION,
            session_id=f"{run_id}:{self.agent_id}",
            query=bindings.invocation.user_payload_json,
            task_ids=task_ids,
            prompt_version=bindings.invocation.prompt_version,
            prompt_sha256=bindings.invocation.prompt_sha256,
            timeout_seconds=timeout_seconds,
        )
        collected_events: list[AgentExecutionEvent] = []
        stream_kwargs: dict[str, object] = {}
        if "cancellation_token" in inspect.signature(runtime.stream).parameters:
            stream_kwargs["cancellation_token"] = cancellation_token
        async for event in runtime.stream(bindings.agent, request, **stream_kwargs):  # type: ignore[arg-type]
            check_cancellation(cancellation_token)
            collected_events.append(event)
            if event_sink is not None:
                await event_sink(event)
        events = tuple(collected_events)
        budget_usage = budget_ledger.snapshot()
        if budget_usage.exhausted_reason is not None:
            raise AgentExecutionError(
                budget_usage.exhausted_reason,
                details={
                    "run_id": run_id,
                    "agent_id": self.agent_id,
                    **budget_ledger.error_details(),
                },
            )
        results = bindings.blackboard.accepted_results
        submitted = {item.check_id for item in results}
        missing = tuple(
            check_id for check_id in self._enabled_check_ids() if check_id not in submitted
        )
        if missing:
            raise AgentExecutionError(
                "single investigator missing fixed checks",
                details={"missing_check_ids": list(missing), **budget_ledger.error_details()},
            )
        if not bindings.self_check.completed:
            raise AgentExecutionError(
                "single investigator ended without completeness self-check",
                details=budget_ledger.error_details(),
            )

        risk_items = self._unique_risks(
            tuple(risk for result in results for risk in result.risk_items)
        )
        fact_refs = self._unique_fact_refs(
            tuple(fact for result in results for fact in result.fact_evidence_refs)
        )
        agent_result = AgentInvestigationResult(
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=task_ids,
            check_results=results,
            risk_items=risk_items,
            fact_evidence_refs=fact_refs,
            prompt_version=bindings.invocation.prompt_version,
        )
        return SingleInvestigatorRun(
            agent_result=agent_result,
            events=events,
            self_check_completed=True,
            snapshot_reads=bindings.reader.audit_records,
            investigation_cost=budget_ledger.to_execution_cost(),
            termination=RunTermination(
                reason=RunTerminationReason.COMPLETED,
                completed_task_ids=task_ids,
            ),
        )

    def build_react_agent(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        model_temperature: float = 0,
        model_timeout_seconds: float = 300,
        max_iterations: int = 40,
    ) -> SingleInvestigatorBindings:
        checks = tuple(item for item in self._check_catalog.checks if item.enabled)
        if not checks:
            raise AgentExecutionError("single investigator has no enabled fixed checks")
        if self._check_catalog.acquisition_catalog_version != snapshot.acquisition_catalog_version:
            raise AgentExecutionError("check catalog does not match frozen snapshot")
        if max_iterations < len(checks) * 2 + 2:
            raise ValueError("single investigator iteration budget cannot submit all checks")

        task_ids = {item.check_id: f"check:{item.check_id}" for item in checks}
        read_grants = tuple(
            SnapshotReadGrant(
                task_id=task_ids[item.check_id],
                check_ids=(item.check_id,),
                allowed_submodule_ids=(
                    *item.required_submodule_ids,
                    *item.optional_submodule_ids,
                ),
            )
            for item in checks
        )
        submission_grants = tuple(
            SubmissionGrant(
                agent_id=self.agent_id,
                task_id=task_ids[item.check_id],
                check_ids=(item.check_id,),
            )
            for item in checks
        )
        reader = SnapshotReadToolset(
            snapshot=snapshot,
            run_id=run_id,
            agent_id=self.agent_id,
            grants=read_grants,
            budget_ledger=budget_ledger,
        )
        blackboard = SubmissionBlackboard(
            run_id=run_id,
            snapshot=snapshot,
            check_catalog=self._check_catalog,
            grants=submission_grants,
            reviewer_agent_ids=(),
            budget_ledger=budget_ledger,
        )
        invocation = self._prompt_invocation(
            snapshot=snapshot,
            run_id=run_id,
            checks=checks,
            task_ids=task_ids,
        )
        self_check = _SelfCheckState()

        def complete_self_check() -> None:
            expected = self._enabled_check_ids()
            submitted = tuple(item.check_id for item in blackboard.accepted_results)
            if submitted != expected:
                raise AgentExecutionError(
                    "single investigator self-check found missing fixed checks",
                    details={"expected": list(expected), "submitted": list(submitted)},
                )
            self_check.completed = True

        react_agent = ReActAgent(
            AgentCard(id=self.agent_id, name="Single Due Diligence Investigator")
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
        underlying_model = model
        if underlying_model is None:
            underlying_model = react_agent._get_llm()
        react_agent.set_llm(cast(Any, BudgetedModel(underlying_model, budget_ledger=budget_ledger)))
        evidence_ids = {item.evidence_id for item in snapshot.evidence}
        prefix = "e"
        while any(f"{prefix}{index}" in evidence_ids for index in range(len(evidence_ids))):
            prefix = "_" + prefix
        evidence_aliases = {
            item.evidence_id: f"{prefix}{index}" for index, item in enumerate(snapshot.evidence)
        }
        assigned_context_tool = reader.build_tools(evidence_aliases=evidence_aliases)[0]
        abilities = (
            assigned_context_tool,
            blackboard.build_submit_bound_check_results_tool(
                agent_id=self.agent_id,
                prompt_version=invocation.prompt_version,
                on_complete=complete_self_check,
                evidence_aliases=evidence_aliases,
            ),
        )
        registrations = [
            react_agent.ability_manager.add_ability(item.card, item) for item in abilities
        ]
        if not all(item.added for item in registrations):
            react_agent.ability_manager.teardown_tools()
            raise AgentExecutionError("single investigator Tool registration failed")
        return SingleInvestigatorBindings(
            agent=react_agent,
            reader=reader,
            blackboard=blackboard,
            self_check=self_check,
            invocation=invocation,
        )

    def _prompt_invocation(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        run_id: str,
        checks: tuple[Any, ...],
        task_ids: dict[str, str],
    ) -> PromptInvocation:
        base = self._prompt_bundle.investigation(self.role)
        prompt_sections = [base.system_prompt]
        versions = [base.prompt_version]
        for check in checks:
            artifact = self._prompt_bundle.check_prompt(check.prompt_template_id)
            definition = json.dumps(
                check.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            prompt_sections.append(f"## {check.check_id}\n{definition}\n\n{artifact.system_prompt}")
            versions.append(f"{check.check_id}:{artifact.version}")
        system_prompt = "\n\n".join(prompt_sections)
        user_payload = {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "subject_id": snapshot.subject.subject_id,
            "report_as_of": snapshot.report_as_of.isoformat(),
            "check_catalog_version": self._check_catalog.catalog_version,
            "assignments": [
                {
                    "task_id": task_ids[item.check_id],
                    "check_id": item.check_id,
                }
                for item in checks
            ],
        }
        return PromptInvocation(
            phase=AgentResultPhase.INVESTIGATION,
            role=self.role,
            prompt_version="+".join(versions),
            prompt_sha256=hashlib.sha256(system_prompt.encode()).hexdigest(),
            system_prompt=system_prompt,
            user_payload_json=json.dumps(
                user_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _enabled_check_ids(self) -> tuple[str, ...]:
        return tuple(item.check_id for item in self._check_catalog.checks if item.enabled)

    def _task_ids(self) -> tuple[str, ...]:
        return tuple(f"check:{check_id}" for check_id in self._enabled_check_ids())

    @staticmethod
    def _unique_risks(items: tuple[RiskItem, ...]) -> tuple[RiskItem, ...]:
        by_id: dict[str, RiskItem] = {}
        for item in items:
            by_id.setdefault(item.risk_id, item)
        return tuple(by_id.values())

    @staticmethod
    def _unique_fact_refs(
        items: tuple[FactEvidenceRef, ...],
    ) -> tuple[FactEvidenceRef, ...]:
        by_key: dict[tuple[str, str], FactEvidenceRef] = {}
        for item in items:
            by_key.setdefault((item.evidence_id, item.fact_path), item)
        return tuple(by_key.values())


__all__ = [
    "SingleInvestigatorAgent",
    "SingleInvestigatorBindings",
    "SingleInvestigatorRun",
    "SubmitInvestigationSelfCheckInput",
]
