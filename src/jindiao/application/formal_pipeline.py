"""Formal acquisition, freezing, investigation, and adjudication input pipeline."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import JsonValue

from jindiao.acquisition import ContextFreezer
from jindiao.agents import (
    AgentTeamsInvestigatorTeam,
    DeepSearchAgent,
    DeepSearchSupplementOutcome,
    EnterpriseContextAgent,
    SingleInvestigatorAgent,
)
from jindiao.contracts.acquisition import (
    EnterpriseContextSnapshot,
    SubmoduleAvailability,
)
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    CoverageItem,
    CoverageSummary,
    SourceStatus,
)
from jindiao.contracts.execution import (
    ComparisonFingerprint,
    ExecutionCost,
    InvestigationBudgetFingerprint,
)
from jindiao.contracts.investigation import ReviewIssue
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentStatus,
    AgentTrace,
    CollaborationSummary,
    ComparisonMetadata,
    OrchestrationMode,
)
from jindiao.deepsearch import SupplementPolicy
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration.agent_runtime import (
    AgentExecutionEvent,
    AgentExecutionEventType,
    AgentExecutionRuntime,
)
from jindiao.orchestration.base import (
    BudgetLedger,
    CancellationToken,
    OrchestrationOutcome,
    RunBudget,
    RuntimeEventSink,
    TeamRuntimeEvent,
    check_cancellation,
)
from jindiao.prompts import PromptBundle, load_prompt_bundle
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.tianyancha import TianyanchaMcpGateway

from .context import RunContext
from .errors import AgentExecutionError

_MODULE_DOMAINS = {
    "company-profile": "governance",
    "judicial-risk": "judicial",
    "operational-risk": "operations",
    "operations-analysis": "operations",
    "related-parties": "relationships",
    "peer-analysis": "peers",
}


@dataclass(frozen=True, slots=True)
class FormalAcquisitionRun:
    """One shared, immutable acquisition result suitable for one or more arms."""

    snapshot: EnterpriseContextSnapshot
    events: tuple[TeamRuntimeEvent, ...]


@dataclass(frozen=True, slots=True)
class FormalPipelineRun:
    """Artifacts needed by the shared deterministic result assembler."""

    snapshot: EnterpriseContextSnapshot
    agent_results: tuple[AgentInvestigationResult, ...]
    outcome: OrchestrationOutcome
    investigation_cost: ExecutionCost
    comparison_metadata: ComparisonMetadata


class FormalDueDiligencePipeline:
    """Run real acquisition Agents before exactly one selected investigation topology."""

    def __init__(
        self,
        *,
        context_agent: EnterpriseContextAgent,
        supplement_policy: SupplementPolicy,
        deepsearch_agent: DeepSearchAgent | None,
        context_freezer: ContextFreezer,
        single_investigator: SingleInvestigatorAgent,
        multi_investigator: AgentTeamsInvestigatorTeam,
        agent_runtime: AgentExecutionRuntime,
        gateway: TianyanchaMcpGateway,
        model_name: str,
        model_provider: str,
        model_api_key: str,
        model_base_url: str,
        model_parameters: dict[str, JsonValue] | None = None,
        random_seed: int | None = None,
        code_version: str = "jindiao-formal-pipeline-v1",
        prompt_bundle: PromptBundle | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._context_agent = context_agent
        self._supplement_policy = supplement_policy
        self._deepsearch_agent = deepsearch_agent
        self._context_freezer = context_freezer
        self._single = single_investigator
        self._multi = multi_investigator
        self._agent_runtime = agent_runtime
        self._gateway = gateway
        self._model_name = model_name
        self._model_provider = model_provider
        self._model_api_key = model_api_key
        self._model_base_url = model_base_url
        self._model_parameters = dict(model_parameters or {})
        raw_temperature = self._model_parameters.get("temperature", 0)
        if (
            isinstance(raw_temperature, bool)
            or not isinstance(raw_temperature, int | float)
            or not 0 <= raw_temperature <= 2
        ):
            raise ValueError("model temperature must be a number between 0 and 2")
        self._model_temperature = float(raw_temperature)
        self._random_seed = random_seed
        self._code_version = code_version
        self._prompts = prompt_bundle or load_prompt_bundle()
        self._clock = clock

    async def execute(
        self,
        context: RunContext,
        *,
        mode: OrchestrationMode,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> FormalPipelineRun:
        """Acquire and freeze once, then invoke only the requested investigation arm."""

        check_cancellation(cancellation_token)
        acquisition = await self.acquire(
            context,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
        )
        investigation = await self.investigate(
            context,
            snapshot=acquisition.snapshot,
            mode=mode,
            budget=budget,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
        )
        return FormalPipelineRun(
            snapshot=investigation.snapshot,
            agent_results=investigation.agent_results,
            outcome=investigation.outcome.model_copy(
                update={
                    "runtime_events": (
                        *acquisition.events,
                        *investigation.outcome.runtime_events,
                    )
                }
            ),
            investigation_cost=investigation.investigation_cost,
            comparison_metadata=investigation.comparison_metadata,
        )

    async def acquire(
        self,
        context: RunContext,
        *,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> FormalAcquisitionRun:
        """Run shared Context/DeepSearch acquisition and close all external sources."""

        if context.requested_enterprise is None:
            raise AgentExecutionError(
                "formal Agent pipeline requires the requested enterprise input"
            )
        acquisition_started = self._clock()
        runtime_events: list[TeamRuntimeEvent] = []

        async def publish(event: TeamRuntimeEvent) -> None:
            runtime_events.append(event)
            if event_sink is not None:
                await event_sink(event)

        try:
            check_cancellation(cancellation_token)
            await self._publish(
                publish,
                "acquisition.started",
                payload={"status": "running"},
            )

            async def publish_context_event(event: AgentExecutionEvent) -> None:
                await self._publish_agent_events(publish, (event,))

            context_kwargs = self._optional_event_sink(
                self._context_agent.run, publish_context_event
            )
            context_kwargs.update(
                self._optional_cancellation_token(self._context_agent.run, cancellation_token)
            )
            context_run = await self._context_agent.run(
                context.requested_enterprise,
                runtime=self._agent_runtime,
                model_name=self._model_name,
                model_provider=self._model_provider,
                model_api_key=self._model_api_key,
                model_base_url=self._model_base_url,
                timeout_seconds=context.policy.request_timeout_seconds,
                model_temperature=self._model_temperature,
                **context_kwargs,
            )

            tasks = self._supplement_policy.plan(
                subject=context_run.acquisition.subject,
                report_as_of=context_run.acquisition.report_as_of,
                submodules=context_run.acquisition.submodules,
            )
            supplement_outcomes: tuple[DeepSearchSupplementOutcome, ...] = ()
            deepsearch_result: AgentInvestigationResult | None = None
            acquisition_events = tuple(context_run.events)
            if tasks:
                check_cancellation(cancellation_token)
                if self._deepsearch_agent is None:
                    raise AgentExecutionError(
                        "SupplementPolicy produced tasks without a DeepSearch Agent",
                        details={"task_ids": [item.task_id for item in tasks]},
                    )
                deepsearch_kwargs = self._optional_event_sink(
                    self._deepsearch_agent.run, publish_context_event
                )
                deepsearch_kwargs.update(
                    self._optional_cancellation_token(
                        self._deepsearch_agent.run,
                        cancellation_token,
                    )
                )
                deepsearch_run = await self._deepsearch_agent.run(
                    tasks=tasks,
                    subject=context_run.acquisition.subject,
                    queried_at=self._clock(),
                    runtime=self._agent_runtime,
                    run_id=context.run_id,
                    model_name=self._model_name,
                    model_provider=self._model_provider,
                    model_api_key=self._model_api_key,
                    model_base_url=self._model_base_url,
                    timeout_seconds=context.policy.request_timeout_seconds,
                    model_temperature=self._model_temperature,
                    max_iterations=max(2, len(tasks) * 2 + 1),
                    **deepsearch_kwargs,
                )
                supplement_outcomes = deepsearch_run.outcomes
                deepsearch_result = deepsearch_run.agent_result
                acquisition_events = (*acquisition_events, *deepsearch_run.events)

            acquisition_cost = self._execution_cost_from_events(
                acquisition_events,
                mcp_calls=self._gateway.mcp_calls,
                wall_time_ms=self._elapsed_ms(acquisition_started, self._clock()),
            )
            snapshot = self._context_freezer.freeze(
                context_run.acquisition,
                supplement_tasks=tasks,
                supplement_outcomes=supplement_outcomes,
                deepsearch_agent_result=deepsearch_result,
                shared_acquisition_cost=acquisition_cost,
            )
            await self._publish(
                publish,
                "acquisition.completed",
                payload={
                    "status": "completed",
                    "evidence_count": len(snapshot.evidence),
                    "mcp_calls": acquisition_cost.mcp_calls,
                },
            )
            await self._publish(
                publish,
                "snapshot.frozen",
                payload={
                    "snapshot": {
                        "snapshot_id": snapshot.snapshot_id,
                        "snapshot_sha256": snapshot.snapshot_sha256,
                        "submodule_count": len(snapshot.submodules),
                    }
                },
            )
            return FormalAcquisitionRun(
                snapshot=snapshot,
                events=tuple(runtime_events),
            )
        finally:
            await self._gateway.aclose()

    async def investigate(
        self,
        context: RunContext,
        *,
        snapshot: EnterpriseContextSnapshot,
        mode: OrchestrationMode,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> FormalPipelineRun:
        """Execute one isolated arm over an already frozen snapshot."""

        runtime_events: list[TeamRuntimeEvent] = []

        async def publish(event: TeamRuntimeEvent) -> None:
            runtime_events.append(event)
            if event_sink is not None:
                await event_sink(event)

        async def publish_context_event(event: AgentExecutionEvent) -> None:
            await self._publish_agent_events(publish, (event,))

        ledger = BudgetLedger(budget)
        check_cancellation(cancellation_token)
        investigation_agent_results: tuple[AgentInvestigationResult, ...]
        review_issues: tuple[ReviewIssue, ...]
        repairs_requested: int
        repairs_completed: int
        if mode is OrchestrationMode.SINGLE:
            single_kwargs = self._optional_event_sink(self._single.run, publish_context_event)
            single_kwargs.update(
                self._optional_cancellation_token(self._single.run, cancellation_token)
            )
            single_run = await self._single.run(
                snapshot=snapshot,
                runtime=self._agent_runtime,
                budget_ledger=ledger,
                run_id=context.run_id,
                model_name=self._model_name,
                model_provider=self._model_provider,
                model_api_key=self._model_api_key,
                model_base_url=self._model_base_url,
                timeout_seconds=context.policy.request_timeout_seconds,
                model_temperature=self._model_temperature,
                **single_kwargs,
            )
            investigation_agent_results = (single_run.agent_result,)
            investigation_cost = single_run.investigation_cost
            review_issues = ()
            repairs_requested = 0
            repairs_completed = 0
        else:
            multi_kwargs = self._optional_event_sink(self._multi.run, publish)
            multi_kwargs.update(
                self._optional_cancellation_token(self._multi.run, cancellation_token)
            )
            multi_run = await self._multi.run(
                snapshot=snapshot,
                budget_ledger=ledger,
                run_id=context.run_id,
                model_name=self._model_name,
                model_provider=self._model_provider,
                model_api_key=self._model_api_key,
                model_base_url=self._model_base_url,
                timeout_seconds=context.policy.request_timeout_seconds,
                model_temperature=self._model_temperature,
                **multi_kwargs,
            )
            investigation_agent_results = multi_run.agent_results
            investigation_cost = multi_run.investigation_cost
            review_issues = tuple(issue for review in multi_run.reviews for issue in review.issues)
            repairs_requested = sum([len(review.repair_tasks) for review in multi_run.reviews])
            repairs_completed = max(
                0,
                repairs_requested - len(multi_run.reviews[-1].repair_tasks),
            )
        # Agent runtimes stream directly into ``publish``.  The tuple remains in
        # the return value for deterministic replay/benchmark compatibility.

        outcome = OrchestrationOutcome(
            subject=snapshot.subject,
            findings=(),
            evidence=snapshot.evidence,
            coverage=self._coverage(snapshot),
            review_issues=review_issues,
            review_completed=True,
            section_data={},
            agent_trace=self._agent_traces(investigation_agent_results),
            collaboration=CollaborationSummary(
                agent_count=(
                    1 if mode is OrchestrationMode.SINGLE else len(investigation_agent_results)
                ),
                task_count=len(CHECK_CATALOG.check_ids),
                parallel_task_count=(
                    0
                    if mode is OrchestrationMode.SINGLE
                    else len(
                        {item.role for item in investigation_agent_results if item.check_results}
                    )
                ),
                conflicts_detected=len(snapshot.unresolved_conflicts),
                repairs_requested=repairs_requested,
                repairs_completed=repairs_completed,
            ),
            errors=(),
            tool_calls=investigation_cost.tool_calls,
            runtime_events=tuple(runtime_events),
        )
        return FormalPipelineRun(
            snapshot=snapshot,
            agent_results=investigation_agent_results,
            outcome=outcome,
            investigation_cost=investigation_cost,
            comparison_metadata=self._comparison_metadata(context, mode),
        )

    def comparison_fingerprint(
        self,
        context: RunContext,
        *,
        snapshot: EnterpriseContextSnapshot,
        budget: RunBudget,
    ) -> ComparisonFingerprint:
        """Describe every mode-independent input before either paired arm starts."""

        common = self._prompts.investigation("single-investigator")
        return ComparisonFingerprint(
            schema_version=1,
            code_version=self._code_version,
            contract_schema_version="due-diligence-result-v1",
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            report_as_of=snapshot.report_as_of,
            model_provider=self._model_provider,
            model_name=self._model_name,
            model_parameters=self._model_parameters,
            random_seed=self._random_seed,
            common_prompt_sha256=common.core_sha256,
            check_catalog_sha256=self._model_sha256(CHECK_CATALOG.model_dump(mode="json")),
            report_catalog_sha256=self._model_sha256(REPORT_CATALOG.model_dump(mode="json")),
            investigation_budget=InvestigationBudgetFingerprint(
                max_llm_requests=budget.max_llm_requests,
                max_input_tokens=budget.max_input_tokens,
                max_output_tokens=budget.max_output_tokens,
                max_total_tokens=budget.max_total_tokens,
                max_wall_time_ms=budget.timeout_seconds * 1000,
                max_concurrency=budget.max_concurrency,
                max_schema_retries=budget.max_schema_retries,
                max_repair_rounds=budget.max_repair_rounds,
            ),
            rule_version=context.rule_version,
            evaluator_version="quality-calculator-v1",
            reporting_policy_sha256=context.reporting_policy.policy_sha256,
            report_renderer_version="report-renderer-v1.1",
            gap_mapping_version="gap-mapping-v1",
        )

    async def _publish_agent_events(
        self,
        publish: Callable[[TeamRuntimeEvent], Any],
        events: tuple[AgentExecutionEvent, ...],
    ) -> None:
        for event in events:
            await publish(
                TeamRuntimeEvent(
                    event_type=event.event_type.value,
                    member_name=event.agent_id,
                    payload={
                        "role": event.role,
                        "phase": event.phase.value,
                        "task_id": event.task_id,
                        "check_id": event.check_id,
                        "tool_id": event.tool_id,
                        "evidence_ids": list(event.evidence_ids),
                        **event.payload,
                    },
                )
            )

    async def _publish_investigation_events(
        self,
        publish: Callable[[TeamRuntimeEvent], Any],
        events: tuple[object, ...],
    ) -> None:
        for event in events:
            if isinstance(event, TeamRuntimeEvent):
                await publish(event)
            elif isinstance(event, AgentExecutionEvent):
                await self._publish_agent_events(publish, (event,))

    @staticmethod
    async def _publish(
        publish: Callable[[TeamRuntimeEvent], Any],
        event_type: str,
        *,
        payload: dict[str, JsonValue],
    ) -> None:
        await publish(TeamRuntimeEvent(event_type=event_type, payload=payload))

    @staticmethod
    def _optional_event_sink(method: object, sink: object) -> dict[str, Any]:
        """Pass the realtime callback only to implementations that support it."""

        try:
            parameters = inspect.signature(cast(Callable[..., Any], method)).parameters
        except (TypeError, ValueError):
            return {}
        return {"event_sink": sink} if "event_sink" in parameters else {}

    @staticmethod
    def _optional_cancellation_token(
        method: object,
        token: CancellationToken | None,
    ) -> dict[str, Any]:
        if token is None:
            return {}
        try:
            parameters = inspect.signature(cast(Callable[..., Any], method)).parameters
        except (TypeError, ValueError):
            return {}
        return {"cancellation_token": token} if "cancellation_token" in parameters else {}

    @staticmethod
    def _coverage(snapshot: EnterpriseContextSnapshot) -> CoverageSummary:
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        items: list[CoverageItem] = []
        for context in snapshot.submodules:
            module_id = REPORT_CATALOG.module_for_submodule(context.submodule_id).module_id
            evidence_ids = (*context.evidence_ids, *context.supplemental_evidence_ids)
            if context.availability is SubmoduleAvailability.AVAILABLE:
                status = next(
                    (
                        evidence_by_id[evidence_id].source_status
                        for evidence_id in evidence_ids
                        if evidence_id in evidence_by_id
                    ),
                    SourceStatus.VERIFIED_RECORDS,
                )
                record_count = max(1, len(context.evidence_ids))
            elif context.availability is SubmoduleAvailability.VERIFIED_EMPTY:
                status = SourceStatus.VERIFIED_EMPTY
                record_count = 0
            elif context.availability is SubmoduleAvailability.SOURCE_ERROR:
                status = SourceStatus.SOURCE_ERROR
                record_count = 0
            else:
                status = SourceStatus.CAPABILITY_ABSENT
                record_count = 0
            items.append(
                CoverageItem(
                    domain=_MODULE_DOMAINS[module_id],
                    capability=context.submodule_id,
                    status=status,
                    record_count=record_count,
                    error=(
                        "not_requested"
                        if context.availability is SubmoduleAvailability.NOT_REQUESTED
                        else None
                    ),
                    completeness=context.completeness,
                    gap_reasons=(
                        (CoverageGapReason.MISSING_FIELDS,)
                        if context.completeness is CoverageCompleteness.PARTIAL
                        else ()
                    ),
                )
            )
        return CoverageSummary.from_items(items)

    def _agent_traces(
        self,
        results: tuple[AgentInvestigationResult, ...],
    ) -> tuple[AgentTrace, ...]:
        completed_at = self._clock()
        return tuple(
            AgentTrace(
                agent_id=result.agent_id,
                role=result.role,
                task_ids=result.task_ids,
                status=result.status,
                started_at=completed_at,
                completed_at=(completed_at if result.status is AgentStatus.COMPLETED else None),
                duration_ms=0 if result.status is AgentStatus.COMPLETED else None,
                evidence_count=len(result.fact_evidence_refs),
                error_codes=(),
            )
            for result in results
        )

    def _comparison_metadata(
        self,
        context: RunContext,
        mode: OrchestrationMode,
    ) -> ComparisonMetadata:
        core = self._prompts.investigation("single-investigator")
        roles = (
            ("single-investigator",)
            if mode is OrchestrationMode.SINGLE
            else (
                "leader",
                "corporate",
                "judicial-compliance",
                "financial-operations",
                "related-peer",
                "reviewer",
            )
        )
        return ComparisonMetadata(
            formal_agent_run=True,
            topology=mode,
            prompt_core_version=core.core_version,
            prompt_core_sha256=core.core_sha256,
            role_prompt_versions={
                role: self._prompts.investigation(role).role_version for role in roles
            },
            check_catalog_version=CHECK_CATALOG.catalog_version,
            check_catalog_sha256=self._model_sha256(CHECK_CATALOG.model_dump(mode="json")),
            rule_version=context.rule_version,
            evaluator_version="quality-calculator-v1",
        )

    @staticmethod
    def _model_sha256(value: object) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def _execution_cost_from_events(
        cls,
        events: tuple[AgentExecutionEvent, ...],
        *,
        mcp_calls: int,
        wall_time_ms: int,
    ) -> ExecutionCost:
        completed = tuple(
            event
            for event in events
            if event.event_type is AgentExecutionEventType.MODEL_REQUEST_COMPLETED
        )
        input_tokens = sum(cls._usage_value(event.payload, "input_tokens") for event in completed)
        output_tokens = sum(cls._usage_value(event.payload, "output_tokens") for event in completed)
        tool_calls = sum(
            event.event_type is AgentExecutionEventType.TOOL_CALL_STARTED for event in events
        )
        return ExecutionCost(
            llm_requests=len(completed),
            successful_llm_requests=len(completed),
            provider_usage_requests=sum(
                cls._usage_value(event.payload, "total_tokens") > 0
                or cls._usage_value(event.payload, "input_tokens") > 0
                or cls._usage_value(event.payload, "output_tokens") > 0
                for event in completed
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            tool_calls=tool_calls,
            mcp_calls=mcp_calls,
            schema_retries=0,
            repair_rounds=0,
            wall_time_ms=wall_time_ms,
        )

    @classmethod
    def _usage_value(cls, value: object, key: str) -> int:
        if isinstance(value, Mapping):
            direct = value.get(key)
            if isinstance(direct, int) and not isinstance(direct, bool):
                return max(0, direct)
            return max((cls._usage_value(item, key) for item in value.values()), default=0)
        if isinstance(value, list | tuple):
            return max((cls._usage_value(item, key) for item in value), default=0)
        return 0

    @staticmethod
    def _elapsed_ms(started_at: datetime, completed_at: datetime) -> int:
        return max(0, round((completed_at - started_at).total_seconds() * 1000))


__all__ = [
    "FormalAcquisitionRun",
    "FormalDueDiligencePipeline",
    "FormalPipelineRun",
]
