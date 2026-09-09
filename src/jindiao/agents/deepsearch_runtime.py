"""Formal prompt-driven DeepSearch acquisition agent."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.core.single_agent import AgentCard, ReActAgent
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec
from pydantic import Field

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import SupplementTask, SupplementTaskReason
from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import FactEvidenceRef
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.deepsearch import AnnualReportSocialSecurityOutcome
from jindiao.orchestration.react_model import build_react_agent_config
from jindiao.prompts import PromptBundle, PromptInvocation

from .deepsearch_agent import DeepSearchSupplementOutcome
from .deepsearch_tools import (
    TIANYANCHA_ANNUAL_REPORT_TOOL_NAME,
    TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
    AnnualReportToolInput,
    register_deepsearch_tool_providers,
)

if TYPE_CHECKING:
    from jindiao.orchestration.agent_runtime import (
        AgentExecutionEvent,
        AgentExecutionRuntime,
    )
from jindiao.orchestration.base import CancellationToken, check_cancellation


class InvokableTool(Protocol):
    async def invoke(self, values: dict[str, object]) -> dict[str, object]: ...


class SubmitSupplementOutcomeInput(ContractModel):
    task_id: str = Field(min_length=1)


class RunBoundedSearchInput(ContractModel):
    task_id: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class DeepSearchAgentRun:
    outcomes: tuple[DeepSearchSupplementOutcome, ...]
    agent_result: AgentInvestigationResult
    events: tuple[AgentExecutionEvent, ...]


@dataclass(slots=True)
class _DeepSearchState:
    captured: dict[str, DeepSearchSupplementOutcome] = field(default_factory=dict)
    submitted: set[str] = field(default_factory=set)


class DeepSearchAgent:
    """Execute only policy-issued tasks through member-scoped acquisition Tools."""

    agent_id = "deepsearch-agent"
    role = "supplemental-evidence"
    skill_ids = ("tianyancha-annual-report-social-security",)

    def __init__(
        self,
        *,
        prompt_bundle: PromptBundle,
        annual_report_tool: InvokableTool | None = None,
        bounded_web_search_tool: InvokableTool | None = None,
        annual_report_lookback_years: int = 5,
        annual_report_timeout_seconds: float = 15,
    ) -> None:
        self._prompt_bundle = prompt_bundle
        self._annual_report_tool = annual_report_tool
        self._bounded_web_search_tool = bounded_web_search_tool
        self._annual_report_lookback_years = annual_report_lookback_years
        self._annual_report_timeout_seconds = annual_report_timeout_seconds

    async def run(
        self,
        *,
        tasks: tuple[SupplementTask, ...],
        subject: ResolvedSubject,
        queried_at: datetime,
        runtime: AgentExecutionRuntime,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        timeout_seconds: float,
        model_temperature: float = 0,
        max_iterations: int = 8,
        event_sink: Callable[[AgentExecutionEvent], Awaitable[None]] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DeepSearchAgentRun:
        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        invocation = self._prompt_invocation(
            tasks=tasks,
            subject=subject,
            queried_at=queried_at,
            run_id=run_id,
        )
        check_cancellation(cancellation_token)
        react_agent, state = self.build_react_agent(
            tasks=tasks,
            subject=subject,
            queried_at=queried_at,
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
        request = AgentExecutionRequest(
            run_id=run_id,
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.ACQUISITION,
            session_id=f"{run_id}:{self.agent_id}",
            query=invocation.user_payload_json,
            task_ids=tuple(item.task_id for item in tasks),
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
        expected = {item.task_id for item in tasks}
        if state.submitted != expected:
            raise AgentExecutionError(
                "DeepSearch Agent ended without submitting every SupplementTask",
                details={"missing_task_ids": sorted(expected - state.submitted)},
            )
        outcomes = tuple(state.captured[item.task_id] for item in tasks)
        evidence = self._unique_evidence(
            tuple(item for outcome in outcomes for item in outcome.evidence)
        )
        facts = tuple(
            FactEvidenceRef(
                evidence_id=item.evidence_id,
                fact_path="supplemental_evidence",
                summary=f"{item.source_tool or 'deepsearch'} 补充事实",
            )
            for item in evidence
        )
        contribution = AgentInvestigationResult(
            agent_id=self.agent_id,
            role=self.role,
            phase=AgentResultPhase.ACQUISITION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(item.task_id for item in tasks),
            fact_evidence_refs=facts,
            prompt_version=invocation.prompt_version,
        )
        return DeepSearchAgentRun(
            outcomes=outcomes,
            agent_result=contribution,
            events=events,
        )

    def build_react_agent(
        self,
        *,
        tasks: tuple[SupplementTask, ...],
        subject: ResolvedSubject,
        queried_at: datetime,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        model_temperature: float = 0,
        model_timeout_seconds: float = 300,
        max_iterations: int = 8,
    ) -> tuple[ReActAgent, _DeepSearchState]:
        self._validate_tasks(tasks, subject=subject, queried_at=queried_at)
        if max_iterations < len(tasks) * 2:
            raise ValueError("DeepSearch Agent iteration budget cannot submit all tasks")
        gap_tasks = tuple(
            item for item in tasks if item.reason is SupplementTaskReason.EVIDENCE_GAP
        )
        if gap_tasks and self._bounded_web_search_tool is None:
            raise AgentExecutionError(
                "formal DeepSearch Agent requires a bounded_web_search Tool for gap tasks"
            )
        invocation = self._prompt_invocation(
            tasks=tasks,
            subject=subject,
            queried_at=queried_at,
            run_id=run_id,
        )
        state = _DeepSearchState()
        task_by_id = {item.task_id: item for item in tasks}
        baseline_tasks = tuple(
            item for item in tasks if item.reason is SupplementTaskReason.BASELINE_ENRICHMENT
        )
        annual_tool = self._annual_report_tool
        if baseline_tasks and annual_tool is None:
            annual_tool = self._build_default_annual_report_tool()

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{run_id}.deepsearch.annual-report",
                name=TIANYANCHA_ANNUAL_REPORT_TOOL_NAME,
                description=(
                    "Fetch the bounded annual-report social-security fact group for the "
                    "assigned subject and cutoff."
                ),
                input_params=AnnualReportToolInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def annual_report(
            subject: dict[str, object] | str,
            queried_at: datetime,
            report_as_of: date,
        ) -> dict[str, object]:
            if isinstance(subject, str):
                try:
                    decoded_subject = json.loads(subject)
                except (TypeError, json.JSONDecodeError) as error:
                    raise AgentExecutionError(
                        "annual-report Tool subject must be a JSON object"
                    ) from error
                if not isinstance(decoded_subject, dict):
                    raise AgentExecutionError("annual-report Tool subject must be a JSON object")
                subject = decoded_subject
            request = AnnualReportToolInput.model_validate(
                {
                    "subject": subject,
                    "queried_at": queried_at,
                    "report_as_of": report_as_of,
                }
            )
            if not baseline_tasks or annual_tool is None:
                raise AgentExecutionError("annual-report Tool is not authorized for this run")
            assigned = baseline_tasks[0]
            if (
                request.subject != subject_value
                or request.report_as_of != assigned.report_as_of
                or request.queried_at != queried_at_value
            ):
                raise AgentExecutionError("annual-report Tool input is outside SupplementTask")
            raw = await annual_tool.invoke(
                {
                    "subject": request.subject.model_dump(mode="json"),
                    "queried_at": request.queried_at.isoformat(),
                    "report_as_of": request.report_as_of.isoformat(),
                }
            )
            outcome = AnnualReportSocialSecurityOutcome.model_validate(raw)
            state.captured[assigned.task_id] = DeepSearchSupplementOutcome(
                task_id=assigned.task_id,
                target_submodule_id=assigned.target_submodule_id,
                source_status=outcome.source_status,
                evidence=outcome.evidence,
                scope={"checked_years": list(outcome.checked_years)},
                unresolved=outcome.source_status is not SourceStatus.VERIFIED_RECORDS,
                conflicts_with=assigned.conflict_evidence_ids,
            )
            return outcome.model_dump(mode="json")

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{run_id}.deepsearch.bounded-search",
                name="bounded_web_search",
                description="Execute one exact policy-issued bounded evidence-gap task.",
                input_params=RunBoundedSearchInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def bounded_web_search(task_id: str) -> dict[str, object]:
            task = task_by_id.get(task_id)
            if task is None or task.reason is not SupplementTaskReason.EVIDENCE_GAP:
                raise AgentExecutionError("bounded_web_search task is not authorized")
            if self._bounded_web_search_tool is None:
                raise AgentExecutionError("bounded_web_search Tool is not configured")
            raw = await self._bounded_web_search_tool.invoke(
                {
                    "task": task.model_dump(mode="json"),
                    "subject": subject_value.model_dump(mode="json"),
                    "queried_at": queried_at_value.isoformat(),
                }
            )
            outcome = DeepSearchSupplementOutcome.model_validate(raw)
            if outcome.task_id != task.task_id:
                raise AgentExecutionError("bounded search returned another task outcome")
            self._validate_outcome(outcome, task=task, subject=subject_value)
            state.captured[task.task_id] = outcome
            return outcome.model_dump(mode="json")

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{run_id}.deepsearch.submit",
                name="submit_supplement_outcome",
                description="Submit one captured, typed SupplementTask outcome.",
                input_params=SubmitSupplementOutcomeInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_supplement_outcome(task_id: str) -> dict[str, object]:
            task = task_by_id.get(task_id)
            outcome = state.captured.get(task_id)
            if task is None or outcome is None:
                raise AgentExecutionError("cannot submit an unexecuted SupplementTask")
            self._validate_outcome(outcome, task=task, subject=subject_value)
            state.submitted.add(task_id)
            return {"status": "accepted", "task_id": task_id}

        subject_value = subject
        queried_at_value = queried_at
        react_agent = ReActAgent(
            AgentCard(id=self.agent_id, name="DeepSearch Evidence Agent")
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
        abilities: list[Any] = []
        if baseline_tasks:
            abilities.append(annual_report)
        if gap_tasks:
            abilities.append(bounded_web_search)
        abilities.append(submit_supplement_outcome)
        registrations = [
            react_agent.ability_manager.add_ability(item.card, item) for item in abilities
        ]
        if not all(item.added for item in registrations):
            react_agent.ability_manager.teardown_tools()
            raise AgentExecutionError("DeepSearch Agent tool registration failed")
        return react_agent, state

    def _prompt_invocation(
        self,
        *,
        tasks: tuple[SupplementTask, ...],
        subject: ResolvedSubject,
        queried_at: datetime,
        run_id: str,
    ) -> PromptInvocation:
        return self._prompt_bundle.build_invocation(
            phase=AgentResultPhase.ACQUISITION,
            role=self.role,
            runtime_data={
                "run_id": run_id,
                "subject": subject.model_dump(mode="json"),
                "queried_at": queried_at.isoformat(),
                "tasks": [item.model_dump(mode="json") for item in tasks],
            },
        )

    def _build_default_annual_report_tool(self) -> InvokableTool:
        register_deepsearch_tool_providers()
        built = BuiltinToolSpec(
            type=TIANYANCHA_ANNUAL_REPORT_TOOL_TYPE,
            params={
                "max_lookback_years": self._annual_report_lookback_years,
                "timeout_seconds": self._annual_report_timeout_seconds,
            },
        ).build(language="zh")
        return cast(InvokableTool, built)

    @staticmethod
    def _validate_tasks(
        tasks: tuple[SupplementTask, ...],
        *,
        subject: ResolvedSubject,
        queried_at: datetime,
    ) -> None:
        if not tasks:
            raise AgentExecutionError("DeepSearch Agent requires explicit SupplementTask input")
        task_ids = tuple(item.task_id for item in tasks)
        if len(task_ids) != len(set(task_ids)):
            raise AgentExecutionError("DeepSearch SupplementTask ids must be unique")
        if sum(item.reason is SupplementTaskReason.BASELINE_ENRICHMENT for item in tasks) > 1:
            raise AgentExecutionError("DeepSearch allows at most one baseline task per run")
        for task in tasks:
            if task.subject_id != subject.subject_id:
                raise AgentExecutionError("DeepSearch SupplementTask subject mismatch")
            if task.report_as_of > queried_at.date():
                raise AgentExecutionError("DeepSearch SupplementTask cutoff is in the future")
            if task.allowed_sources != (SourceType.PUBLIC_WEB,):
                raise AgentExecutionError("DeepSearch SupplementTask source is not allowed")
            if (
                task.reason is SupplementTaskReason.EVIDENCE_GAP
                and task.trigger_status is SourceStatus.VERIFIED_EMPTY
            ):
                raise AgentExecutionError("DeepSearch cannot overwrite verified_empty")

    @staticmethod
    def _validate_outcome(
        outcome: DeepSearchSupplementOutcome,
        *,
        task: SupplementTask,
        subject: ResolvedSubject,
    ) -> None:
        if outcome.task_id != task.task_id:
            raise AgentExecutionError("DeepSearch outcome task mismatch")
        if outcome.target_submodule_id != task.target_submodule_id:
            raise AgentExecutionError("DeepSearch outcome submodule mismatch")
        if any(
            item.subject_id != subject.subject_id or item.source_type is not SourceType.PUBLIC_WEB
            for item in outcome.evidence
        ):
            raise AgentExecutionError("DeepSearch outcome Evidence is outside task boundary")

    @staticmethod
    def _unique_evidence(items: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
        by_id: dict[str, Evidence] = {}
        for item in items:
            by_id.setdefault(item.evidence_id, item)
        return tuple(by_id.values())


__all__ = [
    "DeepSearchAgent",
    "DeepSearchAgentRun",
    "InvokableTool",
    "RunBoundedSearchInput",
    "SubmitSupplementOutcomeInput",
]
