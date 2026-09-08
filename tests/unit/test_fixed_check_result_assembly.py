from __future__ import annotations

from pathlib import Path

from test_investigation_validation import _agent_results
from test_multi_investigator_team import snapshot
from test_single_agent_strategy import context

from jindiao.application.result_assembler import ResultAssembler
from jindiao.contracts.evidence import CoverageSummary
from jindiao.contracts.results import CollaborationSummary
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration.base import OrchestrationOutcome
from jindiao.risk import RiskRuleEngine, RiskRuleSet


def test_result_assembler_prepares_reviewed_inputs_without_legacy_public_result() -> None:
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
    assembler = ResultAssembler(
        rule_engine=RiskRuleEngine(RiskRuleSet.from_file(Path("config/risk-rules-v1.json")))
    )

    reviewed = assembler.prepare(
        context=context(),
        outcome=outcome,
        snapshot=frozen,
        agent_results=agent_results,
    )

    assert tuple(item.check_id for item in reviewed.checks) == CHECK_CATALOG.check_ids
    assert reviewed.evidence == frozen.evidence
    assert reviewed.incomplete is True
    assert not hasattr(reviewed, "report_structure")
