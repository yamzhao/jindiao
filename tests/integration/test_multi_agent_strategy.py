# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jindiao.application import RunContext, Settings
from jindiao.application.errors import AgentExecutionError
from jindiao.application.result_assembler import ResultAssembler
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageSummary, Evidence, SourceStatus, SourceType
from jindiao.contracts.investigation import Finding, FindingStatus, RiskClass, Severity
from jindiao.contracts.results import CollaborationSummary, OrchestrationMode
from jindiao.orchestration import RunBudget
from jindiao.orchestration.base import (
    DomainInvestigation,
    OrchestrationOutcome,
    TeamRuntimeEvent,
)
from jindiao.orchestration.multi import MultiAgentStrategy
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.orchestration.team_runtime import TeamRuntimeDriver
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.risk import RiskRuleEngine, RiskRuleSet
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def context(scenario_id: str, company_name: str, *, max_repairs: int = 1) -> RunContext:
    enterprise = EnterpriseInput(company_name=company_name)
    snapshot = ScenarioRepository(Path("mock_data/scenarios")).load(scenario_id, enterprise)
    settings = Settings(
        model_name="deterministic-test-model",
        max_tool_calls=20,
        max_concurrency=4,
        max_repair_rounds=max_repairs,
    )
    return RunContext.from_settings(
        request_id=f"req-{scenario_id}",
        run_id=f"run-{scenario_id}",
        scenario=snapshot,
        settings=settings,
        skill_versions={"reporting": "1.0.0"},
    )


class FakeTeamRuntime(TeamRuntimeDriver):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        self.calls.append({"spec": spec, "inputs": inputs, "session_id": session_id})
        yield TeamRuntimeEvent(
            event_type="team.runtime_ready",
            member_name="leader",
            payload={"session_id": session_id},
        )
        yield TeamRuntimeEvent(
            event_type="member.completed",
            member_name="reviewer-agent",
            payload={"status": "completed"},
        )


class ParallelProbeToolset:
    def __init__(self, delegate: ScenarioToolset) -> None:
        self.delegate = delegate
        self.active = 0
        self.peak = 0
        self.investigation_calls = 0

    async def resolve_subject(self, run_context: RunContext) -> ResolvedSubject:
        return await self.delegate.resolve_subject(run_context)

    async def investigate(
        self,
        run_context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        self.investigation_calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)
        result = await self.delegate.investigate(run_context, subject, domain)
        await asyncio.sleep(0)
        self.active -= 1
        return result


class CapabilityAwareProbeToolset(ParallelProbeToolset):
    mock_domains = frozenset({"judicial", "operations", "peers"})

    def __init__(self, delegate: ScenarioToolset) -> None:
        super().__init__(delegate)
        self.capability_calls = 0

    async def domain_capabilities(
        self,
        subject: ResolvedSubject,
    ) -> dict[str, tuple[str, ...]]:
        self.capability_calls += 1
        return {"governance": ("get_company_registration_info",)}


def result_assembler() -> ResultAssembler:
    rules = RiskRuleSet.from_file(Path("config/risk-rules-v1.json"))
    return ResultAssembler(rule_engine=RiskRuleEngine(rules))


@pytest.mark.asyncio
async def test_multi_strategy_runs_openjiuwen_shell_and_domains_in_parallel() -> None:
    run_context = context("judicial-high-risk", "金调震岳工程有限公司")
    runtime = FakeTeamRuntime()
    toolset = ParallelProbeToolset(ScenarioToolset(clock=lambda: NOW))
    strategy = MultiAgentStrategy(
        toolset=toolset,
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-integration",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=runtime,
        clock=lambda: NOW,
    )

    outcome = await strategy.execute(
        run_context,
        budget=RunBudget.from_policy(run_context.policy),
    )

    assert runtime.calls[0]["session_id"] == run_context.run_id
    assert toolset.peak > 1
    assert outcome.collaboration.parallel_task_count == 4
    assert outcome.review_completed is True
    assert outcome.runtime_events[0].event_type == "team.runtime_ready"
    assert (
        next(item for item in outcome.findings if item.finding_id == "finding-dishonest").status
        is FindingStatus.ACCEPTED
    )

    result = result_assembler().assemble(
        context=run_context,
        outcome=outcome,
        mode=OrchestrationMode.MULTI,
        started_at=NOW,
        completed_at=NOW,
    )
    assert result.decision.band.value == "reject"
    assert result.decision.score >= 80
    assert result.collaboration.agent_count >= 5


class GatedTeamRuntime(FakeTeamRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        self.calls.append({"spec": spec, "inputs": inputs, "session_id": session_id})
        self.started.set()
        await self.release.wait()
        yield TeamRuntimeEvent(
            event_type="team.runtime_ready",
            member_name="leader",
            payload={"session_id": session_id},
        )


@pytest.mark.asyncio
async def test_agentteams_run_is_a_required_gate_before_specialist_execution() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    runtime = GatedTeamRuntime()
    toolset = ParallelProbeToolset(ScenarioToolset(clock=lambda: NOW))
    execution = asyncio.create_task(
        MultiAgentStrategy(
            toolset=toolset,
            team_spec=build_due_diligence_team_spec(
                team_name="jindiao-gated",
                model_name="test-model",
                max_review_rounds=1,
            ),
            runtime=runtime,
            clock=lambda: NOW,
        ).execute(run_context, budget=RunBudget.from_policy(run_context.policy))
    )

    await asyncio.wait_for(runtime.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert toolset.investigation_calls == 0
    runtime.release.set()
    await execution

    runtime_inputs = runtime.calls[0]["inputs"]
    assert isinstance(runtime_inputs, dict)
    assert runtime_inputs["subject"] == (
        await toolset.delegate.resolve_subject(run_context)
    ).model_dump(mode="json")
    assert runtime_inputs["report_as_of"] == run_context.report_as_of.isoformat()
    assert runtime_inputs["skill_versions"] == dict(run_context.skill_versions)
    query = runtime_inputs["query"]
    assert isinstance(query, str)
    assert "乐视网信息技术（北京）股份有限公司" in query
    assert "build_team" in query
    assert "lifecycle controller" in query
    assert "Do not call send_message" in query


class NonTerminatingTeamRuntime(FakeTeamRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = asyncio.Event()

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        self.calls.append({"spec": spec, "inputs": inputs, "session_id": session_id})
        try:
            yield TeamRuntimeEvent(
                event_type="team.runtime_ready",
                member_name="leader",
                payload={"session_id": session_id},
            )
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


@pytest.mark.asyncio
async def test_multi_strategy_bounds_and_cancels_non_terminating_agentteams_runtime() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    runtime = NonTerminatingTeamRuntime()
    strategy = MultiAgentStrategy(
        toolset=ParallelProbeToolset(ScenarioToolset(clock=lambda: NOW)),
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-timeout",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=runtime,
        clock=lambda: NOW,
    )
    budget = RunBudget(
        max_tool_calls=20,
        max_concurrency=4,
        timeout_seconds=1,
        max_repair_rounds=1,
    )

    with pytest.raises(AgentExecutionError, match="AgentTeams runtime deadline exceeded"):
        await asyncio.wait_for(strategy.execute(run_context, budget=budget), timeout=1.5)

    assert runtime.cancelled.is_set()


@pytest.mark.asyncio
async def test_multi_strategy_uses_runtime_capabilities_before_planning() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    runtime = FakeTeamRuntime()
    toolset = CapabilityAwareProbeToolset(ScenarioToolset(clock=lambda: NOW))

    await MultiAgentStrategy(
        toolset=toolset,
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-live-plan",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=runtime,
        clock=lambda: NOW,
    ).execute(run_context, budget=RunBudget.from_policy(run_context.policy))

    inputs = runtime.calls[0]["inputs"]
    assert isinstance(inputs, dict)
    plan = inputs["plan"]
    assert isinstance(plan, dict)
    raw_tasks = plan["tasks"]
    assert isinstance(raw_tasks, list)
    assert all(isinstance(item, dict) for item in raw_tasks)
    tasks = {item["domain"]: item for item in raw_tasks if isinstance(item, dict)}
    assert toolset.capability_calls == 1
    assert tasks["governance"]["capability"] == "get_company_registration_info"
    assert tasks["judicial"]["capability"] == "mock_fallback"


class OneMissingCapabilityToolset(ParallelProbeToolset):
    mock_domains = frozenset({"judicial"})

    async def domain_capabilities(
        self,
        subject: ResolvedSubject,
    ) -> dict[str, tuple[str, ...]]:
        del subject
        return {
            "governance": ("registration",),
            "judicial": (),
            "operations": ("operations",),
            "peers": ("peers",),
        }


@pytest.mark.asyncio
async def test_capability_absence_is_executed_by_deepsearch_agent() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    toolset = OneMissingCapabilityToolset(ScenarioToolset(clock=lambda: NOW))

    outcome = await MultiAgentStrategy(
        toolset=toolset,
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-deepsearch",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=FakeTeamRuntime(),
        clock=lambda: NOW,
    ).execute(run_context, budget=RunBudget.from_policy(run_context.policy))

    deepsearch_evidence = [
        item for item in outcome.evidence if item.source_tool == "deepsearch.local"
    ]
    assert deepsearch_evidence
    assert all(item.supports_fields == ("judicial.mock_fallback",) for item in deepsearch_evidence)
    assert any(trace.agent_id == "deepsearch-agent" for trace in outcome.agent_trace)


class ConflictToolset(ParallelProbeToolset):
    async def investigate(
        self,
        run_context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        artifact = await super().investigate(run_context, subject, domain)
        if domain != "peers":
            return artifact
        item = Evidence(
            evidence_id="ev-corpus-employees",
            claim="员工人数",
            value=112,
            subject_id=subject.subject_id,
            source_type=SourceType.MOCK,
            source_status=SourceStatus.CAPABILITY_ABSENT,
            source_tool="deepsearch.local",
            source_record_id="chunk-0001",
            queried_at=NOW,
            as_of_date=run_context.scenario.manifest.as_of_date,
            confidence=1,
            is_mock=True,
            supports_fields=("operations.employee_count",),
            raw_ref="mock://evidence-conflict/v1/corpus/operating-notes.md#chunk-0001",
        )
        conflict = Finding(
            finding_id="finding-corpus-employee-count",
            subject_id=subject.subject_id,
            domain="operations",
            claim="补充材料员工人数已采集",
            value=112,
            risk_class=RiskClass.NON_RISK,
            severity=Severity.INFO,
            evidence_ids=(item.evidence_id,),
        )
        return DomainInvestigation(
            task_id=artifact.task_id,
            domain=artifact.domain,
            findings=(*artifact.findings, conflict),
            evidence=(*artifact.evidence, item),
            coverage_items=artifact.coverage_items,
            section_data=artifact.section_data,
        )


@pytest.mark.asyncio
async def test_conflict_blocks_findings_and_creates_only_targeted_bounded_repairs() -> None:
    run_context = context("evidence-conflict", "金调双源制造有限公司", max_repairs=1)
    ticks = 0

    def advancing_clock() -> datetime:
        nonlocal ticks
        ticks += 1
        return NOW + timedelta(microseconds=ticks)

    outcome = await MultiAgentStrategy(
        toolset=ScenarioToolset(clock=advancing_clock),
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-conflict",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=FakeTeamRuntime(),
        clock=advancing_clock,
    ).execute(run_context, budget=RunBudget.from_policy(run_context.policy))

    employee_findings = [item for item in outcome.findings if "employee" in item.finding_id]
    assert len(employee_findings) == 2
    assert all(item.status is FindingStatus.UNCONFIRMED for item in employee_findings)
    assert outcome.collaboration.conflicts_detected == 1
    assert outcome.collaboration.repairs_requested == 1
    assert outcome.collaboration.repairs_completed == 1
    assert len(outcome.repair_tasks) == 1
    assert outcome.repair_tasks[0].target_agent == "operations-peer-agent"
    assert outcome.repair_tasks[0].attempt == 1


class UnbackedToolset(ParallelProbeToolset):
    async def investigate(
        self,
        run_context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        artifact = await super().investigate(run_context, subject, domain)
        if domain != "judicial":
            return artifact
        unbacked = Finding(
            finding_id="finding-unbacked-risk",
            subject_id=subject.subject_id,
            domain="judicial",
            claim="企业可能存在失信记录",
            risk_class=RiskClass.ADMISSION,
            severity=Severity.CRITICAL,
        )
        return artifact.model_copy(update={"findings": (unbacked,)})


@pytest.mark.asyncio
async def test_multi_reviewer_rejects_unbacked_risk_before_rule_engine() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    outcome = await MultiAgentStrategy(
        toolset=UnbackedToolset(ScenarioToolset(clock=lambda: NOW)),
        team_spec=build_due_diligence_team_spec(
            team_name="jindiao-unbacked",
            model_name="test-model",
            max_review_rounds=1,
        ),
        runtime=FakeTeamRuntime(),
        clock=lambda: NOW,
    ).execute(run_context, budget=RunBudget.from_policy(run_context.policy))

    unbacked = next(item for item in outcome.findings if item.finding_id == "finding-unbacked-risk")
    assert unbacked.status is FindingStatus.UNCONFIRMED
    result = result_assembler().assemble(
        context=run_context,
        outcome=outcome,
        mode=OrchestrationMode.MULTI,
        started_at=NOW,
        completed_at=NOW,
    )
    assert result.decision.score == 0


def test_result_assembler_refuses_to_run_before_final_review() -> None:
    run_context = context("normal-enterprise", "乐视网信息技术（北京）股份有限公司")
    unresolved = OrchestrationOutcome(
        subject=ResolvedSubject(
            subject_id="mock:normal-enterprise",
            company_name="乐视网信息技术（北京）股份有限公司",
            source=SubjectSource.MOCK,
            resolved_at=NOW,
        ),
        findings=(),
        evidence=(),
        coverage=CoverageSummary.from_items([]),
        review_issues=(),
        review_completed=False,
        section_data={},
        agent_trace=(),
        collaboration=CollaborationSummary(
            agent_count=0,
            task_count=0,
            parallel_task_count=0,
            conflicts_detected=0,
            repairs_requested=0,
            repairs_completed=0,
        ),
        errors=(),
        tool_calls=0,
    )

    with pytest.raises(ValueError, match="review"):
        result_assembler().assemble(
            context=run_context,
            outcome=unresolved,
            mode=OrchestrationMode.MULTI,
            started_at=NOW,
            completed_at=NOW,
        )
