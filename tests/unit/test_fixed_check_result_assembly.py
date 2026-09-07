from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from test_investigation_validation import _agent_results
from test_multi_investigator_team import snapshot
from test_single_agent_strategy import context

from jindiao.application.result_assembler import ResultAssembler
from jindiao.contracts.evidence import CoverageSummary
from jindiao.contracts.execution import ExecutionCost
from jindiao.contracts.results import CollaborationSummary, OrchestrationMode
from jindiao.orchestration.base import OrchestrationOutcome
from jindiao.risk import RiskRuleEngine, RiskRuleSet

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def test_result_assembler_projects_agent_checks_to_versioned_report_catalog() -> None:
    frozen = snapshot()
    agent_results = _agent_results()
    outcome = OrchestrationOutcome(
        subject=frozen.subject,
        findings=(),
        evidence=frozen.evidence,
        coverage=CoverageSummary.from_items([]),
        review_issues=(),
        review_completed=True,
        section_data={},
        agent_trace=(),
        collaboration=CollaborationSummary(
            agent_count=6,
            task_count=15,
            parallel_task_count=4,
            conflicts_detected=0,
            repairs_requested=0,
            repairs_completed=0,
        ),
        errors=(),
        tool_calls=0,
    )
    investigation_cost = ExecutionCost(
        llm_requests=18,
        successful_llm_requests=18,
        provider_usage_requests=18,
        input_tokens=1800,
        output_tokens=600,
        total_tokens=2400,
        tool_calls=24,
        mcp_calls=0,
        schema_retries=0,
        repair_rounds=0,
        wall_time_ms=1200,
    )
    assembler = ResultAssembler(
        rule_engine=RiskRuleEngine(RiskRuleSet.from_file(Path("config/risk-rules-v1.json")))
    )

    result = assembler.assemble(
        context=context(),
        outcome=outcome,
        mode=OrchestrationMode.MULTI,
        started_at=NOW,
        completed_at=NOW,
        snapshot=frozen,
        agent_results=agent_results,
        investigation_cost=investigation_cost,
    )

    assert result.agent_results == agent_results
    assert result.report_structure.module_count == 8
    assert result.report_structure.submodule_count == 48
    assert result.context_snapshot is not None
    assert result.context_snapshot.snapshot_id == frozen.snapshot_id
    assert result.execution_cost.shared_acquisition_cost == frozen.shared_acquisition_cost
    assert result.execution_cost.investigation_cost == investigation_cost

    sections = {item.section_id: item for item in result.sections}
    company_checks = sections["company-profile"].data["checks"]
    operational_checks = sections["operational-risk"].data["checks"]
    assert isinstance(company_checks, dict)
    assert isinstance(operational_checks, dict)
    assert "registration-status-normal" in company_checks
    assert "registration-status-normal" in operational_checks
    submodule_count = 0
    for section in result.sections:
        submodules = section.data.get("submodules")
        if isinstance(submodules, dict):
            submodule_count += len(submodules)
    assert submodule_count == 48
