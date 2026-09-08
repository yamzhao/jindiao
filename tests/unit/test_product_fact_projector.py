from datetime import UTC, date, datetime
from typing import cast

import pytest
from pydantic import JsonValue

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageGapReason,
    CoverageItem,
    CoverageSummary,
    SourceStatus,
)
from jindiao.contracts.product import CompanyProfile, MissingField, ProductEvidence, ProductReport
from jindiao.reporting.product_assembler import complete_missing_fields
from jindiao.reporting.product_fact_projector import ProductFactProjector


def _project_registration_rows(rows: list[JsonValue]) -> CompanyProfile:
    return ProductFactProjector().project(
        subject=ResolvedSubject(
            subject_id="tyc:one",
            company_name="测试企业",
            source=SubjectSource.TIANYANCHA,
            resolved_at=datetime(2026, 9, 7, tzinfo=UTC),
        ),
        evidence=(),
        findings=(),
        coverage=CoverageSummary.from_items([]),
        section_data={"registration": {"records": rows}},
    ).report.company_profile


def test_registration_field_value_table_projects_disclosed_facts_without_mutation() -> None:
    import copy

    rows: list[JsonValue] = [
        {"字段": "登记状态", "值": "存续"},
        {"字段": "成立日期", "值": "2000-01-02"},
        {"字段": "法定代表人", "值": "法定代表人甲"},
        {"字段": "注册地址", "值": "北京市朝阳区"},
        {"字段": "注册资本", "值": "500.25万人民币"},
        {"字段": "实缴资本", "值": "200万人民币"},
        {"字段": "行业", "值": "设备制造"},
        {"字段": "经营范围", "值": "制造和销售设备"},
        {"字段": "字段", "值": "值"},
        {"字段": "---", "值": "---"},
    ]
    original = copy.deepcopy(rows)
    profile = _project_registration_rows(rows)
    assert profile.registration_status == "存续"
    assert profile.established_date == date(2000, 1, 2)
    assert profile.legal_representative == "法定代表人甲"
    assert profile.registered_address == "北京市朝阳区"
    assert profile.registered_capital == 5_002_500
    assert profile.paid_in_capital == 2_000_000
    assert profile.capital_currency == "CNY"
    assert profile.industry == "设备制造"
    assert profile.main_business == "制造和销售设备"
    assert rows == original


def test_registration_table_does_not_choose_between_conflicting_values() -> None:
    profile = _project_registration_rows([
        {"字段": "登记状态", "值": "存续"},
        {"字段": "登记状态", "值": "注销"},
        {"字段": "行业", "值": "设备制造"},
        {"字段": "行业", "值": "设备制造"},
    ])
    assert profile.registration_status is None
    assert profile.industry == "设备制造"


@pytest.mark.parametrize("capital", ["100万美元", "未披露", "-", "Infinity", "NaN"])
def test_registration_table_does_not_convert_unknown_or_foreign_capital(capital: str) -> None:
    profile = _project_registration_rows([{"字段": "注册资本", "值": capital}])
    assert profile.registered_capital is None
    assert profile.capital_currency is None


def test_disclosed_scalar_resolves_absent_alternate_capability_but_keeps_source_errors() -> None:
    profile = CompanyProfile(legal_representative="法定代表人甲", evidence_ids=("e",))
    evidence = ProductEvidence(
        id="e", source_type="tianyancha", source_label="工商登记", summary="工商登记",
        source_ref="artifact://registration", queried_at=datetime(2026, 9, 7, tzinfo=UTC),
        supports_fields=("report.company_profile.legal_representative",),
    )
    absent = MissingField(
        field="report.company_profile.legal_representative",
        reason="capability_absent", message="另一个查询能力缺失",
    )
    result = complete_missing_fields(
        ProductReport(company_profile=profile), (evidence,), source_missing_fields=(absent,),
    )
    assert all(g.field != "legal_representative" for g in result.company_profile.missing_fields)
    for reason in ("source_error", "pagination_truncated"):
        result = complete_missing_fields(
            ProductReport(company_profile=profile), (evidence,),
            source_missing_fields=(absent.model_copy(update={"reason": reason}),),
        )
        assert any(g.field == "legal_representative" for g in result.company_profile.missing_fields)
    for missing_profile, missing_evidence in (
        (CompanyProfile(evidence_ids=("e",)), (evidence,)),
        (profile, ()),
    ):
        result = complete_missing_fields(
            ProductReport(company_profile=missing_profile), missing_evidence,
            source_missing_fields=(absent,),
        )
        assert any(g.field == "legal_representative" for g in result.company_profile.missing_fields)


def test_public_missing_fields_preserve_acquisition_outcome_semantics() -> None:
    coverage = CoverageSummary.from_items(
        [
            CoverageItem(
                domain="operations",
                capability="products",
                status=SourceStatus.VERIFIED_EMPTY,
            ),
            CoverageItem(
                domain="operations",
                capability="suppliers_customers",
                status=SourceStatus.CAPABILITY_ABSENT,
            ),
            CoverageItem(
                domain="operations",
                capability="public_opinion",
                status=SourceStatus.SOURCE_ERROR,
                error="timeout",
            ),
            CoverageItem(
                domain="operations",
                capability="financial_summary",
                status=SourceStatus.VERIFIED_RECORDS,
                record_count=1,
                completeness=CoverageCompleteness.PARTIAL,
                gap_reasons=(CoverageGapReason.PAGINATION_TRUNCATED,),
            ),
        ]
    )
    facts = ProductFactProjector().project(
        subject=ResolvedSubject(
            subject_id="tyc:one",
            company_name="测试企业",
            source=SubjectSource.TIANYANCHA,
            resolved_at=datetime(2026, 9, 7, tzinfo=UTC),
        ),
        evidence=(),
        findings=(),
        coverage=coverage,
        section_data={},
    )

    report = complete_missing_fields(
        facts.report,
        facts.evidence,
        verified_empty_fields=facts.verified_empty_fields,
        source_missing_fields=facts.source_missing_fields,
    )

    business_gaps = {item.field: item.reason for item in report.business_analysis.missing_fields}
    external_gaps = {
        item.field: item.reason for item in report.external_verification.missing_fields
    }
    financial_gaps = {item.field: item.reason for item in report.financial_analysis.missing_fields}
    assert "products" not in business_gaps
    assert business_gaps["customers"] == "capability_absent"
    assert business_gaps["suppliers"] == "capability_absent"
    assert external_gaps["public_opinion"] == "source_error"
    assert financial_gaps["periods"] == "pagination_truncated"


def test_projector_uses_product_relationship_and_separate_statement_facts() -> None:
    counterparties: list[JsonValue] = [
        cast(JsonValue, {"name": f"企业{index}", "period": "2025", "ratio": ratio})
        for index, ratio in enumerate((30, 20, 10, 5, 4), start=1)
    ]
    result = (
        ProductFactProjector()
        .project(
            subject=ResolvedSubject(
                subject_id="tyc:one",
                company_name="测试企业",
                source=SubjectSource.TIANYANCHA,
                resolved_at=datetime(2026, 9, 7, tzinfo=UTC),
            ),
            evidence=(),
            findings=(),
            coverage=CoverageSummary.from_items([]),
            section_data={
                "registration": {
                    "regCapital": "500万元",
                    "businessScope": "设备制造",
                    "regLocation": "北京市朝阳区",
                    "legalPersonName": "法定代表人甲",
                    "estiblishTime": 946684800000,
                },
                "shareholders": {
                    "records": [{"shareholderName": "股东甲", "percent": 60}],
                },
                "actual_controller": {
                    "records": [{"name": "控制人甲", "percent": 60}],
                },
                "beneficial_owners": {
                    "records": [{"name": "受益人甲", "percent": 60}],
                },
                "external_investments": {
                    "records": [{"company_name": "被投企业", "ownership_percent": 51}],
                },
                "guarantees": {
                    "records": [{"guaranteed_party": "被担保企业", "amount": 800000}],
                },
                "disclosed_transactions": {
                    "records": [
                        {
                            "counterparty": "关联企业",
                            "year": "2025",
                            "transaction_amount": 1200000,
                        }
                    ],
                },
                "products": {
                    "records": [{"product_name": "工业部件", "category": "制造"}],
                },
                "suppliers_customers": {
                    "customers": counterparties,
                    "suppliers": counterparties,
                    "complete_top_n": 5,
                    "ratios_confirmed_same_denominator": True,
                },
                "industry_benchmarks": {
                    "period": "2025",
                    "growth_rate": 7.5,
                    "outlook": "平稳",
                },
                "financial_summary": {
                    "records": [{"year": "2025", "revenue": 1200}],
                    "source_metadata": {
                        "amount_multiplier": 10000,
                        "statement_scope": "consolidated",
                    },
                },
                "income_statement": {
                    "records": [{"year": "2025", "net_profit": 80}],
                    "source_metadata": {"amount_multiplier": 10000},
                },
                "balance_sheet": {
                    "records": [
                        {
                            "year": "2025",
                            "total_assets": 3000,
                            "total_liabilities": 1000,
                            "total_equity": 2000,
                        }
                    ],
                    "source_metadata": {"amount_multiplier": 10000},
                },
                "cash_flow_statement": {
                    "records": [{"year": "2025", "operating_cash_flow": 200}],
                    "source_metadata": {"amount_multiplier": 10000},
                },
            },
        )
        .report
    )

    assert result.company_profile.registered_capital == 5_000_000
    assert result.company_profile.established_date == date(2000, 1, 1)
    assert result.company_profile.registered_address == "北京市朝阳区"
    assert result.company_profile.legal_representative == "法定代表人甲"
    assert result.company_profile.shareholders[0].name == "股东甲"
    assert result.ownership.actual_controllers[0].name == "控制人甲"
    assert result.ownership.beneficial_owners[0].name == "受益人甲"
    assert result.ownership.related_transactions[0].amount == 1_200_000
    assert result.ownership.guarantees[0].amount == 800_000
    assert len(result.ownership.equity_graph.edges) == 5
    assert result.business_analysis.products[0].name == "工业部件"
    assert result.business_analysis.customer_top1_ratio == 30
    assert result.business_analysis.customer_top5_ratio == 69
    assert result.business_analysis.supplier_top5_ratio == 69
    assert result.business_analysis.industry_trend.growth_rate == 7.5
    period = result.financial_analysis.periods[0]
    assert period.entity_scope == "consolidated"
    assert period.income_statement.revenue == 12_000_000
    assert period.income_statement.net_profit == 800_000
    assert period.balance_sheet.total_assets == 30_000_000
    assert period.cash_flow_statement.operating_cash_flow == 2_000_000
