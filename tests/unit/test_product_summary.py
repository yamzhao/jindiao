# ruff: noqa: RUF001
from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest
from test_single_agent_strategy import context

from jindiao.contracts.evidence import CoverageSummary
from jindiao.contracts.product import (
    CheckLabel,
    EvidenceTag,
    MissingField,
    ProductEvidence,
    ProductReport,
    ProductResult,
    ProductSubject,
    ProductSummary,
    RiskFinding,
    RiskPoints,
)
from jindiao.contracts.product_facts import ProductFactBundle
from jindiao.contracts.report_inputs import ReviewedReportInputs
from jindiao.contracts.report_policy import ReportingPolicyBinding, ReportPolicy
from jindiao.contracts.reporting import Decision, DecisionBand
from jindiao.contracts.results import OrchestrationMode
from jindiao.investigation import CHECK_CATALOG
from jindiao.reporting.product_assembler import ProductReportAssembler
from jindiao.reporting.product_markdown import ProductMarkdownRenderer, ProductReportView
from jindiao.reporting.replay import ReplaySnapshot, replay


def assemble(
    titles: tuple[str, ...] = (),
    *,
    band: DecisionBand = DecisionBand.MANUAL_REVIEW,
    incomplete: bool = True,
    disclosure: str | None = None,
    check_id: str | None = None,
) -> tuple[ProductResult, ProductReportView]:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    evidence = ProductEvidence(
        id="evidence-one",
        source_type="tianyancha",
        source_label="天眼查·企业核查",
        summary="已核实的企业风险资料",
        source_ref="mcp://enterprise",
        queried_at=now,
    )
    risks = tuple(
        RiskFinding(
            id=f"risk-{index}",
            title=title,
            risk_fact=title,
            source_kind="check" if check_id else "fact",
            check_items=(CheckLabel(id=check_id, label=check_id),) if check_id else (),
            evidence_tags=(EvidenceTag(evidence_id=evidence.id, label=evidence.source_label),),
        )
        for index, title in enumerate(titles)
    )
    report = ProductReport()
    report = report.model_copy(
        update={
            name: getattr(report, name).model_copy(update={"status": "complete"})
            for name in tuple(ProductReport.model_fields)[:-1]
        }
    )
    if incomplete:
        report = report.model_copy(
            update={
                "financial_analysis": report.financial_analysis.model_copy(
                    update={
                        "status": "partial",
                        "missing_fields": (
                            MissingField(
                                field="periods",
                                reason="not_provided",
                                message="缺少财务报表",
                            ),
                        ),
                    }
                )
            }
        )
    reviewed = ReviewedReportInputs(
        decision=Decision(
            band=band,
            score=0,
            confidence=0.8,
            rule_version="v1",
            rule_hits=(),
            major_risk_finding_ids=(),
            pending_review_items=(),
            as_of_date=date(2026, 9, 9),
        ),
        findings=(),
        evidence=(),
        coverage=CoverageSummary.from_items([]),
        incomplete=incomplete,
        demo_partial_disclosure=disclosure,
    )
    return ProductReportAssembler().finish(
        facts=ProductFactBundle(report=report, evidence=(evidence,)),
        risks=risks,
        reviewed=reviewed,
        context=context(),
        subject=ProductSubject(subject_id="subject", company_name="测试企业"),
        mode=OrchestrationMode.MULTI,
        generated_at=now,
    )


def assert_short_chinese(text: str) -> None:
    assert not re.search(r"[A-Za-z]", text)
    assert 1 <= len([part for part in re.split(r"[。！？]", text) if part.strip()]) <= 2
    assert len(text) <= 280


def test_summary_combines_customer_risks_followup_and_credit_strategy() -> None:
    result, _ = assemble(("登记事项集中变更", "实际控制人穿透不明"))
    reason = result.summary.ai_suggestion_reason
    assert "登记事项集中变更" in reason
    assert "实际控制人穿透不明" in reason
    assert "核验" in reason
    assert "暂缓新增授信" in reason
    assert "额度" in reason
    assert_short_chinese(reason)
    assert reason in result.report_markdown


def test_summary_does_not_copy_runtime_diagnostics_or_english_check_ids() -> None:
    disclosure = (
        "演示降级：审核未完成；未完成核查：registration-change-anomaly, "
        "ownership-control-risk；未追加报告模型请求。"
    )
    result, _ = assemble(("实际控制人穿透不明",), disclosure=disclosure)
    reason = result.summary.ai_suggestion_reason
    assert "实际控制人穿透不明" in reason
    assert "核查尚未完成" in reason
    assert "演示降级" not in reason
    assert "未追加" not in reason
    assert_short_chinese(reason)


@pytest.mark.parametrize("check_id", ["material-execution-risk", None])
def test_english_risk_text_uses_chinese_check_label_or_neutral_fallback(
    check_id: str | None,
) -> None:
    result, _ = assemble(("pending execution risk",), check_id=check_id)
    assert_short_chinese(result.summary.ai_suggestion_reason)
    assert "核验" in result.summary.ai_suggestion_reason
    expected = CHECK_CATALOG.get(check_id).title if check_id else "需进一步核实的风险事项"
    assert expected in result.summary.ai_suggestion_reason


def test_zero_risks_with_gaps_is_not_presented_as_clean_credit() -> None:
    result, _ = assemble()
    reason = result.summary.ai_suggestion_reason
    assert "未发现已审核风险" in reason
    assert "财务" in reason
    assert "不代表" in reason
    assert "暂缓新增授信" in reason
    assert_short_chinese(reason)


@pytest.mark.parametrize(
    ("band", "expected"),
    [(DecisionBand.PASS, "按审批流程"), (DecisionBand.REJECT, "暂不新增授信")],
)
def test_summary_credit_strategy_respects_existing_decision(
    band: DecisionBand,
    expected: str,
) -> None:
    result, _ = assemble(incomplete=False, band=band)
    assert expected in result.summary.ai_suggestion_reason
    assert "核验" in result.summary.ai_suggestion_reason
    assert_short_chinese(result.summary.ai_suggestion_reason)


def test_five_risks_are_consistent_in_result_markdown_and_version_comparison() -> None:
    result, view = assemble(
        ("登记事项集中变更", "实际控制人穿透不明", "股权高度集中", "现金流承压", "短期偿债承压")
    )
    assert result.summary.risk_count == len(result.risk_findings) == 5
    assert len(result.report.risk_points.finding_ids) == 5
    assert "风险点：5 个" in result.report_markdown
    assert_short_chinese(result.summary.ai_suggestion_reason)
    snapshot = ReplaySnapshot.freeze(
        run_id=result.meta.run_id,
        view=view,
        binding=ReportingPolicyBinding.freeze(ReportPolicy(), version="1.1.0", revision=0),
        report=result.report_markdown,
    )
    evaluated = replay(snapshot, target_section_ids=("financial_analysis",))
    assert evaluated.passed
    for markdown in (evaluated.cases[0].before, evaluated.cases[0].after):
        assert "风险点：5 个" in markdown
        assert "风险点：0 个" not in markdown
        for risk in result.risk_findings:
            assert f"### {risk.title}" in markdown


@pytest.mark.parametrize("mutation", ["count", "cards"])
def test_markdown_renderer_rejects_stale_summary_or_risk_references(mutation: str) -> None:
    _, view = assemble(("现金流承压",))
    if mutation == "count":
        view = view.model_copy(update={"summary": ProductSummary(risk_count=0)})
    else:
        view = view.model_copy(
            update={"report": view.report.model_copy(update={"risk_points": RiskPoints()})}
        )
    with pytest.raises(ValueError, match=r"risk count|section 7"):
        ProductMarkdownRenderer().render(view)


def test_result_contract_rejects_markdown_count_from_another_result() -> None:
    result, _ = assemble(("现金流承压",))
    payload = result.model_dump(mode="json")
    payload["report_markdown"] = result.report_markdown.replace("风险点：1 个", "风险点：0 个")
    with pytest.raises(ValueError, match="Markdown risk count"):
        ProductResult.model_validate(payload)
