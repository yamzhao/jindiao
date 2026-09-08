"""Render the product report and its charts from the same immutable values."""

# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import html

from jindiao.contracts.base import ContractModel
from jindiao.contracts.product import (
    ProductEvidence,
    ProductReport,
    ProductSubject,
    ProductSummary,
    RiskFinding,
)
from jindiao.contracts.report_policy import ReportPolicy

SECTION_TITLES = {
    "business_plan": "业务申报方案",
    "company_profile": "§1 公司基本情况",
    "ownership": "§2 股权结构与实际控制人",
    "business_analysis": "§3 经营情况·行业上下游",
    "financial_analysis": "§4 财务分析·三表与比率",
    "bank_flow_analysis": "§5 银行流水分析",
    "external_verification": "§6 外部多源核验",
    "risk_points": "§7 风险点提示",
}
LABELS = dict(
    item.split(":", 1)
    for item in (
        "reporting_org:上报机构|reporting_date:上报时间|company_name:申请企业名称|"
        "business_product:业务品种|customer_manager:主办客户经理|"
        "unified_social_credit_code:统一社会信用代码|industry:所属行业|application_type:发生类型|"
        "application_amount:申请金额|application_term_months:申请期限（月）|fund_use:资金用途|"
        "suggested_amount:建议额度|suggested_interest_rate:建议利率|"
        "suggested_credit_term_months:建议授信期限（月）|suggested_loan_term_months:建议贷款期限（月）|"
        "fund_use_detail:资金用途详细说明|guarantee_methods:建议保证方式|repayment_methods:建议还款方式|"
        "repayment_source:还款来源|unified_credit:统一授信情况|investigation_location:调查地点|"
        "established_date:成立日期|registration_status:登记状态|registered_address:注册地址|"
        "registered_capital:注册资本|paid_in_capital:实缴资本|capital_currency:资本币种|"
        "legal_representative:法定代表人|main_business:主营业务|employee_count:员工总数|"
        "insured_employee_count:参保人数|employee_period:人员统计期间|shareholders:股东|"
        "financial_highlights:财务摘要|period:期间|revenue:营业收入|revenue_yoy:营收同比（%）|"
        "net_profit:净利润|net_profit_yoy:净利润同比（%）|debt_to_asset_ratio:资产负债率（%）|"
        "name:名称|type:类型|shareholding_ratio:持股比例（%）|subscribed_capital:认缴资本|"
        "actual_controllers:实际控制人|beneficial_owners:受益所有人|control_depth:查得穿透层数|"
        "identification_basis:识别依据|is_suspected:是否为疑似识别|ownership_features:股权特征|"
        "foreign_involvement:涉外资本|state_owned_involvement:国有资本|vie_structure:VIE结构|"
        "nominee_shareholding:代持情况|related_companies:关联企业|company_id:企业标识|relation:关系|"
        "related_transactions:关联交易|counterparty:交易对方|transaction_type:交易类型|amount:金额|"
        "revenue_ratio:占营收比例（%）|pricing_disclosure:定价披露|guarantees:担保|guarantor:担保人|"
        "guaranteed_party:被担保方|method:担保方式|as_of_date:适用日期|equity_graph:股权关系图|"
        "products:产品|category:类别|description:说明|industry_trend:行业趋势|growth_rate:增速（%）|"
        "previous_growth_rate:前期增速（%）|outlook:展望|customers:客户|suppliers:供应商|ratio:比例（%）|"
        "relationship:关联关系|customer_top1_ratio:第一大客户占比（%）|"
        "customer_top5_ratio:前五大客户占比（%）|supplier_top5_ratio:前五大供应商占比（%）|"
        "raw_material_cost_change:原材料成本变化|change_ratio:变化比例（%）|equipment:设备|count:数量|"
        "capacity_utilization:产能利用率|periods:财务期间|period_type:期间类型|entity_scope:报表口径|"
        "balance_sheet:资产负债表|income_statement:利润表|cash_flow_statement:现金流量表|"
        "ratios:财务比率|total_assets:总资产|total_liabilities:总负债|total_equity:所有者权益|"
        "current_assets:流动资产|current_liabilities:流动负债|accounts_receivable:应收账款|"
        "cash_balance:货币资金|operating_cost:营业成本|ebit:息税前利润|interest_expense:利息费用|"
        "operating_cash_flow:经营现金净流量|investing_cash_flow:投资现金净流量|"
        "financing_cash_flow:筹资现金净流量|total_assets_yoy:总资产同比（%）|"
        "accounts_receivable_yoy:应收账款同比（%）|gross_margin:毛利率（%）|current_ratio:流动比率|"
        "receivable_turnover_days:应收周转天数|interest_coverage_ratio:利息保障倍数|"
        "reconciliation:三表勾稽|explanation:说明|indicator_comparisons:指标水位比较|metric:指标|"
        "value:数值|unit:单位|industry_value:行业值|warning_value:预警值|warning_operator:预警条件|"
        "period_start:统计起始日期|period_end:统计截止日期|account_count:账户数|"
        "transaction_count:交易笔数|total_inflow:总流入|total_outflow:总流出|"
        "revenue_match_difference_ratio:流入与可比营收差异（%）|"
        "operating_receipts_ratio:主营回款占流入（%）|recent_three_month_change_ratio:近三月流入变化（%）|"
        "monthly_totals:月度统计|month:月份|inflow:流入|outflow:流出|operating_receipts:主营回款|"
        "abnormal_transactions:异常交易线索|date:日期|direction:方向|verification_status:核验状态|"
        "repayment_gap:静态偿付缺口测算|available_cash:可用现金|unused_credit:未用授信|"
        "short_term_debt:短期债务|guarantee_exposure:假设代偿敞口|net_balance:测算净余额|"
        "assumptions:测算及代偿假设|relationship_graph:交易及关联关系图|registration:工商核验|"
        "judicial:司法核验|credit:征信摘要|tax:税务核验|public_opinion:舆情核验|"
        "internal_record:本行记录|status:状态|conclusion:结论|facts:已知事实"
    ).split("|")
)
ENUM_LABELS = {
    "legal_representative": "追加法定代表人保证",
    "actual_controller": "追加实控人保证",
    "legal_representative_spouse": "追加法代配偶保证",
    "actual_controller_spouse": "追加实控人配偶保证",
    "equal_payment": "按月等额本息",
    "monthly_interest_bullet_principal": "按月付息，一次性还本",
    "monthly_interest_periodic_principal": "按月付息，定期还本",
    "equal_principal": "按月等额本金",
    "other": "其他",
    "person": "自然人",
    "company": "企业",
    "annual": "年度",
    "quarter": "季度",
    "interim": "中期",
    "passed": "通过",
    "failed": "不通过",
    "inconclusive": "证据不足",
    "attention": "关注",
    "verified": "已核实",
    "unverified": "未独立核实",
    "in": "流入",
    "out": "流出",
}
_META = {"analysis", "evidence_ids", "missing_fields", "generated_fields"}


class ProductReportView(ContractModel):
    subject: ProductSubject
    summary: ProductSummary
    report: ProductReport
    risk_findings: tuple[RiskFinding, ...]
    evidence: tuple[ProductEvidence, ...]

    def validate_references(self) -> None:
        ids = tuple(item.id for item in self.risk_findings)
        if len(ids) != len(set(ids)) or self.summary.risk_count != len(ids):
            raise ValueError("invalid risk count in report view")
        if self.report.risk_points.finding_ids != ids:
            raise ValueError("invalid section 7 references")
        known = {item.id for item in self.evidence}
        if len(known) != len(self.evidence):
            raise ValueError("duplicate report evidence")

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"evidence_ids", "derived_from"} and not set(child) <= known:
                        raise ValueError("invalid report evidence references")
                    if key == "evidence_id" and child not in known:
                        raise ValueError("invalid report evidence reference")
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.model_dump(mode="json"))


def gap_annotations(view: ProductReportView) -> tuple[tuple[str, str, str], ...]:
    result = []
    for section in SECTION_TITLES:
        for gap in getattr(getattr(view.report, section), "missing_fields", ()):
            text = f"{LABELS.get(gap.field, gap.field)}: {gap.message}"
            identity = (
                "gap-" + hashlib.sha256(f"{section}:{gap.field}:{gap.reason}".encode()).hexdigest()
            )
            result.append((identity, section, text))
    return tuple(result)


def _scalar(value: object) -> str:
    if value is None:
        return "未获得"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    text = str(value)
    return html.escape(ENUM_LABELS.get(text, text)).replace("|", "&#124;").replace("\n", "<br>")


def _values(value: dict[str, object], *, depth: int = 3) -> list[str]:
    scalars = [
        (key, item)
        for key, item in value.items()
        if key not in _META and not isinstance(item, dict | list)
    ]
    lines = ["| 项目 | 内容 |", "| --- | --- |"] if scalars else []
    lines.extend(f"| {LABELS.get(key, key)} | {_scalar(item)} |" for key, item in scalars)
    for key, item in value.items():
        if key in _META or not isinstance(item, dict | list):
            continue
        lines.extend(["", f"{'#' * min(depth, 6)} {LABELS.get(key, key)}", ""])
        if isinstance(item, dict) and set(item) == {"nodes", "edges"}:
            nodes = {node["id"]: node["name"] for node in item["nodes"]}
            lines.extend(
                [
                    "| 关系起点 | 关系终点 | 关系 | 比例（%） | 金额 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for edge in item["edges"]:
                lines.append(
                    "| "
                    + " | ".join(
                        _scalar(v)
                        for v in (
                            nodes[edge["source"]],
                            nodes[edge["target"]],
                            edge["relation"],
                            edge["shareholding_ratio"],
                            edge["amount"],
                        )
                    )
                    + " |"
                )
            if not item["edges"]:
                lines.append("未获得可展示的关系。")
        elif isinstance(item, dict):
            lines.extend(_values(item, depth=depth + 1))
        elif not item:
            lines.append("未获得记录或已核验无记录，具体范围见数据缺口及证据。")
        elif all(isinstance(row, dict) for row in item):
            for index, row in enumerate(item, 1):
                lines.extend([f"**{index}**", "", *_values(row, depth=depth + 1), ""])
        else:
            lines.append("、".join(_scalar(row) for row in item))
    return lines


class ProductMarkdownRenderer:
    def render(self, view: ProductReportView, *, policy: ReportPolicy | None = None) -> str:
        policy = policy or ReportPolicy()
        advice = {"proceed": "正常推进", "manual_review": "建议补充尽调", "stop": "暂不推进"}
        lines = [
            f"# {_scalar(view.subject.company_name)} 尽调报告",
            "",
            f"风险点：{view.summary.risk_count} 个；AI 建议：{advice[view.summary.ai_suggestion]}",
            view.summary.ai_suggestion_reason,
            "",
            "金额单位为人民币元（资本以披露币种为准），比例为百分数，期限为月。空值表示未获得，不等于零。",
        ]
        if any(item.is_mock for item in view.evidence):
            lines.extend(["", "> Mock 数据提示：本报告含模拟事实，仅用于测试。"])
        gaps = gap_annotations(view)
        for key, title in SECTION_TITLES.items():
            lines.extend(["", f"## {title}", ""])
            if key == "risk_points":
                if not view.risk_findings:
                    lines.append("本次已获得资料中无已审核风险发现；不代表未覆盖范围已核验无风险。")
                for risk in view.risk_findings:
                    lines.extend(
                        [
                            f"### {_scalar(risk.title)}",
                            "",
                            _scalar(risk.risk_fact),
                            "",
                            "证据来源："
                            + "；".join(
                                f"{_scalar(tag.label)} [{tag.evidence_id}]"
                                for tag in risk.evidence_tags
                            ),
                            "核查项："
                            + (
                                "、".join(item.label for item in risk.check_items) or "审核事实披露"
                            ),
                            risk.historical_case or "历史案例：未生成",
                            "",
                        ]
                    )
                continue
            section = getattr(view.report, key)
            if section.analysis:
                lines.extend([_scalar(section.analysis), ""])
            values = section.model_dump(mode="json")
            values.pop("status", None)
            lines.extend(_values(values))
            if section.evidence_ids:
                lines.extend(
                    ["", "证据：" + "、".join(f"[{item}]" for item in section.evidence_ids)]
                )
            if policy.gap_placement == "section_and_appendix":
                for identity, section_id, text in gaps:
                    if section_id == key:
                        lines.extend(
                            [
                                "",
                                f"<!-- jindiao:gap-begin id={identity} section={key} -->",
                                "> 数据缺口(原附录披露):",
                                "> " + html.escape(text).replace("\n", "\\n"),
                                "<!-- jindiao:gap-end -->",
                            ]
                        )
        lines.extend(["", "## 数据缺口", ""])
        lines.extend(f"- {SECTION_TITLES[section]} / {_scalar(text)}" for _, section, text in gaps)
        lines.extend(["", "## 证据来源", ""])
        lines.extend(
            f"- [{item.id}] {_scalar(item.source_label)}：{_scalar(item.summary)}；"
            f"适用日期：{item.data_as_of or '未披露'}；来源：{_scalar(item.source_ref)}"
            for item in view.evidence
        )
        lines.extend(["", "内容由 AI 生成，仅供参考。", ""])
        return "\n".join(lines)
