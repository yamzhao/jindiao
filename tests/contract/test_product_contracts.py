import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from jindiao.contracts.business import BusinessContext
from jindiao.contracts.product import (
    EvidenceTag,
    ProductEvidence,
    ProductMeta,
    ProductReport,
    ProductResult,
    ProductSubject,
    ProductSummary,
    RiskFinding,
    RiskPoints,
)
from jindiao.contracts.results import DueDiligenceRequest, OrchestrationMode, RunStatus


def test_request_defaults_and_business_values() -> None:
    base = {"enterprise": {"company_name": "契约测试企业"}}
    first = DueDiligenceRequest.model_validate(base)
    second = DueDiligenceRequest.model_validate({**base, "business_context": {}})
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.business_context.application_amount is None
    supplied = DueDiligenceRequest.model_validate(
        {**base, "business_context": {"application_amount": 50000000}}
    )
    assert supplied.business_context.application_amount == 50000000
    assert first.model_dump(mode="json") != supplied.model_dump(mode="json")


def test_invalid_amount_and_unknown_input_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BusinessContext(application_amount=-1)
    with pytest.raises(ValidationError):
        BusinessContext.model_validate({"unknown": "value"})
    with pytest.raises(ValidationError):
        BusinessContext(application_amount=float("nan"))


def test_empty_report_retains_every_prototype_section() -> None:
    report = ProductReport().model_dump(mode="json")
    assert tuple(report) == (
        "business_plan",
        "company_profile",
        "ownership",
        "business_analysis",
        "financial_analysis",
        "bank_flow_analysis",
        "external_verification",
        "risk_points",
    )
    assert report["business_plan"]["application_amount"] is None
    assert report["bank_flow_analysis"]["total_inflow"] is None
    assert report["risk_points"] == {"finding_ids": []}


def test_result_checks_risk_counts_and_references() -> None:
    result = ProductResult(
        meta=ProductMeta(
            request_id="req",
            run_id="run",
            status=RunStatus.PARTIAL,
            mode=OrchestrationMode.MULTI,
            generated_at=datetime(2026, 9, 7, tzinfo=UTC),
            report_as_of=date(2026, 9, 7),
        ),
        subject=ProductSubject(subject_id="test", company_name="测试企业"),
        report_markdown="# 测试报告",
    )
    raw = result.model_dump(mode="json")
    assert set(raw) == {
        "schema_version",
        "meta",
        "subject",
        "summary",
        "report",
        "risk_findings",
        "evidence",
        "report_markdown",
    }
    raw["summary"]["risk_count"] = 1
    with pytest.raises(ValidationError, match="risk count"):
        ProductResult.model_validate(raw)
    raw["summary"]["risk_count"] = 0
    raw["report"]["ownership"]["evidence_ids"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown evidence"):
        ProductResult.model_validate(raw)


def test_simulated_historical_case_does_not_mark_real_facts_as_mock() -> None:
    evidence = ProductEvidence(
        id="evidence-real",
        source_type="tianyancha",
        source_label="天眼查·司法案件",
        summary="查得一项尚未解除的执行事项",
        source_ref="mcp://judicial-case/1",
        queried_at=datetime(2026, 9, 7, tzinfo=UTC),
    )
    finding = RiskFinding(
        id="risk-one",
        title="执行事项尚未解除",
        source_kind="fact",
        risk_fact="查得一项尚未解除的执行事项",
        evidence_tags=(EvidenceTag(evidence_id=evidence.id, label=evidence.source_label),),
        historical_case="模拟案例" + "\uff1a" + "同类企业因账户受限而延迟回款。",
        historical_case_is_mock=True,
    )
    result = ProductResult(
        meta=ProductMeta(
            request_id="req",
            run_id="run",
            status=RunStatus.PARTIAL,
            mode=OrchestrationMode.MULTI,
            generated_at=datetime(2026, 9, 7, tzinfo=UTC),
            report_as_of=date(2026, 9, 7),
            is_mock=False,
        ),
        subject=ProductSubject(subject_id="test", company_name="测试企业"),
        report=ProductReport(risk_points=RiskPoints(finding_ids=(finding.id,))),
        summary=ProductSummary(risk_count=1),
        risk_findings=(finding,),
        evidence=(evidence,),
        report_markdown="# 测试报告",
    )

    assert result.meta.is_mock is False
    assert result.risk_findings[0].historical_case_is_mock is True


@pytest.mark.parametrize(
    ("filename", "business_status", "bank_status"),
    (
        ("product-result-full-input.json", "complete", "partial"),
        ("product-result-missing-input.json", "unavailable", "partial"),
    ),
)
def test_documented_product_result_samples_are_exact_contract_outputs(
    filename: str,
    business_status: str,
    bank_status: str,
) -> None:
    path = Path("docs/api/samples") / filename
    payload = json.loads(path.read_text(encoding="utf-8"))

    result = ProductResult.model_validate(payload)

    assert result.model_dump(mode="json") == payload
    assert result.report.business_plan.status == business_status
    assert result.report.bank_flow_analysis.status == bank_status
    assert result.summary.ai_suggestion == "manual_review"
