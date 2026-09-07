"""Parallel multi-agent strategy with independent evidence review."""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable, Mapping, Set
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from jindiao.agents.deepsearch_agent import (
    DeepSearchEvidenceAgent,
    DeepSearchTask,
)
from jindiao.agents.specialists import (
    GovernanceAgent,
    JudicialComplianceAgent,
    OperationsPeerAgent,
)
from jindiao.application.context import RunContext
from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.evidence import CoverageSummary, SourceStatus
from jindiao.contracts.investigation import Finding, InvestigationTask, RepairTask, TaskStatus
from jindiao.contracts.results import (
    AgentStatus,
    AgentTrace,
    CollaborationSummary,
    OrchestrationMode,
)
from jindiao.deepsearch import MockFallbackService, ScenarioDeepSearchProvider
from jindiao.tianyancha import SourceStateDecision

from .base import (
    BudgetLedger,
    CancellationToken,
    CapabilityAwareInvestigationToolset,
    DomainInvestigation,
    InvestigationToolset,
    OrchestrationOutcome,
    RunBudget,
    RuntimeEventSink,
    TeamRuntimeEvent,
    check_cancellation,
    emit_runtime_event,
    merge_section_data,
    require_deterministic_harness,
    resolve_subject_with_cancellation,
)
from .evidence_store import EvidenceStore
from .planner import CapabilityAwarePlanner
from .repair import RepairCoordinator
from .reviewer import EvidenceReviewer
from .team_runtime import TeamRuntimeDriver

_DOMAINS = ("governance", "judicial", "operations", "peers")


class MultiAgentStrategy:
    """Non-formal deterministic team harness retained for fixture regression."""

    formal_agent_run = False

    def __init__(
        self,
        *,
        toolset: InvestigationToolset,
        team_spec: object,
        runtime: TeamRuntimeDriver,
        planner: CapabilityAwarePlanner | None = None,
        reviewer: EvidenceReviewer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        domain_capabilities: Mapping[str, tuple[str, ...]] | None = None,
        mock_domains: frozenset[str] = frozenset(),
    ) -> None:
        self._toolset = toolset
        self._team_spec = team_spec
        self._runtime = runtime
        self._planner = planner or CapabilityAwarePlanner()
        self._reviewer = reviewer or EvidenceReviewer(clock=clock)
        self._clock = clock
        self._domain_capabilities = dict(
            domain_capabilities or {domain: ("scenario_repository",) for domain in _DOMAINS}
        )
        self._mock_domains = mock_domains

    @property
    def mode(self) -> OrchestrationMode:
        return OrchestrationMode.MULTI

    async def execute(
        self,
        context: RunContext,
        *,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> OrchestrationOutcome:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        ledger = BudgetLedger(budget)
        run_started = self._clock()
        await ledger.claim_tool_call("resolve_subject")
        subject = await resolve_subject_with_cancellation(
            self._toolset,
            context,
            cancellation_token,
        )
        await emit_runtime_event(
            event_sink,
            "entity.resolved",
            payload={"subject": subject.model_dump(mode="json")},
        )
        domain_capabilities = self._domain_capabilities
        mock_domains: Set[str] = self._mock_domains
        if isinstance(self._toolset, CapabilityAwareInvestigationToolset):
            await ledger.claim_tool_call("discover_capabilities")
            domain_capabilities = dict(await self._toolset.domain_capabilities(subject))
            mock_domains = self._toolset.mock_domains
        plan = self._planner.create_plan(
            subject=subject,
            domain_capabilities=domain_capabilities,
            mock_domains=mock_domains,
            budget=budget,
        )
        await emit_runtime_event(
            event_sink,
            "plan.created",
            payload={"plan": plan.model_dump(mode="json")},
        )
        leader_completed = self._clock()
        runtime_ready = asyncio.Event()
        runtime_task = asyncio.create_task(
            self._collect_runtime_events(
                context,
                {
                    "query": self._team_query(subject, plan),
                    "request_id": context.request_id,
                    "run_id": context.run_id,
                    "scenario_snapshot_id": context.scenario_snapshot_id,
                    "report_as_of": context.report_as_of.isoformat(),
                    "rule_version": context.rule_version,
                    "skill_versions": dict(context.skill_versions),
                    "budget": budget.model_dump(mode="json"),
                    "subject": subject.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                },
                event_sink=event_sink,
                ready=runtime_ready,
                timeout_seconds=budget.timeout_seconds,
                cancellation_token=cancellation_token,
            )
        )
        await self._wait_for_runtime_ready(runtime_task, runtime_ready)

        active_tasks = tuple(
            task
            for task in plan.tasks
            if task.domain != "review" and task.status is not TaskStatus.SKIPPED
        )
        store = EvidenceStore(subject_id=subject.subject_id)
        semaphore = asyncio.Semaphore(budget.max_concurrency)
        executions: list[tuple[str, DomainInvestigation, datetime, datetime]] = []

        async def execute_task(task: InvestigationTask) -> DomainInvestigation:
            check_cancellation(cancellation_token)
            async with semaphore:
                check_cancellation(cancellation_token)
                await ledger.claim_tool_call(f"investigate:{task.domain}")
                started = self._clock()
                artifact = await self._investigate_task(
                    context, subject, task, cancellation_token=cancellation_token
                )
                normalized = await store.ingest(task.assigned_agent, artifact)
                for item in normalized.evidence:
                    await emit_runtime_event(
                        event_sink,
                        "evidence.collected",
                        member_name=task.assigned_agent,
                        payload={"evidence": item.model_dump(mode="json")},
                    )
                for coverage in normalized.coverage_items:
                    if coverage.status in {
                        SourceStatus.CAPABILITY_ABSENT,
                        SourceStatus.DEGRADED_MOCK,
                    }:
                        await emit_runtime_event(
                            event_sink,
                            "source.fallback",
                            member_name=task.assigned_agent,
                            payload={
                                "source_status": coverage.status.value,
                                "domain": coverage.domain,
                                "capability": coverage.capability,
                            },
                        )
                completed = self._clock()
                executions.append((task.assigned_agent, normalized, started, completed))
                return normalized

        try:
            artifacts = tuple(await asyncio.gather(*(execute_task(task) for task in active_tasks)))
            runtime_events = await runtime_task
        except BaseException:
            if not runtime_task.done():
                runtime_task.cancel()
            with suppress(asyncio.CancelledError):
                await runtime_task
            raise
        proposed_findings = tuple(
            finding for artifact in artifacts for finding in artifact.findings
        )
        review_started = self._clock()
        review = self._reviewer.review(
            subject=subject,
            findings=proposed_findings,
            evidence=store.evidence,
            report_as_of=context.report_as_of,
        )
        initial_conflict_count = len(review.conflicts)
        for issue in review.issues:
            if "conflict" in issue.issue_type:
                await emit_runtime_event(
                    event_sink,
                    "conflict.detected",
                    member_name="reviewer-agent",
                    payload={"issue": issue.model_dump(mode="json")},
                )
        repair_coordinator = RepairCoordinator(max_rounds=budget.max_repair_rounds)
        repair_tasks: list[RepairTask] = []
        repairs_completed = 0
        finding_by_id: dict[str, Finding] = {
            finding.finding_id: finding for finding in proposed_findings
        }
        for attempt in range(1, budget.max_repair_rounds + 1):
            requested = repair_coordinator.create_tasks(review.issues, attempt=attempt)
            if not requested:
                break
            repair_tasks.extend(requested)
            for repair in requested:
                await emit_runtime_event(
                    event_sink,
                    "repair.requested",
                    member_name=repair.target_agent,
                    payload={"repair_task": repair.model_dump(mode="json")},
                )
                target = self._repair_target(repair, active_tasks)
                if target is None:
                    continue
                await ledger.claim_tool_call(f"repair:{target.domain}")
                started = self._clock()
                check_cancellation(cancellation_token)
                repaired = await self._investigate_task(
                    context, subject, target, cancellation_token=cancellation_token
                )
                repaired = self._version_repair_artifact(repaired, repair)
                normalized = await store.ingest(target.assigned_agent, repaired)
                completed = self._clock()
                executions.append((target.assigned_agent, normalized, started, completed))
                repairs_completed += 1
                for finding in normalized.findings:
                    finding_by_id[finding.finding_id] = finding
            review = self._reviewer.review(
                subject=subject,
                findings=tuple(finding_by_id.values()),
                evidence=store.evidence,
                report_as_of=context.report_as_of,
            )
            if not review.issues:
                break
        review_completed = self._clock()
        final_findings = repair_coordinator.finalize_unresolved(review.findings, review.issues)
        traces = self._build_traces(
            executions,
            leader_started=run_started,
            leader_completed=leader_completed,
            review_started=review_started,
            review_completed=review_completed,
            review_evidence_count=len(store.evidence),
        )
        errors = tuple(item for artifact in artifacts for item in artifact.errors)
        return OrchestrationOutcome(
            subject=subject,
            findings=final_findings,
            evidence=store.evidence,
            coverage=CoverageSummary.from_items(
                [item for artifact in artifacts for item in artifact.coverage_items]
            ),
            review_issues=review.issues,
            review_completed=True,
            section_data=merge_section_data(artifacts),
            agent_trace=traces,
            collaboration=CollaborationSummary(
                agent_count=len(traces),
                task_count=len(active_tasks) + 1,
                parallel_task_count=(len(active_tasks) if budget.max_concurrency > 1 else 0),
                conflicts_detected=initial_conflict_count,
                repairs_requested=len(repair_tasks),
                repairs_completed=repairs_completed,
            ),
            errors=errors,
            tool_calls=ledger.tool_calls,
            repair_tasks=tuple(repair_tasks),
            runtime_events=runtime_events,
        )

    async def _investigate_task(
        self,
        context: RunContext,
        subject: object,
        task: InvestigationTask,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> DomainInvestigation:
        from jindiao.contracts.entities import ResolvedSubject

        if not isinstance(subject, ResolvedSubject):
            raise TypeError("expected ResolvedSubject")
        check_cancellation(cancellation_token)
        if task.assigned_agent == GovernanceAgent.agent_id:
            return await GovernanceAgent(toolset=self._toolset).investigate(
                context, subject, task.domain, cancellation_token=cancellation_token
            )
        if task.assigned_agent == JudicialComplianceAgent.agent_id:
            return await JudicialComplianceAgent(toolset=self._toolset).investigate(
                context, subject, task.domain, cancellation_token=cancellation_token
            )
        if task.assigned_agent == OperationsPeerAgent.agent_id:
            return await OperationsPeerAgent(toolset=self._toolset).investigate(
                context, subject, task.domain, cancellation_token=cancellation_token
            )
        if task.assigned_agent == DeepSearchEvidenceAgent.agent_id:
            provider = ScenarioDeepSearchProvider(context.scenario)
            await provider.aopen()
            try:
                deepsearch_task = DeepSearchTask(
                    source=SourceStateDecision(
                        status=SourceStatus.CAPABILITY_ABSENT,
                        mock_fallback_allowed=True,
                    ),
                    domain=task.domain,
                    capability=task.capability or "mock_fallback",
                    query=self._deepsearch_query(subject.company_name, task.domain),
                    allow_degraded_mock=context.policy.allow_degraded_mock,
                )
                deepsearch = DeepSearchEvidenceAgent(service=MockFallbackService(provider))
                if cancellation_token is None:
                    return await deepsearch.investigate(
                        deepsearch_task,
                        subject,
                        queried_at=self._clock(),
                    )
                check_cancellation(cancellation_token)
                return await deepsearch.investigate(
                    deepsearch_task,
                    subject,
                    queried_at=self._clock(),
                )
            finally:
                await provider.aclose()
        raise ValueError(f"unsupported assigned agent: {task.assigned_agent}")

    async def _collect_runtime_events(
        self,
        context: RunContext,
        inputs: dict[str, object],
        *,
        event_sink: RuntimeEventSink | None = None,
        ready: asyncio.Event | None = None,
        timeout_seconds: int | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> tuple[TeamRuntimeEvent, ...]:
        events: list[TeamRuntimeEvent] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                stream_kwargs: dict[str, Any] = {"session_id": context.run_id}
                if "cancellation_token" in inspect.signature(self._runtime.stream).parameters:
                    stream_kwargs["cancellation_token"] = cancellation_token
                async for event in cast(Any, self._runtime).stream(
                    self._team_spec, inputs, **stream_kwargs
                ):
                    check_cancellation(cancellation_token)
                    if ready is not None:
                        ready.set()
                    events.append(event)
                    if event_sink is not None:
                        await event_sink(event)
        except TimeoutError as error:
            raise AgentExecutionError(
                "AgentTeams runtime deadline exceeded",
                details={"timeout_seconds": timeout_seconds},
            ) from error
        return tuple(events)

    @staticmethod
    def _team_query(subject: object, plan: object) -> str:
        from jindiao.contracts.entities import ResolvedSubject
        from jindiao.contracts.investigation import InvestigationPlan

        if not isinstance(subject, ResolvedSubject) or not isinstance(plan, InvestigationPlan):
            raise TypeError("expected ResolvedSubject and InvestigationPlan")
        assignments = ", ".join(
            f"{task.domain}->{task.assigned_agent}"
            for task in plan.tasks
            if task.domain != "review" and task.status is not TaskStatus.SKIPPED
        )
        return (
            f"Coordinate the bounded due-diligence run for {subject.company_name}. "
            f"Acknowledge these predefined specialist assignments: {assignments}. "
            "The application executes evidence retrieval and deterministic review independently; "
            "do not use shell or file tools, do not retrieve evidence, and do not set a score. "
            "Call build_team exactly once to establish and acknowledge the declared roster. "
            "Do not call send_message, shutdown_member, or clean_team: the application lifecycle "
            "controller performs deterministic cleanup immediately after build_team succeeds."
        )

    @staticmethod
    async def _wait_for_runtime_ready(
        runtime_task: asyncio.Task[tuple[TeamRuntimeEvent, ...]],
        ready: asyncio.Event,
    ) -> None:
        ready_waiter = asyncio.create_task(ready.wait())
        done, _ = await asyncio.wait(
            (runtime_task, ready_waiter),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready_waiter not in done:
            ready_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await ready_waiter
        if runtime_task in done and not ready.is_set():
            await runtime_task
            raise AgentExecutionError(
                "AgentTeams runtime produced no observable coordination event"
            )

    @staticmethod
    def _deepsearch_query(company_name: str, domain: str) -> str:
        topics = {
            "governance": "公司 工商 股东 高管 治理",
            "judicial": "公司 司法 执行 失信 案件 合规",
            "operations": "公司 经营 员工 财务 资质",
            "peers": "公司 行业 同类 企业 对标",
        }
        return f"{company_name} {topics.get(domain, '公司 尽调')}"

    @staticmethod
    def _repair_target(
        repair: RepairTask,
        active_tasks: tuple[InvestigationTask, ...],
    ) -> InvestigationTask | None:
        if not repair.requested_fields:
            return None
        domain = repair.requested_fields[0].split(".", 1)[0]
        return next((task for task in active_tasks if task.domain == domain), None)

    @staticmethod
    def _version_repair_artifact(
        artifact: DomainInvestigation,
        repair: RepairTask,
    ) -> DomainInvestigation:
        evidence_aliases = {
            item.evidence_id: f"{item.evidence_id}:{repair.repair_id}" for item in artifact.evidence
        }
        return artifact.model_copy(
            update={
                "task_id": repair.repair_id,
                "evidence": tuple(
                    item.model_copy(update={"evidence_id": evidence_aliases[item.evidence_id]})
                    for item in artifact.evidence
                ),
                "findings": tuple(
                    finding.model_copy(
                        update={
                            "evidence_ids": tuple(
                                evidence_aliases[evidence_id]
                                for evidence_id in finding.evidence_ids
                            )
                        }
                    )
                    for finding in artifact.findings
                ),
            }
        )

    @staticmethod
    def _build_traces(
        executions: list[tuple[str, DomainInvestigation, datetime, datetime]],
        *,
        leader_started: datetime,
        leader_completed: datetime,
        review_started: datetime,
        review_completed: datetime,
        review_evidence_count: int,
    ) -> tuple[AgentTrace, ...]:
        grouped: dict[str, list[tuple[DomainInvestigation, datetime, datetime]]] = defaultdict(list)
        for agent_id, artifact, started, completed in executions:
            grouped[agent_id].append((artifact, started, completed))
        traces = [
            AgentTrace(
                agent_id="leader",
                role="leader",
                task_ids=("plan",),
                status=AgentStatus.COMPLETED,
                started_at=leader_started,
                completed_at=leader_completed,
                duration_ms=max(0, int((leader_completed - leader_started).total_seconds() * 1000)),
                evidence_count=0,
                error_codes=(),
            )
        ]
        for agent_id, rows in sorted(grouped.items()):
            started = min(row[1] for row in rows)
            completed = max(row[2] for row in rows)
            traces.append(
                AgentTrace(
                    agent_id=agent_id,
                    role=agent_id.removesuffix("-agent"),
                    task_ids=tuple(row[0].task_id for row in rows),
                    status=AgentStatus.COMPLETED,
                    started_at=started,
                    completed_at=completed,
                    duration_ms=max(0, int((completed - started).total_seconds() * 1000)),
                    evidence_count=sum(len(row[0].evidence) for row in rows),
                    error_codes=tuple(error.code.value for row in rows for error in row[0].errors),
                )
            )
        traces.append(
            AgentTrace(
                agent_id="reviewer-agent",
                role="reviewer",
                task_ids=("review-evidence",),
                status=AgentStatus.COMPLETED,
                started_at=review_started,
                completed_at=review_completed,
                duration_ms=max(0, int((review_completed - review_started).total_seconds() * 1000)),
                evidence_count=review_evidence_count,
                error_codes=(),
            )
        )
        return tuple(traces)


__all__ = ["MultiAgentStrategy"]
