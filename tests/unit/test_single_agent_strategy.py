from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jindiao.application import RunContext, Settings
from jindiao.application.result_assembler import ResultAssembler
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.evidence import SourceStatus
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.orchestration import RunBudget, SingleAgentStrategy
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.risk import RiskRuleEngine, RiskRuleSet
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 3, tzinfo=UTC)
SCENARIOS = Path("mock_data/scenarios")


def context(*, allow_degraded_mock: bool = False) -> RunContext:
    enterprise = EnterpriseInput(company_name="金调绿洲科技有限公司")
    snapshot = ScenarioRepository(SCENARIOS).resolve(enterprise)
    settings = Settings(
        model_name="deterministic-test-model",
        allow_degraded_mock=allow_degraded_mock,
        max_tool_calls=10,
    )
    return RunContext.from_settings(
        request_id="req-single",
        run_id="run-single",
        scenario=snapshot,
        settings=settings,
        skill_versions={"evidence": "1.0.0", "reporting": "1.0.0"},
    )


def assembler() -> ResultAssembler:
    rules = RiskRuleSet.from_file(Path("config/risk-rules-v1.json"))
    return ResultAssembler(rule_engine=RiskRuleEngine(rules))


@pytest.mark.asyncio
async def test_single_agent_runs_subject_domains_and_self_review_sequentially() -> None:
    run_context = context()
    toolset = ScenarioToolset()
    strategy = SingleAgentStrategy(toolset=toolset, clock=lambda: NOW)

    outcome = await strategy.execute(
        run_context,
        budget=RunBudget.from_policy(run_context.policy),
    )

    assert toolset.call_order == [
        "resolve_subject",
        "governance",
        "judicial",
        "operations",
        "peers",
    ]
    assert outcome.review_completed is True
    assert all(item.status.value == "accepted" for item in outcome.findings)
    assert outcome.collaboration.agent_count == 1
    assert outcome.collaboration.parallel_task_count == 0
    assert outcome.agent_trace[0].task_ids == (
        "single-governance",
        "single-judicial",
        "single-operations",
        "single-peers",
    )


@pytest.mark.asyncio
async def test_single_agent_uses_shared_rules_and_report_assembler_end_to_end() -> None:
    run_context = context()
    outcome = await SingleAgentStrategy(toolset=ScenarioToolset(), clock=lambda: NOW).execute(
        run_context, budget=RunBudget.from_policy(run_context.policy)
    )

    result = assembler().assemble(
        context=run_context,
        outcome=outcome,
        mode=OrchestrationMode.SINGLE,
        started_at=NOW,
        completed_at=NOW,
    )

    assert result.meta.status is RunStatus.COMPLETED
    assert result.meta.scenario_snapshot_id == run_context.scenario_snapshot_id
    assert result.decision.band.value == "pass"
    assert len(result.sections) == 8
    assert "# 企业信用与风控尽调报告" in result.report_markdown
    assert result.evaluation.mode is OrchestrationMode.SINGLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "allow_degraded", "expected_section_status", "expected_run_status"),
    [
        (SourceStatus.VERIFIED_EMPTY, False, "complete", RunStatus.COMPLETED),
        (SourceStatus.CAPABILITY_ABSENT, False, "partial", RunStatus.PARTIAL),
        (SourceStatus.SOURCE_ERROR, False, "unavailable", RunStatus.PARTIAL),
        (SourceStatus.SOURCE_ERROR, True, "complete", RunStatus.COMPLETED),
    ],
)
async def test_single_agent_preserves_empty_fallback_and_error_semantics(
    status: SourceStatus,
    allow_degraded: bool,
    expected_section_status: str,
    expected_run_status: RunStatus,
) -> None:
    run_context = context(allow_degraded_mock=allow_degraded)
    toolset = ScenarioToolset(source_status_overrides={"operations": status})
    outcome = await SingleAgentStrategy(toolset=toolset, clock=lambda: NOW).execute(
        run_context,
        budget=RunBudget.from_policy(run_context.policy),
    )
    result = assembler().assemble(
        context=run_context,
        outcome=outcome,
        mode=OrchestrationMode.SINGLE,
        started_at=NOW,
        completed_at=NOW,
    )

    operations = next(item for item in result.sections if item.section_id == "operations-analysis")
    assert operations.status.value == expected_section_status
    assert result.meta.status is expected_run_status
    if status is SourceStatus.SOURCE_ERROR and not allow_degraded:
        assert result.errors[0].code.value == "source_unavailable"
        assert "数据源不可用" in result.report_markdown
