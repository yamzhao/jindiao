from __future__ import annotations

# ruff: noqa: RUF001 -- user-facing Chinese demo disclosure
from pathlib import Path

import pytest
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

    # Demo reporting is explicit and preserves the fact that no review happened.
    partial = outcome.model_copy(
        update={
            "review_completed": False,
            "demo_partial_disclosure": "演示降级：审核未完成；财务核查待人工核验。",
        }
    )
    reviewed_partial = assembler.prepare(
        context=context(),
        outcome=partial,
        snapshot=frozen,
        agent_results=agent_results[:1],
    )
    assert reviewed_partial.incomplete
    assert reviewed_partial.demo_partial_disclosure == partial.demo_partial_disclosure
    assert len(reviewed_partial.checks) == len(agent_results[0].check_results)
    from jindiao.contracts.product import ProductReport, ProductSubject
    from jindiao.contracts.product_facts import ProductFactBundle
    from jindiao.contracts.results import OrchestrationMode
    from jindiao.reporting.product_assembler import ProductReportAssembler

    product, _ = ProductReportAssembler().finish(
        facts=ProductFactBundle(report=ProductReport(), evidence=()),
        risks=(),
        reviewed=reviewed_partial,
        context=context(),
        subject=ProductSubject(
            subject_id=frozen.subject.subject_id, company_name=frozen.subject.company_name
        ),
        mode=OrchestrationMode.MULTI,
        generated_at=frozen.created_at,
    )
    assert product.meta.status.value == "partial"
    assert product.summary.ai_suggestion == "manual_review"
    assert "审核未完成" in product.summary.ai_suggestion_reason
    assert "审核未完成" in product.report_markdown
    with pytest.raises(ValueError, match="before review"):
        assembler.prepare(
            context=context(),
            outcome=outcome.model_copy(update={"review_completed": False}),
            snapshot=frozen,
            agent_results=agent_results,
        )
