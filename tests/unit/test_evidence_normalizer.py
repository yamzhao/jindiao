from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus, SourceType
from jindiao.tianyancha import (
    EvidenceDomain,
    McpCallResult,
    TianyanchaEvidenceNormalizer,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)
AS_OF = date(2026, 8, 31)


def subject() -> ResolvedSubject:
    return ResolvedSubject(
        subject_id="tyc:22822",
        company_name="示例科技有限公司",
        source=SubjectSource.TIANYANCHA,
        resolved_at=NOW,
    )


@pytest.mark.parametrize(
    ("domain", "record", "expected_field"),
    [
        (
            EvidenceDomain.GOVERNANCE,
            {"id": "holder-1", "shareholderName": "某投资公司", "percent": "60%"},
            "governance.shareholders",
        ),
        (
            EvidenceDomain.JUDICIAL,
            {"caseNo": "(2026)京01执1号", "amount": 500000, "caseStatus": "未结"},
            "judicial.cases",
        ),
        (
            EvidenceDomain.OPERATIONS,
            {"id": "abnormal-1", "abnormalReason": "未按期公示年报"},
            "operations.abnormal_operations",
        ),
        (
            EvidenceDomain.PEERS,
            {"id": "peer-1", "peerCompanyName": "同行甲有限公司", "rank": 3},
            "peers.companies",
        ),
    ],
)
def test_normalizer_maps_four_domains_to_traceable_evidence(
    domain: EvidenceDomain,
    record: dict[str, object],
    expected_field: str,
) -> None:
    result = McpCallResult.model_validate({"structured_content": {"result": {"items": [record]}}})

    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=domain,
        subject=subject(),
        tool_name="actual_tool_from_manifest",
        result=result,
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/tianyancha/response-1.json",
    )

    assert batch.domain is domain
    assert batch.record_count == 1
    evidence = batch.evidence[0]
    assert evidence.subject_id == "tyc:22822"
    assert evidence.source_type is SourceType.TIANYANCHA
    assert evidence.source_status is SourceStatus.VERIFIED_RECORDS
    assert evidence.source_tool == "actual_tool_from_manifest"
    assert evidence.is_mock is False
    assert expected_field in evidence.supports_fields
    assert evidence.raw_ref.startswith("artifact://run-1/")
    assert "22822" not in evidence.evidence_id


def test_normalizer_preserves_markdown_as_source_evidence_without_inventing_fields() -> None:
    result = McpCallResult(text=("## 企业基础画像\n\n- 经营状态: 存续",))

    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.GOVERNANCE,
        subject=subject(),
        tool_name="get_company_basic_profile",
        result=result,
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/tianyancha/basic.md",
    )

    assert batch.record_count == 1
    assert batch.evidence[0].supports_fields == ("governance.raw",)
    assert batch.evidence[0].value == result.text[0]


def test_normalizer_returns_empty_batch_for_verified_empty_payload() -> None:
    result = McpCallResult.model_validate({"structured_content": {"result": {"items": []}}})

    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.JUDICIAL,
        subject=subject(),
        tool_name="get_execution_cases",
        result=result,
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/tianyancha/execution.json",
    )

    assert batch.record_count == 0
    assert batch.evidence == ()


@pytest.mark.parametrize(
    "envelope",
    [
        {
            "items": [{"id": "case-1"}],
            "page": 1,
            "page_size": 20,
            "total_count": 21,
            "has_more": True,
        },
        {
            "items": [{"id": "case-1"}],
            "pageNum": 1,
            "pageSize": 20,
            "total": 21,
            "hasMore": True,
        },
    ],
)
def test_normalizer_preserves_explicit_pagination_metadata(
    envelope: dict[str, object],
) -> None:
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.JUDICIAL,
        subject=subject(),
        tool_name="get_judicial_documents",
        result=McpCallResult.model_validate({"structured_content": {"result": envelope}}),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/tianyancha/judicial.json",
    )

    assert batch.pagination is not None
    assert batch.pagination.page == 1
    assert batch.pagination.page_size == 20
    assert batch.pagination.total_count == 21
    assert batch.pagination.has_more is True
    assert batch.pagination.is_truncated(returned_count=batch.record_count) is True
