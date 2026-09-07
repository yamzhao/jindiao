from __future__ import annotations

from datetime import UTC, date, datetime

from jindiao.contracts.evidence import (
    CoverageItem,
    CoverageSummary,
    Evidence,
    SourceStatus,
    SourceType,
)
from jindiao.contracts.investigation import (
    Finding,
    FindingStatus,
    ReviewIssue,
    RiskClass,
    Severity,
)
from jindiao.risk import QualityCalculator

REPORT_AS_OF = date(2026, 8, 31)
QUERIED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def evidence(evidence_id: str, *, as_of_date: date | None = REPORT_AS_OF) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        claim="企业存在一条经营异常记录",
        value=True,
        subject_id="tyc:22822",
        source_type=SourceType.TIANYANCHA,
        source_tool="operation_abnormal",
        source_record_id="record-1",
        queried_at=QUERIED_AT,
        as_of_date=as_of_date,
        confidence=0.9,
        is_mock=False,
        supports_fields=("operations.abnormal",),
        raw_ref="artifact://run-1/operations.json#record-1",
    )


def finding(*evidence_ids: str) -> Finding:
    return Finding(
        finding_id="finding-1",
        subject_id="tyc:22822",
        domain="operations",
        claim="企业存在经营异常记录",
        risk_class=RiskClass.ATTENTION,
        severity=Severity.MEDIUM,
        status=FindingStatus.ACCEPTED,
        evidence_ids=evidence_ids,
    )


def test_quality_calculator_reports_per_domain_coverage_and_full_confidence() -> None:
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain="judicial",
                capability="dishonest",
                status=SourceStatus.VERIFIED_EMPTY,
            ),
            CoverageItem(
                domain="operations",
                capability="operation_abnormal",
                status=SourceStatus.VERIFIED_RECORDS,
                record_count=1,
            ),
        ]
    )

    assessment = QualityCalculator().assess(
        coverage=coverage,
        findings=(finding("ev-1"),),
        evidence=(evidence("ev-1"),),
        review_issues=(),
        report_as_of=REPORT_AS_OF,
    )

    assert assessment.coverage_ratio == 1
    assert assessment.evidence_completeness_ratio == 1
    assert assessment.freshness_ratio == 1
    assert assessment.unresolved_conflict_count == 0
    assert assessment.decision_confidence == 1
    assert [(item.domain, item.ratio) for item in assessment.domain_coverage] == [
        ("judicial", 1),
        ("operations", 1),
    ]
    assert assessment.pending_review_items == ()


def test_source_gaps_stale_evidence_and_conflicts_reduce_confidence() -> None:
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain="judicial",
                capability="dishonest",
                status=SourceStatus.SOURCE_ERROR,
                error="upstream timeout",
            ),
            CoverageItem(
                domain="operations",
                capability="operation_abnormal",
                status=SourceStatus.VERIFIED_RECORDS,
                record_count=1,
            ),
        ]
    )
    conflict = ReviewIssue(
        issue_id="conflict-1",
        issue_type="evidence_conflict",
        message="工商状态存在冲突",
        evidence_ids=("ev-1",),
    )

    assessment = QualityCalculator(freshness_days=365).assess(
        coverage=coverage,
        findings=(finding("ev-missing"),),
        evidence=(evidence("ev-1", as_of_date=date(2024, 1, 1)),),
        review_issues=(conflict,),
        report_as_of=REPORT_AS_OF,
    )

    assert assessment.coverage_ratio == 0.5
    assert assessment.evidence_completeness_ratio == 0
    assert assessment.freshness_ratio == 0
    assert assessment.unresolved_conflict_count == 1
    assert assessment.decision_confidence == 0.2
    assert assessment.domain_coverage[0].domain == "judicial"
    assert assessment.domain_coverage[0].ratio == 0
    assert assessment.pending_review_items == (
        "source_gap:judicial:dishonest:source_error",
        "conflict-1",
    )


def test_empty_verified_sources_are_current_without_record_evidence() -> None:
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain="judicial",
                capability="dishonest",
                status=SourceStatus.VERIFIED_EMPTY,
            )
        ]
    )

    assessment = QualityCalculator().assess(
        coverage=coverage,
        findings=(),
        evidence=(),
        review_issues=(),
        report_as_of=REPORT_AS_OF,
    )

    assert assessment.evidence_completeness_ratio == 1
    assert assessment.freshness_ratio == 1
    assert assessment.decision_confidence == 1
