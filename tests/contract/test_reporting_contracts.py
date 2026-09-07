from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageSummary
from jindiao.contracts.reporting import (
    Decision,
    DecisionBand,
    ReportSection,
    ReportSectionStatus,
    ReportViewModel,
    RiskSummary,
    RuleHit,
)


def make_subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:123",
        company_name="示例科技有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=datetime(2026, 9, 3, tzinfo=UTC),
    )


def make_decision() -> Decision:
    hit = RuleHit(
        rule_id="attention.execution",
        finding_id="finding-1",
        evidence_ids=("ev-1",),
        points=20,
        explanation="存在被执行记录",
    )
    return Decision(
        band=DecisionBand.MANUAL_REVIEW,
        score=20,
        confidence=0.9,
        rule_version="v1",
        rule_hits=(hit,),
        major_risk_finding_ids=("finding-1",),
        pending_review_items=(),
        as_of_date=date(2026, 9, 3),
    )


def test_rule_hit_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        RuleHit(
            rule_id="bad",
            finding_id="finding-1",
            evidence_ids=(),
            points=10,
            explanation="缺少证据",
        )


def test_decision_score_must_equal_rule_hit_sum() -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(make_decision().model_dump() | {"score": 30})


def test_report_view_requires_unique_section_ids() -> None:
    section = ReportSection(
        section_id="risk-summary",
        title="风险摘要",
        status=ReportSectionStatus.COMPLETE,
        coverage=1,
        finding_ids=("finding-1",),
        evidence_ids=("ev-1",),
        data={"score": 20},
    )
    summary = RiskSummary(
        admission_count=0,
        attention_count=1,
        non_risk_count=0,
        total_score=20,
        top_finding_ids=("finding-1",),
    )
    values = {
        "subject": make_subject(),
        "decision": make_decision(),
        "risk_summary": summary,
        "coverage": CoverageSummary.from_items([]),
        "sections": [section],
        "generated_at": datetime(2026, 9, 3, tzinfo=UTC),
        "source_disclosure": ["企业公开信息来自天眼查 MCP"],
    }

    view = ReportViewModel.model_validate(values)
    assert view.sections[0].section_id == "risk-summary"

    with pytest.raises(ValidationError):
        ReportViewModel.model_validate(values | {"sections": [section, section]})
