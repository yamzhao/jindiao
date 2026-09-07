"""Top-level due-diligence use case shared by JSON, SSE and benchmarks."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from jindiao.acquisition import ContextFreezer
from jindiao.agents import (
    AgentTeamsInvestigatorTeam,
    DeepSearchAgent,
    DeepSearchEvidenceAgent,
    EnterpriseContextAgent,
    SingleInvestigatorAgent,
)
from jindiao.contracts.events import EventSequencer, EventType, RunEvent
from jindiao.contracts.report_policy import ReportingPolicyBinding
from jindiao.contracts.reporting import ReportViewModel
from jindiao.contracts.results import (
    DueDiligenceRequest,
    DueDiligenceResult,
    OrchestrationMode,
    SkillEvolutionStatus,
    SkillEvolutionSummary,
)
from jindiao.deepsearch import SupplementPolicy, TianyanchaAnnualReportProvider
from jindiao.observability import JsonlRunTrace, RunArtifactStore, RunMetricsCollector
from jindiao.orchestration import (
    AsyncClosableToolset,
    CancellationToken,
    OpenJiuwenAgentExecutionRuntime,
    RunBudget,
    SingleAgentStrategy,
)
from jindiao.orchestration.base import (
    InvestigationToolset,
    OrchestrationOutcome,
    OrchestrationStrategy,
    RuntimeEventSink,
    TeamRuntimeEvent,
)
from jindiao.orchestration.multi import MultiAgentStrategy
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.orchestration.team_runtime import (
    OfflineTeamRuntime,
    OpenJiuwenTeamRuntime,
    TeamRuntimeDriver,
)
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.orchestration.tianyancha_toolset import TianyanchaHybridToolset
from jindiao.prompts import load_prompt_bundle
from jindiao.reporting.catalog import REPORT_CATALOG
from jindiao.reporting.demo_store import ReportingDemoStore, bootstrap
from jindiao.reporting.replay import ReplaySnapshot
from jindiao.risk import RiskRuleEngine, RiskRuleSet
from jindiao.scenarios import ScenarioRepository
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    GatewayBudget,
    StreamableHttpMcpTransport,
    TianyanchaMcpClient,
    TianyanchaMcpGateway,
)

from .context import RunContext
from .errors import error_to_record
from .formal_pipeline import FormalDueDiligencePipeline, FormalPipelineRun
from .result_assembler import ResultAssembler
from .settings import Settings

if TYPE_CHECKING:
    from .run_coordinator import RunCoordinator


@dataclass(frozen=True, slots=True)
class DetailedRun:
    result: DueDiligenceResult
    outcome: OrchestrationOutcome


class DueDiligenceService:
    """Create one frozen context and execute the selected shared strategy."""

    def __init__(
        self,
        *,
        settings: Settings,
        scenarios: ScenarioRepository,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        team_runtime: TeamRuntimeDriver | None = None,
        risk_rules_path: Path = Path("config/risk-rules-v1.json"),
        artifact_store: RunArtifactStore | None = None,
        toolset_factory: Callable[[], InvestigationToolset] | None = None,
        formal_pipeline_factory: (Callable[[RunContext], FormalDueDiligencePipeline] | None) = None,
    ) -> None:
        self._settings = settings
        self._scenarios = scenarios
        self._id_factory = id_factory
        self._clock = clock
        self._team_runtime = team_runtime or (
            OpenJiuwenTeamRuntime() if settings.formal_agent_run else OfflineTeamRuntime()
        )
        self._artifact_store = artifact_store or RunArtifactStore(settings.artifact_root)
        self._toolset_factory = toolset_factory
        self._formal_pipeline_factory = formal_pipeline_factory
        self.reporting_demo_store = (
            ReportingDemoStore(settings.artifact_root / "reporting-demo")
            if settings.reporting_demo_enabled
            else None
        )
        self._result_assembler = ResultAssembler(
            rule_engine=RiskRuleEngine(RiskRuleSet.from_file(risk_rules_path))
        )
        self._coordinator: RunCoordinator | None = None

    def attach_coordinator(self, coordinator: RunCoordinator) -> None:
        """Attach the lifecycle facade used by HTTP compatibility adapters.

        The service remains directly usable by benchmarks and deterministic tests;
        once attached, public ``run``/``stream`` calls are routed through the same
        RunCoordinator used by the versioned API.
        """

        self._coordinator = coordinator

    def freeze_reporting_policy(self) -> ReportingPolicyBinding:
        return self.reporting_demo_store.active() if self.reporting_demo_store else bootstrap()

    def load_report_replay(self, run_id: str) -> ReplaySnapshot | None:
        return self._artifact_store.load_replay(run_id)

    @property
    def team_runtime(self) -> TeamRuntimeDriver:
        """Expose the selected runtime for diagnostics and reproducibility metadata."""

        return self._team_runtime

    @property
    def settings(self) -> Settings:
        """Expose immutable runtime configuration to protocol adapters."""

        return self._settings

    async def run(
        self,
        request: DueDiligenceRequest,
        *,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
        request_id: str | None = None,
        run_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> DueDiligenceResult:
        if self._coordinator is not None:
            return await self._coordinator.execute_compat(
                request,
                mode=mode,
                request_id=request_id,
                run_id=run_id,
                event_sink=event_sink,
                cancellation_token=cancellation_token,
            )
        return await self._run_impl(
            request,
            mode=mode,
            request_id=request_id,
            run_id=run_id,
            event_sink=event_sink,
            cancellation_token=cancellation_token,
        )

    async def _run_impl(
        self,
        request: DueDiligenceRequest,
        *,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
        request_id: str | None = None,
        run_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
        reporting_policy: ReportingPolicyBinding | None = None,
    ) -> DueDiligenceResult:
        if cancellation_token is not None and getattr(cancellation_token, "cancelled", False):
            raise asyncio.CancelledError()
        detailed = await self._execute(
            request,
            mode=mode,
            request_id=request_id or self._id_factory(),
            run_id=run_id or self._id_factory(),
            event_sink=event_sink,
            cancellation_token=cancellation_token,
            reporting_policy=reporting_policy or self.freeze_reporting_policy(),
        )
        return detailed.result

    async def stream(
        self,
        request: DueDiligenceRequest,
        *,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
    ) -> AsyncIterator[RunEvent]:
        if self._coordinator is not None:
            async for event in self._coordinator.stream_compat(request, mode=mode):
                yield event
            return
        async for event in self._stream_impl(request, mode=mode):
            yield event

    async def _stream_impl(
        self,
        request: DueDiligenceRequest,
        *,
        mode: OrchestrationMode = OrchestrationMode.MULTI,
    ) -> AsyncIterator[RunEvent]:
        request_id = self._id_factory()
        run_id = self._id_factory()
        sequencer = EventSequencer(request_id=request_id, run_id=run_id)
        reporting_policy = self.freeze_reporting_policy()
        yield sequencer.next(EventType.RUN_ACCEPTED, {"status": "accepted"})
        from jindiao.api.event_mapper import EventMapper

        queue: asyncio.Queue[RunEvent | object] = asyncio.Queue()
        completed: list[DetailedRun] = []
        failures: list[Exception] = []
        finished = object()

        async def event_sink(event: object) -> None:
            from jindiao.orchestration.base import TeamRuntimeEvent

            if not isinstance(event, TeamRuntimeEvent):
                return
            mapped = EventMapper().map(event, sequencer)
            if mapped is not None:
                await queue.put(mapped)

        async def produce() -> None:
            try:
                completed.append(
                    await self._execute(
                        request,
                        mode=mode,
                        request_id=request_id,
                        run_id=run_id,
                        event_sink=event_sink,
                        reporting_policy=reporting_policy,
                    )
                )
            except Exception as error:
                failures.append(error)
            finally:
                await queue.put(finished)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is finished:
                    break
                if isinstance(item, RunEvent):
                    yield item
            if failures:
                record = error_to_record(failures[0])
                yield sequencer.next(
                    EventType.RUN_FAILED,
                    {"error": cast(JsonValue, record.model_dump(mode="json"))},
                )
                return
            result = completed[0].result
            yield sequencer.next(
                EventType.REPORT_COMPLETED,
                {"result": cast(JsonValue, result.model_dump(mode="json"))},
            )
        finally:
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer

    async def _execute(
        self,
        request: DueDiligenceRequest,
        *,
        mode: OrchestrationMode,
        request_id: str,
        run_id: str,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
        reporting_policy: ReportingPolicyBinding | None = None,
    ) -> DetailedRun:
        reporting_policy = reporting_policy or self.freeze_reporting_policy()
        started_at = self._clock()
        metrics = RunMetricsCollector()
        trace = self._artifact_store.begin(request_id=request_id, run_id=run_id)
        trace.emit("run.started", attributes={"status": "running"})
        runtime_event_sequencer = EventSequencer(request_id=request_id, run_id=run_id)

        async def observe_runtime_event(event: TeamRuntimeEvent) -> None:
            from jindiao.api.event_mapper import EventMapper

            public_event = EventMapper().map(event, runtime_event_sequencer)
            if public_event is not None:
                trace.emit_run_event(public_event)
            if event_sink is not None:
                await event_sink(event)

        try:
            if cancellation_token is not None and getattr(cancellation_token, "cancelled", False):
                raise asyncio.CancelledError()
            metrics.start_phase("context")
            use_live_source = (
                self._settings.data_source_mode == "tianyancha"
                and self._settings.tianyancha_authorization is not None
                and request.scenario_id is None
            )
            if request.scenario_id:
                scenario = self._scenarios.load(request.scenario_id, request.enterprise)
            elif use_live_source:
                scenario = self._scenarios.load_template(self._settings.live_fallback_scenario_id)
            else:
                scenario = self._scenarios.resolve(request.enterprise)
            effective_settings = self._settings.model_copy(
                update={"allow_degraded_mock": request.allow_degraded_mock}
            )
            skill_versions = {
                "tyc-evidence-acquisition": "1.0.0",
                "evidence-backed-due-diligence": "1.0.0",
                "feedback-evolved-reporting": reporting_policy.version,
            }
            if use_live_source and self._settings.tianyancha_annual_report_enabled:
                skill_versions["tianyancha-annual-report-social-security"] = "1.0.0"
            context = RunContext.from_settings(
                request_id=request_id,
                run_id=run_id,
                scenario=scenario,
                settings=effective_settings,
                skill_versions=skill_versions,
                requested_enterprise=request.enterprise,
                report_as_of=request.report_as_of,
                reporting_policy=reporting_policy,
            )
            metrics.finish_phase("context")
            metrics.start_phase("orchestration")
            formal_run: FormalPipelineRun | None = None
            if self._settings.formal_agent_run:
                pipeline = (
                    self._formal_pipeline_factory(context)
                    if self._formal_pipeline_factory is not None
                    else self._create_formal_pipeline(
                        context,
                        use_live_source=use_live_source,
                    )
                )
                formal_run = await pipeline.execute(
                    context,
                    mode=mode,
                    budget=RunBudget.from_policy(context.policy),
                    event_sink=observe_runtime_event,
                    cancellation_token=cancellation_token,
                )
                outcome = formal_run.outcome
            else:
                trace.emit("context.frozen", attributes={"status": "completed"})
                toolset = self._create_toolset(use_live_source=use_live_source)
                strategy: OrchestrationStrategy
                if mode is OrchestrationMode.SINGLE:
                    strategy = SingleAgentStrategy(toolset=toolset, clock=self._clock)
                else:
                    strategy = MultiAgentStrategy(
                        toolset=toolset,
                        team_spec=build_due_diligence_team_spec(
                            team_name=f"jindiao-{run_id}",
                            model_name=context.policy.model_name,
                            max_review_rounds=context.policy.max_repair_rounds,
                            model_temperature=self._settings.model_temperature,
                            model_timeout_seconds=context.policy.request_timeout_seconds,
                            tianyancha_annual_report_enabled=(
                                use_live_source and self._settings.tianyancha_annual_report_enabled
                            ),
                            tianyancha_annual_report_lookback_years=(
                                self._settings.tianyancha_annual_report_lookback_years
                            ),
                            tianyancha_annual_report_timeout_seconds=(
                                self._settings.tianyancha_annual_report_timeout_seconds
                            ),
                        ),
                        runtime=self._team_runtime,
                        clock=self._clock,
                    )
                try:
                    if cancellation_token is not None and getattr(
                        cancellation_token, "cancelled", False
                    ):
                        raise asyncio.CancelledError()
                    outcome = await strategy.execute(
                        context,
                        budget=RunBudget.from_policy(context.policy),
                        event_sink=observe_runtime_event,
                        cancellation_token=cancellation_token,
                    )
                finally:
                    if isinstance(toolset, AsyncClosableToolset):
                        await toolset.aclose()
            metrics.finish_phase("orchestration")
            if outcome.evidence:
                metrics.mark_first_valid_evidence()
            token_count = self._token_count(outcome)
            metrics.record_resources(
                tool_calls=outcome.tool_calls,
                token_count=token_count,
                conflicts=outcome.collaboration.conflicts_detected,
                repairs=outcome.collaboration.repairs_requested,
                repairs_completed=outcome.collaboration.repairs_completed,
            )
            self._trace_outcome(trace, outcome)
            metrics.start_phase("reporting")
            completed_at = self._clock()
            skill_evolution = None
            if request.skill_feedback is not None:
                skill_evolution = SkillEvolutionSummary(
                    status=SkillEvolutionStatus.REJECTED,
                    active_version=reporting_policy.version,
                    reason_codes=("use_feedback_api",),
                    change_summary="Legacy feedback is deprecated; use the v2 feedback endpoint.",
                )
            report_views: list[ReportViewModel] = []
            result = self._result_assembler.assemble(
                context=context,
                outcome=outcome,
                mode=mode,
                started_at=started_at,
                completed_at=completed_at,
                skill_evolution=skill_evolution,
                view_sink=report_views.append,
                snapshot=formal_run.snapshot if formal_run is not None else None,
                agent_results=(formal_run.agent_results if formal_run is not None else ()),
                investigation_cost=(
                    formal_run.investigation_cost if formal_run is not None else None
                ),
                comparison_metadata=(
                    formal_run.comparison_metadata if formal_run is not None else None
                ),
            )
            if event_sink is not None:
                from jindiao.orchestration.base import emit_runtime_event

                for section in result.sections:
                    await emit_runtime_event(
                        event_sink,
                        "section.completed",
                        payload={"section": section.model_dump(mode="json")},
                    )
                if skill_evolution is not None:
                    await emit_runtime_event(
                        event_sink,
                        "skill_evolution.proposed",
                        payload={"candidate": skill_evolution.model_dump(mode="json")},
                    )
            metrics.finish_phase("reporting")
            metric_snapshot = metrics.finish()
            trace.emit(
                "run.completed",
                attributes={
                    "status": result.meta.status.value,
                    "duration_ms": metric_snapshot["end_to_end_duration_ms"],
                    "tool_calls": outcome.tool_calls,
                    "token_count": token_count,
                },
            )
            result = self._artifact_store.complete(
                result,
                metrics=metric_snapshot,
                replay_view=report_views[0],
                binding=reporting_policy,
            )
            return DetailedRun(result=result, outcome=outcome)
        except Exception as error:
            record = error_to_record(error)
            metric_snapshot = metrics.finish()
            trace.emit(
                "run.failed",
                attributes={"error_code": record.code, "message": record.message},
            )
            self._artifact_store.fail(
                run_id=run_id,
                error_code=record.code,
                message=record.message,
                metrics=metric_snapshot,
            )
            raise

    def _create_toolset(self, *, use_live_source: bool) -> InvestigationToolset:
        if self._toolset_factory is not None:
            return self._toolset_factory()
        if not use_live_source:
            return ScenarioToolset(clock=self._clock)
        authorization = self._settings.tianyancha_authorization
        if authorization is None:
            raise AssertionError("live source selected without Tianyancha authorization")
        transport = StreamableHttpMcpTransport(
            self._settings.tianyancha_mcp_url,
            authorization,
            timeout_seconds=self._settings.request_timeout_seconds,
        )
        client = TianyanchaMcpClient(
            transport,
            timeout_seconds=self._settings.request_timeout_seconds,
        )
        deepsearch_agent = None
        if self._settings.tianyancha_annual_report_enabled:
            deepsearch_agent = DeepSearchEvidenceAgent(
                annual_report_provider=TianyanchaAnnualReportProvider(
                    max_lookback_years=(self._settings.tianyancha_annual_report_lookback_years),
                    timeout_seconds=(self._settings.tianyancha_annual_report_timeout_seconds),
                )
            )
        return TianyanchaHybridToolset(
            client=client,
            routing=CapabilityRoutingConfig.from_file(self._settings.tianyancha_routes_path),
            deepsearch_agent=deepsearch_agent,
            max_concurrency=self._settings.max_concurrency,
            clock=self._clock,
        )

    def _create_formal_pipeline(
        self,
        context: RunContext,
        *,
        use_live_source: bool,
    ) -> FormalDueDiligencePipeline:
        if not use_live_source:
            raise ValueError(
                "formal Agent runs require Tianyancha data_source_mode and no scenario override"
            )
        authorization = self._settings.tianyancha_authorization
        model_api_key = self._settings.model_api_key
        model_base_url = self._settings.model_base_url
        if authorization is None or model_api_key is None or model_base_url is None:
            raise AssertionError("validated formal runtime is missing a required route")
        transport = StreamableHttpMcpTransport(
            self._settings.tianyancha_mcp_url,
            authorization,
            timeout_seconds=self._settings.request_timeout_seconds,
        )
        client = TianyanchaMcpClient(
            transport,
            timeout_seconds=self._settings.request_timeout_seconds,
        )
        gateway = TianyanchaMcpGateway(
            client=client,
            routing=CapabilityRoutingConfig.from_file(self._settings.tianyancha_routes_path),
            run_id=context.run_id,
            agent_id=EnterpriseContextAgent.agent_id,
            report_as_of=context.report_as_of,
            budget=GatewayBudget(
                max_mcp_calls=self._settings.max_tool_calls,
                max_concurrency=self._settings.max_concurrency,
            ),
            clock=self._clock,
        )
        prompts = load_prompt_bundle()
        deepsearch = (
            DeepSearchAgent(
                prompt_bundle=prompts,
                annual_report_lookback_years=(
                    self._settings.tianyancha_annual_report_lookback_years
                ),
                annual_report_timeout_seconds=(
                    self._settings.tianyancha_annual_report_timeout_seconds
                ),
            )
            if self._settings.tianyancha_annual_report_enabled
            else None
        )
        return FormalDueDiligencePipeline(
            context_agent=EnterpriseContextAgent(
                gateway=gateway,
                prompt_bundle=prompts,
                report_catalog=REPORT_CATALOG,
            ),
            supplement_policy=SupplementPolicy(
                annual_report_social_security_enabled=(
                    self._settings.tianyancha_annual_report_enabled
                ),
                # No production bounded-Web Tool is configured yet; the declared
                # annual-report baseline remains the only default supplement.
                max_gap_tasks=0,
            ),
            deepsearch_agent=deepsearch,
            context_freezer=ContextFreezer(clock=self._clock),
            single_investigator=SingleInvestigatorAgent(prompt_bundle=prompts),
            multi_investigator=AgentTeamsInvestigatorTeam(
                prompt_bundle=prompts,
                runtime=cast(OpenJiuwenTeamRuntime, self._team_runtime),
            ),
            agent_runtime=OpenJiuwenAgentExecutionRuntime(clock=self._clock),
            gateway=gateway,
            model_name=self._settings.model_name,
            model_provider=self._settings.model_provider,
            model_api_key=model_api_key.get_secret_value(),
            model_base_url=model_base_url,
            model_parameters={"temperature": self._settings.model_temperature},
            prompt_bundle=prompts,
            clock=self._clock,
        )

    @staticmethod
    def _token_count(outcome: OrchestrationOutcome) -> int:
        total = 0
        for event in outcome.runtime_events:
            raw = event.payload.get("token_count", event.payload.get("tokens", 0))
            if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                total += raw
        return total

    @staticmethod
    def _trace_outcome(trace: JsonlRunTrace, outcome: OrchestrationOutcome) -> None:
        for agent in outcome.agent_trace:
            trace.emit(
                "agent.completed",
                agent_id=agent.agent_id,
                attributes={
                    "status": agent.status.value,
                    "duration_ms": agent.duration_ms,
                    "evidence_count": agent.evidence_count,
                },
            )
            for task_id in agent.task_ids:
                trace.emit(
                    "task.completed",
                    agent_id=agent.agent_id,
                    task_id=task_id,
                    attributes={"status": agent.status.value},
                )
        trace.emit(
            "tool.summary",
            tool_id="budget-ledger",
            attributes={"tool_calls": outcome.tool_calls},
        )
        for evidence in outcome.evidence:
            trace.emit(
                "evidence.collected",
                evidence_id=evidence.evidence_id,
                attributes={
                    "domain": evidence.supports_fields[0].partition(".")[0],
                    "source_status": evidence.source_status.value,
                    "result_count": 1,
                },
            )
        for issue in outcome.review_issues:
            trace.emit(
                "review.issue",
                attributes={
                    "status": "resolved" if issue.resolved else "unresolved",
                    "conflicts_detected": int("conflict" in issue.issue_type),
                },
            )
        for repair in outcome.repair_tasks:
            trace.emit(
                "repair.requested",
                agent_id=repair.target_agent,
                task_id=repair.repair_id,
                attributes={"repairs_requested": 1, "status": "requested"},
            )


__all__ = ["DetailedRun", "DueDiligenceService"]
