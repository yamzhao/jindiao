from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import SourceStatus, SourceType
from jindiao.tianyancha import (
    EvidenceDomain,
    McpCallResult,
    TianyanchaEvidenceNormalizer,
)
from jindiao.tianyancha.normalizer import NormalizedEvidenceBatch

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


def _recorded_shapes() -> dict[str, object]:
    value = json.loads(
        Path("tests/fixtures/tianyancha/product-response-shapes.json").read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def test_product_markdown_shape_counts_business_rows_and_preserves_unit_scope() -> None:
    shape = _recorded_shapes()["markdown_detail"]
    assert isinstance(shape, dict)
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_financial_summary",
        result=McpCallResult.model_validate({"text": [shape["text"]]}),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/financial.md",
    )

    assert batch.record_count == 2
    assert batch.amount_multiplier == 10_000
    assert batch.statement_scope == "consolidated"
    assert batch.evidence[0].value == {"年度": "2025", "营业收入": "1200", "净利润": "80"}


def _normalize_financial_tables(text: str) -> NormalizedEvidenceBatch:
    return TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_financial_summary",
        result=McpCallResult(text=(text,)),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://test/multiple-tables.md",
    )


@pytest.mark.parametrize("separator", ["\n\n财务明细\n\n", "\n"])
def test_all_markdown_tables_keep_their_own_headers_and_skip_summary_count(separator: str) -> None:
    batch = _normalize_financial_tables(
        "| 字段 | 值 |\n| --- | --- |\n| 总数 | 2 |"
        + separator
        + "| 年度 | 营业收入 |\n| --- | ---: |\n| 2025 | 1200 |"
        + "\n\n| 年度 | 营业收入 | 净利润 |\n| --- | --- | --- |\n| 2024 | 1000 | 60 |"
    )
    assert batch.record_count == 2
    assert [e.value for e in batch.evidence] == [
        {"年度": "2025", "营业收入": "1200"},
        {"年度": "2024", "营业收入": "1000", "净利润": "60"},
    ]
    assert batch.pagination is not None and batch.pagination.total_count == 2
    assert not batch.pagination.is_truncated(returned_count=2)


def test_markdown_empty_or_malformed_first_table_does_not_hide_later_detail() -> None:
    batch = _normalize_financial_tables(
        "| 空表 |\n| --- |\n\n无数据\n\n"
        "| 错误表 |\n| --- |\n| 不匹配 | 两列 |\n\n"
        "| 年度 | 营业收入 |\n| --- | --- |\n| 2025 | 0 |"
    )
    assert batch.record_count == 1
    assert batch.evidence[0].value == {"年度": "2025", "营业收入": "0"}


def test_markdown_escaped_pipe_is_preserved_as_one_cell() -> None:
    batch = _normalize_financial_tables("| 年度 | 说明 |\n| --- | --- |\n| 2025 | A\\|B |")
    assert batch.record_count == 1
    assert batch.evidence[0].value == {"年度": "2025", "说明": "A|B"}


@pytest.mark.parametrize("total", [0, 10])
def test_count_only_table_is_metadata_not_a_verified_financial_record(total: int) -> None:
    batch = _normalize_financial_tables(f"| 字段 | 值 |\n| --- | --- |\n| 总数 | {total} |")
    assert batch.record_count == 0
    assert batch.pagination is not None and batch.pagination.total_count == total
    assert ("count_only" in batch.partial_reasons) is (total > 0)
    assert batch.evidence[0].value == {"total_count": total}


def test_multi_table_financial_records_reach_product_projection() -> None:
    from jindiao.contracts.evidence import CoverageSummary
    from jindiao.reporting.product_fact_projector import ProductFactProjector

    batch = _normalize_financial_tables(
        "单位：万元；口径：合并\n"  # noqa: RUF001 - actual provider punctuation
        "| 字段 | 值 |\n| --- | --- |\n| 总数 | 1 |\n\n"
        "| 年度 | 营业收入 | 净利润 |\n| --- | --- | --- |\n| 2025 | 1200 | 80 |"
    )
    facts = ProductFactProjector().project(
        subject=subject(),
        evidence=batch.evidence,
        findings=(),
        coverage=CoverageSummary.from_items([]),
        section_data={
            "financial_summary": {
                "records": [e.value for e in batch.evidence],
                "source_metadata": {
                    "amount_multiplier": batch.amount_multiplier,
                    "statement_scope": batch.statement_scope,
                },
            }
        },
    )
    assert len(facts.report.financial_analysis.periods) == 1
    period = facts.report.financial_analysis.periods[0]
    assert period.income_statement.revenue == 12_000_000
    assert period.income_statement.net_profit == 800_000
    assert period.entity_scope == "consolidated"


@pytest.mark.parametrize("total", [0, 1])
@pytest.mark.parametrize("wrapped", [True, False])
def test_count_table_cannot_replace_structured_business_details(total: int, wrapped: bool) -> None:
    detail = {"year": 2025, "revenue": 1200}
    structured = {"items": [detail]} if wrapped else {"result": detail}
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_financial_summary",
        result=McpCallResult.model_validate(
            {
                "structured_content": structured,
                "text": [f"| 字段 | 值 |\n| --- | --- |\n| 总数 | {total} |"],
            }
        ),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://test/mixed",
    )
    assert batch.record_count == 1
    assert batch.evidence[0].value == detail
    assert "count_only" not in batch.partial_reasons


def test_markdown_total_fills_missing_pagination_field_without_losing_page() -> None:
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_financial_summary",
        result=McpCallResult.model_validate(
            {
                "structured_content": {"page": 1, "page_size": 1},
                "text": [
                    "| 字段 | 值 |\n| --- | --- |\n| 总数 | 2 |\n\n"
                    "| 年度 | 营业收入 |\n| --- | --- |\n| 2025 | 1200 |"
                ],
            }
        ),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://test/pagination",
    )
    assert batch.record_count == 1
    assert batch.pagination is not None
    assert batch.pagination.page == 1 and batch.pagination.page_size == 1
    assert batch.pagination.total_count == 2
    assert batch.pagination.is_truncated(returned_count=1)


@pytest.mark.parametrize(
    "metadata",
    [
        {"page_no": 1, "limit": 20},
        {"available_years": [2025, 2024, 2023]},
        {"url": "https://example.invalid/snapshot.png"},
    ],
)
def test_structured_metadata_cannot_turn_count_only_into_a_business_record(
    metadata: dict[str, object],
) -> None:
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_financial_summary",
        result=McpCallResult.model_validate(
            {
                "structured_content": metadata,
                "text": ["| 字段 | 值 |\n| --- | --- |\n| 总数 | 1 |"],
            }
        ),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://test/count-metadata",
    )
    assert batch.record_count == 0
    assert "count_only" in batch.partial_reasons
    assert batch.evidence[0].value == {"total_count": 1}


def test_year_directory_is_partial_metadata_and_not_a_cashflow_record() -> None:
    shape = _recorded_shapes()["year_directory"]
    assert isinstance(shape, dict)
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.OPERATIONS,
        subject=subject(),
        tool_name="get_cash_flow_statement",
        result=McpCallResult.model_validate(shape),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/cash-flow.json",
    )

    assert batch.record_count == 0
    assert batch.available_years == (2025, 2023)
    assert batch.selected_years == (2025, 2023)
    assert set(batch.partial_reasons) == {"year_directory_only", "missing_period"}


def test_link_only_shape_is_preserved_without_inventing_equity_nodes() -> None:
    shape = _recorded_shapes()["link_only"]
    assert isinstance(shape, dict)
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.GOVERNANCE,
        subject=subject(),
        tool_name="get_relation_graph",
        result=McpCallResult.model_validate(shape),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/equity.json",
    )

    assert batch.record_count == 0
    assert batch.links == ("https://example.invalid/equity/snapshot.png",)
    assert batch.partial_reasons == ("link_only",)


def test_saved_pagination_shape_uses_actual_rows_and_reports_truncation() -> None:
    shape = _recorded_shapes()["paginated"]
    assert isinstance(shape, dict)
    batch = TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.JUDICIAL,
        subject=subject(),
        tool_name="get_judicial_documents",
        result=McpCallResult.model_validate(shape),
        queried_at=NOW,
        as_of_date=AS_OF,
        raw_snapshot_ref="artifact://run-1/judicial.json",
    )

    assert batch.record_count == 1
    assert batch.pagination is not None
    assert batch.pagination.total_count == 51
    assert batch.pagination.is_truncated(returned_count=batch.record_count)
