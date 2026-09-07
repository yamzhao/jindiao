from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    CoverageItem,
    CoverageSummary,
    Evidence,
    EvidenceReference,
    SourceStatus,
    SourceType,
)


def make_evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "evidence_id": "ev-1",
        "claim": "企业登记状态",
        "value": "存续",
        "subject_id": "tyc:123",
        "source_type": SourceType.TIANYANCHA,
        "source_tool": "get_company_registration_info",
        "source_record_id": "record-1",
        "queried_at": datetime(2026, 9, 3, tzinfo=UTC),
        "as_of_date": date(2026, 9, 3),
        "confidence": 1,
        "is_mock": False,
        "supports_fields": ["company.registration_status"],
        "raw_ref": "tyc://get_company_registration_info/record-1",
    }
    values.update(overrides)
    return Evidence.model_validate(values)


def test_tianyancha_evidence_is_source_aware_and_referenceable() -> None:
    evidence = make_evidence()
    reference = EvidenceReference(
        evidence_id=evidence.evidence_id,
        supports_fields=evidence.supports_fields,
    )

    assert evidence.source_type is SourceType.TIANYANCHA
    assert evidence.is_mock is False
    assert reference.supports_fields == ("company.registration_status",)


def test_mock_evidence_requires_mock_flag_and_mock_uri() -> None:
    with pytest.raises(ValidationError):
        make_evidence(source_type=SourceType.MOCK, is_mock=False)

    with pytest.raises(ValidationError):
        make_evidence(source_type=SourceType.MOCK, is_mock=True, raw_ref="file://scenario.json")

    evidence = make_evidence(
        source_type=SourceType.MOCK,
        source_tool=None,
        is_mock=True,
        raw_ref="mock://normal/v1/company.json#registration_status",
    )
    assert evidence.is_mock is True


def test_tianyancha_evidence_cannot_be_marked_as_mock() -> None:
    with pytest.raises(ValidationError):
        make_evidence(is_mock=True)


def test_verified_empty_coverage_has_zero_records() -> None:
    item = CoverageItem(
        domain="judicial",
        capability="dishonest_info",
        status=SourceStatus.VERIFIED_EMPTY,
        record_count=0,
    )

    assert item.record_count == 0
    with pytest.raises(ValidationError):
        CoverageItem(
            domain="judicial",
            capability="dishonest_info",
            status=SourceStatus.VERIFIED_EMPTY,
            record_count=1,
        )


def test_source_status_and_coverage_completeness_are_independent() -> None:
    item = CoverageItem(
        domain="judicial",
        capability="get_judicial_documents",
        status=SourceStatus.VERIFIED_RECORDS,
        record_count=20,
        completeness=CoverageCompleteness.PARTIAL,
        gap_reasons=(CoverageGapReason.PAGINATION_TRUNCATED,),
    )

    assert item.status is SourceStatus.VERIFIED_RECORDS
    assert item.completeness is CoverageCompleteness.PARTIAL
    assert item.gap_reasons == (CoverageGapReason.PAGINATION_TRUNCATED,)


def test_complete_coverage_rejects_gap_reasons() -> None:
    with pytest.raises(ValidationError):
        CoverageItem(
            domain="governance",
            capability="get_shareholder_info",
            status=SourceStatus.VERIFIED_RECORDS,
            record_count=1,
            completeness=CoverageCompleteness.COMPLETE,
            gap_reasons=(CoverageGapReason.MISSING_FIELDS,),
        )


def test_public_web_evidence_requires_traceable_non_mock_metadata() -> None:
    evidence = make_evidence(
        source_type=SourceType.PUBLIC_WEB,
        source_tool="deepsearch.web_fetch",
        source_record_id="sha256:" + "a" * 64,
        raw_ref="https://example.gov.cn/notices/1",
        source_title="行政处罚决定书",
        source_publisher="示例监管机构",
        content_hash="sha256:" + "a" * 64,
    )

    assert evidence.source_type is SourceType.PUBLIC_WEB
    assert evidence.is_mock is False

    with pytest.raises(ValidationError):
        make_evidence(
            source_type=SourceType.PUBLIC_WEB,
            raw_ref="mock://scenario/v1/corpus/a.md#chunk-0001",
            content_hash="sha256:" + "a" * 64,
        )

    with pytest.raises(ValidationError):
        make_evidence(
            source_type=SourceType.PUBLIC_WEB,
            raw_ref="https://example.gov.cn/notices/1",
            content_hash=None,
        )


def test_coverage_summary_counts_verified_and_degraded_items() -> None:
    items = [
        CoverageItem(
            domain="identity",
            capability="registration",
            status=SourceStatus.VERIFIED_RECORDS,
            record_count=1,
        ),
        CoverageItem(
            domain="judicial",
            capability="dishonest_info",
            status=SourceStatus.VERIFIED_EMPTY,
            record_count=0,
        ),
        CoverageItem(
            domain="operations",
            capability="industry_history",
            status=SourceStatus.CAPABILITY_ABSENT,
            record_count=0,
        ),
        CoverageItem(
            domain="operations",
            capability="news",
            status=SourceStatus.SOURCE_ERROR,
            record_count=0,
        ),
    ]

    summary = CoverageSummary.from_items(items)

    assert summary.total_items == 4
    assert summary.completed_items == 2
    assert summary.ratio == 0.5
    assert summary.status_counts[SourceStatus.SOURCE_ERROR] == 1
