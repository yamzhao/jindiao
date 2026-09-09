"""Regression shapes from live Tianyancha responses, 2026-09-09 (names anonymized)."""
# ruff: noqa: RUF001, E501 -- Preserve provider Markdown punctuation and table rows.

from copy import deepcopy
from datetime import UTC, datetime

from jindiao.contracts.entities import ResolvedSubject, SubjectSource
from jindiao.contracts.evidence import CoverageSummary
from jindiao.reporting.product_fact_projector import ProductFactProjector
from jindiao.tianyancha.client import McpCallResult
from jindiao.tianyancha.normalizer import EvidenceDomain, TianyanchaEvidenceNormalizer

SUBJECT = ResolvedSubject(
    subject_id="tyc:test",
    company_name="测试企业",
    source=SubjectSource.TIANYANCHA,
    resolved_at=datetime(2026, 9, 9, tzinfo=UTC),
)
SHAREHOLDERS = """### 股东信息（1 条）
| # | 股东名称 | 持股比例 | 认缴出资 | 首次持股时间 | subscribedMethod | 认缴时间 |
|---|---|---|---|---|---|---|
| 1 | 示例股东有限公司 | 100% | 13732.339万元人民币 | 2025-12-10 | 货币 | 2021-09-22 |
"""
CONTROLLERS = """### 实际控制人（1 条）
| # | 实际控制人 | 比例 | 图谱ID | 人员ID | 人员ID |
|---|---|---|---|---|---|
| 1 | Example HK Limited | 1.0 | c1 | c1 | c1 |

### 控制路径（2 条）
| # | 控制路径 | 关系数 | 节点数 | 路径 | 关系 |
|---|---|---|---|---|---|
| 1 | 测试企业 -> Example HK Limited -> 示例股东有限公司 | 2 | 3 | p_0 | INVEST；INVEST |
| 2 | Example HK Limited -> 示例股东有限公司 -> 测试企业 | 2 | 3 | p_1 | INVEST；INVEST |
"""
OWNERS = """### 明细（1 条）
| # | 名称 | 类型 | 企业ID | 持股链路 | decisionReason | 关系图ID | ID | 完整持股比例 | 总数 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 示例人员 | human | c0 | 类型=title；标题=股权路径 | 未能穿透识别出拥有或超过25%公司股权的自然人，将关键管理人员视同为受益所有人 | p1-c0 | p1 | 13 | 0 |
"""


def normalized(text, tool):
    return TianyanchaEvidenceNormalizer().normalize(
        domain=EvidenceDomain.GOVERNANCE,
        subject=SUBJECT,
        tool_name=tool,
        result=McpCallResult(text=(text,)),
        queried_at=SUBJECT.resolved_at,
        as_of_date=None,
        raw_snapshot_ref="diagnostic://anonymized",
    )


def project(sections, evidence=()):
    return (
        ProductFactProjector()
        .project(
            subject=SUBJECT,
            evidence=evidence,
            findings=(),
            coverage=CoverageSummary.from_items([]),
            section_data=sections,
        )
        .report
    )


def test_repeated_equal_headers_do_not_drop_controller_table():
    batch = normalized(CONTROLLERS, "get_actual_controller")
    assert batch.record_count == 3
    assert batch.evidence[0].value["实际控制人"] == "Example HK Limited"
    assert batch.evidence[0].value["人员ID"] == "c1"


def test_conflicting_duplicate_cells_are_not_silently_overwritten():
    batch = normalized(
        CONTROLLERS.replace("| c1 | c1 | c1 |", "| c1 | c1 | c2 |"), "get_actual_controller"
    )
    rows = [e.value for e in batch.evidence if "实际控制人" in e.value]
    assert len(rows) == 1
    assert "人员ID" not in rows[0]


def test_live_shareholder_shape_maps_name_ratio_and_cny_without_mutating_evidence():
    batch = normalized(SHAREHOLDERS, "get_shareholder_info")
    rows = [e.value for e in batch.evidence]
    before = deepcopy(rows)
    report = project({"shareholders": {"records": rows}}, batch.evidence)
    s = report.ownership.shareholders[0]
    assert s.name == "示例股东有限公司"
    assert s.type == "company"
    assert s.shareholding_ratio == 100
    assert s.subscribed_capital == 137323390
    assert s.capital_currency == "CNY"
    assert s.evidence_ids
    assert report.company_profile.shareholders == report.ownership.shareholders
    assert rows == before


def test_control_paths_are_not_people_and_depth_uses_disclosed_edges():
    batch = normalized(CONTROLLERS, "get_actual_controller")
    report = project({"actual_controller": {"records": [e.value for e in batch.evidence]}})
    assert len(report.ownership.actual_controllers) == 1
    c = report.ownership.actual_controllers[0]
    assert c.name == "Example HK Limited"
    assert c.shareholding_ratio is None  # Bare 1.0 does not declare percent vs fraction.
    assert "1.0" in c.identification_basis
    assert report.ownership.control_depth == 2
    assert not report.ownership.beneficial_owners  # A controller is not automatically a UBO.


def test_deemed_beneficiary_preserves_reason_not_unsupported_ownership_ratio():
    batch = normalized(OWNERS, "get_beneficial_owners")
    report = project({"beneficial_owners": {"records": [e.value for e in batch.evidence]}})
    owner = report.ownership.beneficial_owners[0]
    assert owner.name == "示例人员"
    assert owner.type == "person"
    assert "视同" in owner.identification_basis
    assert owner.shareholding_ratio is None


def test_metadata_is_not_an_owner_and_ambiguous_foreign_capital_stays_unknown():
    report = project(
        {
            "shareholders": {
                "records": [
                    {"字段": "总数", "值": "0"},
                    {"股东名称": "示例股东", "认缴出资": "100万美元"},
                ]
            }
        }
    )
    assert len(report.ownership.shareholders) == 1
    assert report.ownership.shareholders[0].subscribed_capital is None


def test_supplier_without_related_party_disclosure_is_not_related_transaction():
    report = project(
        {
            "disclosed_transactions": {
                "source_tool": "get_suppliers_and_customers",
                "records": [{"名称": "普通供应商", "关联关系": "无", "交易金额": "100万元"}],
            }
        }
    )
    assert not report.ownership.related_transactions


def test_affirmative_related_party_and_legacy_transaction_fields_remain_supported():
    report = project(
        {
            "disclosed_transactions": {
                "source_tool": "get_suppliers_and_customers",
                "records": [
                    {"名称": "关联客户", "关联关系": "是", "交易金额": "100万元"},
                    {"名称": "普通客户", "关联关系": "未知", "交易金额": "200万元"},
                ],
            },
        }
    )
    assert len(report.ownership.related_transactions) == 1
    assert report.ownership.related_transactions[0].counterparty == "关联客户"
    assert report.ownership.related_transactions[0].amount == 1_000_000
    legacy = project(
        {
            "disclosed_transactions": {
                "related_transactions": [
                    {"counterparty": "旧数据关联客户", "amount": 20, "period": "2025"},
                ]
            }
        }
    )
    assert legacy.ownership.related_transactions[0].counterparty == "旧数据关联客户"
    assert legacy.ownership.related_transactions[0].amount == 20


def test_investment_label_mapping_preserves_company_identity_and_percentage():
    report = project(
        {
            "external_investments": {
                "records": [
                    {"企业名称": "被投企业", "企业ID": "123", "持股比例": "51%"},
                ]
            }
        }
    )
    company = report.ownership.related_companies[0]
    assert company.name == "被投企业"
    assert company.company_id == "123"
    assert company.shareholding_ratio == 51


def test_inconsistent_path_counts_do_not_invent_control_depth():
    report = project(
        {
            "actual_controller": {
                "records": [
                    {"控制路径": "A -> B", "关系数": "3", "节点数": "4"},
                    {"控制路径": "A -> C", "关系数": "1", "节点数": "3"},
                ]
            }
        }
    )
    assert report.ownership.control_depth is None
    assert not report.ownership.actual_controllers
