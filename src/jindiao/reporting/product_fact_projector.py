"""Project normalized investigation facts into the fixed product report schema."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import cast

from pydantic import JsonValue

from jindiao.acquisition.catalog import ACQUISITION_CATALOG
from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import (
    CoverageCompleteness,
    CoverageSummary,
    Evidence,
    SourceStatus,
)
from jindiao.contracts.investigation import Finding, FindingStatus, RiskClass
from jindiao.contracts.product import (
    BalanceSheet,
    BankFlowAnalysis,
    BusinessAnalysis,
    BusinessProduct,
    CapacityUtilization,
    CashFlowStatement,
    CompanyProfile,
    ControlPerson,
    CostChange,
    Counterparty,
    Equipment,
    ExternalVerification,
    FinancialAnalysis,
    FinancialPeriod,
    GraphEdge,
    GraphNode,
    Guarantee,
    IncomeStatement,
    IndustryTrend,
    MissingField,
    MissingReason,
    Ownership,
    ProductEvidence,
    ProductReport,
    RelatedCompany,
    RelatedTransaction,
    RelationshipGraph,
    Shareholder,
    Verification,
    VerificationFact,
)
from jindiao.contracts.product_facts import ProductFactBundle
from jindiao.reporting.product_metrics import ConcentrationInput, calculate_concentration

_GAP_MESSAGES = {
    "capability_absent": "本次主体未发现对应查询能力",
    "source_error": "本次来源查询失败",
    "pagination_truncated": "本次查询结果因分页预算未取全",
    "missing_period": "来源未提供所需期间",
    "not_disclosed": "本次来源未披露完整字段",
    "not_requested": "本次受预算限制未发起查询",
}


def _coverage_field_state(
    coverage: CoverageSummary,
) -> tuple[tuple[str, ...], tuple[MissingField, ...]]:
    """Project acquisition outcomes onto real public report field paths."""
    catalog_ids = set(ACQUISITION_CATALOG.acquisition_ids)
    verified_empty: list[str] = []
    gaps: dict[str, MissingField] = {}
    for item in coverage.items:
        if item.capability not in catalog_ids:
            continue
        fields = tuple(
            field
            for field in ACQUISITION_CATALOG.get(item.capability).report_fields
            if field.count(".") >= 2 and not field.startswith("report.risk_points")
        )
        if (
            item.status is SourceStatus.VERIFIED_EMPTY
            and item.completeness is CoverageCompleteness.COMPLETE
        ):
            verified_empty.extend(fields)
            continue
        reason = None
        if item.error == "not_requested":
            reason = "not_requested"
        elif item.status is SourceStatus.CAPABILITY_ABSENT:
            reason = "capability_absent"
        elif item.status is SourceStatus.SOURCE_ERROR:
            reason = "source_error"
        elif item.completeness is CoverageCompleteness.PARTIAL:
            values = {value.value for value in item.gap_reasons}
            reason = (
                "pagination_truncated"
                if "pagination_truncated" in values
                else "missing_period"
                if "missing_period" in values
                else "source_error"
                if "source_unavailable" in values
                else "not_disclosed"
            )
        if reason is None:
            continue
        for field in fields:
            gaps[field] = MissingField(
                field=field,
                reason=cast(MissingReason, reason),
                message=_GAP_MESSAGES[reason],
            )
    return tuple(dict.fromkeys(verified_empty)), tuple(gaps.values())


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _first(source: Mapping[str, object], *keys: str) -> object | None:
    direct = next((source[key] for key in keys if source.get(key) not in (None, "")), None)
    if direct is not None:
        return direct
    normalized = {
        re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(key).lower()): value
        for key, value in source.items()
        if value not in (None, "")
    }
    return next(
        (
            normalized[re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", key.lower())]
            for key in keys
            if re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", key.lower()) in normalized
        ),
        None,
    )


def _text(source: Mapping[str, object], *keys: str) -> str | None:
    value = _first(source, *keys)
    return str(value).strip() if value is not None and str(value).strip() else None


def _number(source: Mapping[str, object], *keys: str, multiplier: float = 1) -> float | None:
    value = _first(source, *keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) * multiplier
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace(",", "")
    unit = 1
    if normalized.endswith("亿元"):
        unit, normalized = 100_000_000, normalized[:-2]
    elif normalized.endswith("万元"):
        unit, normalized = 10_000, normalized[:-2]
    elif normalized.endswith("千元"):
        unit, normalized = 1_000, normalized[:-2]
    elif normalized.endswith(("元", "%")):
        normalized = normalized[:-1]
    try:
        return float(normalized) * multiplier * unit
    except ValueError:
        return None


def _integer(source: Mapping[str, object], *keys: str) -> int | None:
    value = _number(source, *keys)
    return int(value) if value is not None and value >= 0 and value.is_integer() else None


def _date(source: Mapping[str, object], *keys: str) -> date | None:
    value = _first(source, *keys)
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) >= 100_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC).date()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip() if value is not None else ""
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _module_ids(evidence: tuple[Evidence, ...], domains: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        item.evidence_id
        for item in evidence
        if any(field.split(".", 1)[0] in domains for field in item.supports_fields)
    )


def _submodule_facts(
    snapshot: EnterpriseContextSnapshot | None,
) -> dict[str, Mapping[str, object]]:
    if snapshot is None:
        return {}
    return {item.submodule_id: item.facts for item in snapshot.submodules}


def _submodule_evidence_ids(
    snapshot: EnterpriseContextSnapshot | None,
    submodule_id: str,
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    if snapshot is None:
        return fallback
    item = next(
        (candidate for candidate in snapshot.submodules if candidate.submodule_id == submodule_id),
        None,
    )
    if item is None:
        return fallback
    return tuple(dict.fromkeys((*item.evidence_ids, *item.supplemental_evidence_ids)))


def _source_sections(
    section_data: Mapping[str, Mapping[str, JsonValue]],
    snapshot: EnterpriseContextSnapshot | None,
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {
        key: cast(Mapping[str, object], value) for key, value in section_data.items()
    }
    result.update(_submodule_facts(snapshot))
    return result


def _record_group(source: Mapping[str, object], key: str) -> tuple[Mapping[str, object], ...]:
    direct = _records(source.get(key))
    if direct:
        return direct
    facts = _mapping(source.get("facts"))
    direct = _records(facts.get(key))
    if direct:
        return direct
    return _records(source.get("records"))


def _single_record_source(source: Mapping[str, object]) -> Mapping[str, object]:
    records = _records(source.get("records"))
    if len(records) != 1:
        return source
    return {**source, **records[0]}


def _ownership_rows(
    source: Mapping[str, object],
    key: str,
    *name_keys: str,
) -> tuple[Mapping[str, object], ...]:
    """Adapt provider field labels on copies, leaving frozen evidence untouched."""
    aliases = {
        "持股比例": "shareholding_ratio",
        "认缴出资": "subscribed_capital",
        "认缴出资额": "subscribed_capital",
        "实缴出资": "paid_in_capital",
        "实缴出资额": "paid_in_capital",
        "被投资企业名称": "company_name",
        "企业名称": "company_name",
        "企业ID": "company_id",
        "交易金额": "amount",
        "交易类型": "transaction_type",
        "公告日期": "period",
        "decisionReason": "identification_basis",
    }
    result: list[Mapping[str, object]] = []
    for item in _record_group(source, key):
        name = _text(item, *name_keys)
        if not name or name in {"-", "--", "暂无", "未披露", "null"}:
            continue  # Metadata and control-path rows are not entities.
        row = dict(item)
        row["name"] = name
        for label, canonical in aliases.items():
            if label in item:
                row.setdefault(canonical, item[label])
        for field in ("subscribed_capital", "paid_in_capital"):
            value = row.get(field)
            if isinstance(value, str) and re.fullmatch(r"[\d,.]+(?:亿|万|千)?元人民币", value):
                row[field] = value.removesuffix("人民币")
                row.setdefault("capital_currency", "CNY")
        if "实际控制人" in item and "比例" in item:
            # The live bare value 1.0 does not state whether it is a fraction
            # or percentage. Preserve it as context, never silently scale it.
            row.setdefault(
                "identification_basis",
                f"来源披露的控制人字段; 比例原值: {item['比例']} (单位未明确)",
            )
        result.append(row)
    return tuple(result)


def _disclosed_control_depth(source: Mapping[str, object]) -> int | None:
    depths = []
    for row in _records(source.get("records")):
        path = _text(row, "控制路径")
        edges, nodes = _integer(row, "关系数"), _integer(row, "节点数")
        if path and edges is not None and nodes == edges + 1 and edges > 0:
            if len(path.split(" -> ")) == nodes:
                depths.append(edges)
    return max(depths) if depths else None


def _disclosed_related_transactions(source: Mapping[str, object]) -> Mapping[str, object]:
    if source.get("source_tool") != "get_suppliers_and_customers":
        return source
    # A trading counterparty is not necessarily a related party. Require an
    # affirmative disclosure instead of treating every supplier as related.
    rows = tuple(
        row
        for row in _record_group(source, "related_transactions")
        if _text(row, "关联关系", "是否关联方", "is_related_party")
        in {"是", "关联方", "存在关联关系", "True", "true"}
    )
    return {**source, "records": rows, "related_transactions": rows}


def _registration_source(source: Mapping[str, object]) -> Mapping[str, object]:
    """Read the provider's vertical field table without rewriting frozen evidence."""
    result = dict(_single_record_source(source))
    aliases = {
        "成立日期": "established_date",
        "登记状态": "registration_status",
        "注册地址": "registered_address",
        "注册资本": "registered_capital",
        "实缴资本": "paid_in_capital",
        "法定代表人": "legal_representative",
        "行业": "industry",
        "经营范围": "main_business",
    }
    candidates: dict[str, list[object]] = {}
    for record in _records(source.get("records")):
        field = record.get("字段")
        if isinstance(field, str) and field.strip() in aliases:
            values = candidates.setdefault(aliases[field.strip()], [])
            value = record.get("值")
            if value not in values:
                values.append(value)
    for field, values in candidates.items():
        # Conflicting disclosures must not become a last-row-wins fact.
        if len(values) != 1 or values[0] is None:
            continue
        value = str(values[0]).strip()
        if value in {"", "-", "--", "未披露", "暂无", "null", "None", "N/A"}:
            continue
        if field in {"registered_capital", "paid_in_capital"}:
            match = re.fullmatch(
                r"(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
                r"(?P<scale>亿|万|千)?(?:元)?(?P<cny>人民币)?",
                value,
            )
            if match is None:
                # No implicit FX conversion or acceptance of non-finite values.
                continue
            value = match["amount"] + (match["scale"] or "") + "元"
            if match["cny"]:
                field += "_cny"
        result.setdefault(field, value)
    return result


def _counterparty_rows(
    source: Mapping[str, object],
    kind: str,
) -> tuple[Mapping[str, object], ...]:
    direct = _records(source.get(kind))
    if direct:
        return direct
    signals = ("客户", "customer") if kind == "customers" else ("供应商", "supplier")
    rows = _records(source.get("records"))
    return tuple(
        item
        for item in rows
        if any(
            signal
            in (_text(item, "type", "category", "relation", "counterparty_type") or "").lower()
            for signal in signals
        )
    )


def _public_evidence(
    evidence: tuple[Evidence, ...],
    supports: Mapping[str, tuple[str, ...]],
) -> tuple[ProductEvidence, ...]:
    known = {item.evidence_id for item in evidence}
    labels = {
        "tianyancha": "天眼查",
        "public_web": "公开来源",
        "user_input": "调用方资料",
        "derived": "确定性计算",
        "mock": "Mock 测试数据",
    }
    return tuple(
        ProductEvidence(
            id=item.evidence_id,
            source_type=item.source_type.value,
            source_label=f"{labels[item.source_type.value]}·{item.source_title or item.claim}",
            source_tool=item.source_tool,
            summary=item.claim,
            source_ref=item.raw_ref,
            data_as_of=item.as_of_date,
            queried_at=item.queried_at,
            supports_fields=supports.get(item.evidence_id, ()),
            derived_from=tuple(parent for parent in item.source_chain if parent in known)
            if item.source_type.value == "derived"
            else (),
            is_mock=item.is_mock,
        )
        for item in evidence
    )


def _financial_periods(
    sources: tuple[Mapping[str, object], ...],
    evidence_ids: tuple[str, ...],
) -> tuple[FinancialPeriod, ...]:
    periods: dict[str, dict[str, object]] = {}
    for source in sources:
        records = _record_group(source, "financial_indicators")
        metadata = _mapping(source.get("source_metadata"))
        multiplier = _number(metadata, "amount_multiplier") or 1
        scope = _text(metadata, "statement_scope")
        for record in records:
            period = _text(record, "year", "period", "report_year", "年度", "年份")
            if not period:
                continue
            values = periods.setdefault(period, {})
            values["scope"] = (
                _text(record, "entity_scope", "scope", "口径")
                or scope
                or values.get("scope", "企业披露口径")
            )
            money_fields = {
                "total_assets": ("total_assets", "assets", "资产总额", "总资产"),
                "total_liabilities": ("total_liabilities", "liabilities", "负债总额", "总负债"),
                "total_equity": ("total_equity", "equity", "所有者权益", "股东权益"),
                "current_assets": ("current_assets", "流动资产"),
                "current_liabilities": ("current_liabilities", "流动负债"),
                "accounts_receivable": ("accounts_receivable", "应收账款"),
                "cash_balance": ("cash_balance", "cash", "货币资金"),
                "revenue": ("revenue", "operating_revenue", "revenue_cny", "营业收入"),
                "operating_cost": ("operating_cost", "营业成本"),
                "net_profit": ("net_profit", "net_profit_cny", "净利润"),
                "ebit": ("ebit", "息税前利润"),
                "interest_expense": ("interest_expense", "利息费用"),
                "operating_cash_flow": ("operating_cash_flow", "经营现金净流量"),
                "investing_cash_flow": ("investing_cash_flow", "投资现金净流量"),
                "financing_cash_flow": ("financing_cash_flow", "筹资现金净流量"),
            }
            for name, aliases in money_fields.items():
                value = _number(record, *aliases, multiplier=multiplier)
                if value is not None:
                    values[name] = value
            revenue_10k = _number(record, "revenue_cny_10k", multiplier=10000)
            profit_10k = _number(record, "net_profit_cny_10k", multiplier=10000)
            if revenue_10k is not None:
                values["revenue"] = revenue_10k
            if profit_10k is not None:
                values["net_profit"] = profit_10k
    return tuple(
        FinancialPeriod(
            period=period,
            period_type="annual" if period.isdecimal() else "interim",
            entity_scope=str(values.get("scope", "企业披露口径")),
            evidence_ids=evidence_ids,
            balance_sheet=BalanceSheet.model_validate(
                {name: values.get(name) for name in BalanceSheet.model_fields if name in values}
            ),
            income_statement=IncomeStatement.model_validate(
                {name: values.get(name) for name in IncomeStatement.model_fields if name in values}
            ),
            cash_flow_statement=CashFlowStatement.model_validate(
                {
                    name: values.get(name)
                    for name in CashFlowStatement.model_fields
                    if name in values
                }
            ),
        )
        for period, values in sorted(periods.items())
    )


def _node_id(name: str) -> str:
    return "entity-" + hashlib.sha256(name.encode()).hexdigest()[:12]


def _relationship_graph(
    subject_name: str,
    shareholders: tuple[Shareholder, ...],
    controllers: tuple[ControlPerson, ...],
    related: tuple[RelatedCompany, ...],
    transactions: tuple[RelatedTransaction, ...],
    guarantees: tuple[Guarantee, ...],
) -> RelationshipGraph:
    if not any((shareholders, controllers, related, transactions, guarantees)):
        return RelationshipGraph()
    nodes: dict[str, GraphNode] = {
        _node_id(subject_name): GraphNode(
            id=_node_id(subject_name), name=subject_name, type="company"
        )
    }
    edges: list[GraphEdge] = []
    subject_id = _node_id(subject_name)
    for shareholder in shareholders:
        identity = _node_id(shareholder.name)
        nodes.setdefault(
            identity,
            GraphNode(id=identity, name=shareholder.name, type=shareholder.type),
        )
        edges.append(
            GraphEdge(
                source=identity,
                target=subject_id,
                relation="股东",
                shareholding_ratio=shareholder.shareholding_ratio,
                evidence_ids=shareholder.evidence_ids,
            )
        )
    for controller in controllers:
        identity = _node_id(controller.name)
        nodes.setdefault(
            identity, GraphNode(id=identity, name=controller.name, type=controller.type)
        )
        edges.append(
            GraphEdge(
                source=identity,
                target=subject_id,
                relation="实际控制",
                shareholding_ratio=controller.shareholding_ratio,
                evidence_ids=controller.evidence_ids,
            )
        )
    for related_company in related:
        identity = _node_id(related_company.name)
        nodes.setdefault(
            identity, GraphNode(id=identity, name=related_company.name, type="company")
        )
        edges.append(
            GraphEdge(
                source=subject_id,
                target=identity,
                relation=related_company.relation,
                shareholding_ratio=related_company.shareholding_ratio,
                evidence_ids=related_company.evidence_ids,
            )
        )
    for transaction in transactions:
        identity = _node_id(transaction.counterparty)
        nodes.setdefault(
            identity, GraphNode(id=identity, name=transaction.counterparty, type="company")
        )
        edges.append(
            GraphEdge(
                source=subject_id,
                target=identity,
                relation=transaction.transaction_type or "关联交易",
                amount=transaction.amount,
                evidence_ids=transaction.evidence_ids,
            )
        )
    for guarantee in guarantees:
        identity = _node_id(guarantee.guaranteed_party)
        nodes.setdefault(
            identity,
            GraphNode(id=identity, name=guarantee.guaranteed_party, type="company"),
        )
        edges.append(
            GraphEdge(
                source=subject_id,
                target=identity,
                relation="对外担保",
                amount=guarantee.amount,
                evidence_ids=guarantee.evidence_ids,
            )
        )
    return RelationshipGraph(nodes=tuple(nodes.values()), edges=tuple(edges))


def _concentration(
    items: tuple[Counterparty, ...],
    source: Mapping[str, object],
) -> tuple[float | None, float | None]:
    periods = {item.period for item in items if item.period}
    period = next(iter(periods)) if len(periods) == 1 else None
    ranked = _integer(source, "ranked_complete_top_n", "complete_top_n")
    if period is None or ranked is None:
        return None, None
    value = ConcentrationInput(
        counterparties=items,
        period=period,
        denominator=_number(source, "denominator", "total_amount"),
        ranked_complete_top_n=ranked,
        ratios_confirmed_same_denominator=bool(source.get("ratios_confirmed_same_denominator")),
    )
    return calculate_concentration(value)


def _verification(
    capabilities: frozenset[str],
    support_prefixes: tuple[str, ...],
    finding_domains: frozenset[str],
    coverage: CoverageSummary,
    evidence: tuple[Evidence, ...],
    findings: tuple[Finding, ...],
    evidence_ids: tuple[str, ...] = (),
) -> Verification:
    domain_evidence = tuple(
        item
        for item in evidence
        if item.evidence_id in evidence_ids
        or item.source_tool in capabilities
        or any(field.startswith(support_prefixes) for field in item.supports_fields)
    )
    risks = tuple(
        item
        for item in findings
        if item.status is FindingStatus.ACCEPTED
        and item.risk_class is not RiskClass.NON_RISK
        and item.domain in finding_domains
    )
    statuses = {item.status for item in coverage.items if item.capability in capabilities}
    ids = tuple(dict.fromkeys(item.evidence_id for item in domain_evidence))
    facts = tuple(
        VerificationFact(description=item.claim, evidence_ids=(item.evidence_id,))
        for item in domain_evidence
    )
    if risks:
        return Verification(
            status="attention",
            conclusion="发现已审核关注事项",
            facts=facts,
            evidence_ids=tuple(dict.fromkeys(e for risk in risks for e in risk.evidence_ids)),
        )
    if SourceStatus.VERIFIED_EMPTY in statuses or SourceStatus.VERIFIED_RECORDS in statuses:
        return Verification(
            status="passed",
            conclusion="在本次查询范围内未发现已审核风险事项",
            facts=facts,
            evidence_ids=ids,
        )
    return Verification()


class ProductFactProjector:
    """Deterministic projection; it never infers missing facts or risk decisions."""

    def project(
        self,
        *,
        subject: ResolvedSubject,
        evidence: tuple[Evidence, ...],
        findings: tuple[Finding, ...],
        coverage: CoverageSummary,
        section_data: Mapping[str, Mapping[str, JsonValue]],
        snapshot: EnterpriseContextSnapshot | None = None,
    ) -> ProductFactBundle:
        verified_empty_fields, source_missing_fields = _coverage_field_state(coverage)
        source = _source_sections(section_data, snapshot)
        company = _registration_source(
            _mapping(source.get("company-profile", source.get("registration", {})))
        )
        governance = _mapping(source.get("related-parties", source.get("shareholders", company)))
        operations = _mapping(
            source.get("operations-analysis", source.get("financial_summary", {}))
        )
        shareholder_source = _mapping(source.get("shareholders", governance))
        controller_source = _mapping(source.get("actual_controller", governance))
        owner_source = _mapping(source.get("beneficial_owners", {}))
        investment_source = _mapping(source.get("external_investments", governance))
        guarantee_source = _mapping(source.get("guarantees", governance))
        transaction_source = _disclosed_related_transactions(
            _mapping(source.get("disclosed_transactions", governance))
        )
        employment_source = _single_record_source(
            _mapping(source.get("annual_reports", operations))
        )
        industry_source = _single_record_source(
            _mapping(source.get("industry_benchmarks", source.get("peer-analysis", {})))
        )
        company_ids = _module_ids(evidence, frozenset({"company", "governance"}))
        operation_ids = _module_ids(evidence, frozenset({"operations", "financial"}))
        judicial_ids = _module_ids(evidence, frozenset({"judicial"}))
        peer_ids = _module_ids(evidence, frozenset({"peers"}))
        registration_ids = tuple(
            dict.fromkeys(
                identity
                for name in ("registration", "registration_changes", "simple_deregistration")
                for identity in _submodule_evidence_ids(snapshot, name, ())
            )
        )
        tax_ids = tuple(
            dict.fromkeys(
                identity
                for name in ("tax_credit", "tax_arrears", "tax_violations")
                for identity in _submodule_evidence_ids(snapshot, name, ())
            )
        )
        opinion_ids = _submodule_evidence_ids(snapshot, "public_opinion", ())
        annual_ids = _submodule_evidence_ids(snapshot, "annual_reports", operation_ids)
        financial_ids = tuple(
            dict.fromkeys(
                identity
                for name in (
                    "financial_summary",
                    "balance_sheet",
                    "income_statement",
                    "cash_flow_statement",
                )
                for identity in _submodule_evidence_ids(snapshot, name, ())
            )
        )
        if snapshot is None:
            financial_ids = operation_ids
        shareholder_ids = _submodule_evidence_ids(snapshot, "shareholders", company_ids)
        controller_ids = _submodule_evidence_ids(snapshot, "actual_controller", company_ids)
        owner_ids = _submodule_evidence_ids(snapshot, "beneficial_owners", controller_ids)
        investment_ids = _submodule_evidence_ids(snapshot, "external_investments", company_ids)
        guarantee_ids = _submodule_evidence_ids(snapshot, "guarantees", company_ids)
        transaction_ids = _submodule_evidence_ids(snapshot, "disclosed_transactions", company_ids)

        shareholder_rows = _ownership_rows(
            shareholder_source, "shareholders", "name", "shareholder_name", "股东名称"
        ) or _ownership_rows(company, "shareholders", "name", "shareholder_name", "股东名称")
        shareholders = tuple(
            Shareholder(
                name=_text(item, "name", "shareholder_name") or "未披露名称",
                type=(
                    "company"
                    if any(
                        word in (_text(item, "name") or "").lower()
                        for word in ("公司", "集团", "limited", "ltd")
                    )
                    else "person"
                ),
                shareholding_ratio=_number(
                    item, "shareholding_ratio", "ownership_percent", "percent"
                ),
                subscribed_capital=_number(item, "subscribed_capital", "subscribed_capital_cny"),
                paid_in_capital=_number(item, "paid_in_capital", "paid_in_capital_cny"),
                capital_currency=_text(item, "capital_currency")
                or (
                    "CNY"
                    if _first(item, "subscribed_capital_cny", "paid_in_capital_cny") is not None
                    else None
                ),
                evidence_ids=shareholder_ids,
            )
            for item in shareholder_rows
        )
        controller_rows = _ownership_rows(
            controller_source, "actual_controllers", "name", "controller_name", "实际控制人"
        )
        controller = _text(
            controller_source, "name", "ultimate_controller", "actual_controller", "controller"
        )
        actual = tuple(
            ControlPerson(
                name=_text(item, "name", "controller_name") or "未披露名称",
                shareholding_ratio=_number(item, "shareholding_ratio", "percent"),
                identification_basis=_text(item, "identification_basis", "basis")
                or "来源披露的控制人字段",
                is_suspected=bool(item.get("is_suspected", True)),
                evidence_ids=controller_ids,
            )
            for item in controller_rows
        )
        if not actual and controller:
            actual = (
                ControlPerson(
                    name=controller,
                    identification_basis="来源披露的控制人字段",
                    is_suspected=True,
                    evidence_ids=controller_ids,
                ),
            )
        owner_rows = _ownership_rows(
            owner_source, "beneficial_owners", "name", "owner_name", "名称"
        )
        owners = tuple(
            ControlPerson(
                name=_text(item, "name", "owner_name") or "未披露名称",
                type="person" if item.get("类型") == "human" else "other",
                shareholding_ratio=_number(item, "shareholding_ratio", "percent"),
                identification_basis=_text(item, "identification_basis", "basis")
                or "来源披露的受益所有人字段",
                is_suspected=bool(item.get("is_suspected", True)),
                evidence_ids=owner_ids,
            )
            for item in owner_rows
        )
        investments = _ownership_rows(
            investment_source,
            "outbound_investments",
            "company_name",
            "name",
            "被投资企业名称",
            "企业名称",
        ) or _ownership_rows(
            investment_source,
            "external_investments",
            "company_name",
            "name",
            "被投资企业名称",
            "企业名称",
        )
        related = tuple(
            RelatedCompany(
                company_id=_text(item, "company_id", "id"),
                name=_text(item, "company_name", "name") or "未披露名称",
                relation="对外投资",
                shareholding_ratio=_number(item, "shareholding_ratio", "ownership_percent"),
                evidence_ids=investment_ids,
            )
            for item in investments
        )
        transactions = tuple(
            RelatedTransaction(
                counterparty=_text(item, "counterparty", "name", "related_party") or "未披露名称",
                period=_text(item, "period", "year"),
                transaction_type=_text(item, "transaction_type", "type"),
                amount=_number(item, "amount", "transaction_amount"),
                revenue_ratio=_number(item, "revenue_ratio", "ratio"),
                pricing_disclosure=_text(item, "pricing_disclosure", "pricing_basis"),
                evidence_ids=transaction_ids,
            )
            for item in _ownership_rows(
                transaction_source,
                "related_transactions",
                "counterparty",
                "name",
                "related_party",
                "名称",
                "供应商/客户名称",
            )
        )
        guarantees = tuple(
            Guarantee(
                guarantor=_text(item, "guarantor"),
                guaranteed_party=_text(
                    item, "guaranteed_party", "guarantee_target", "debtor", "name"
                )
                or "未披露名称",
                amount=_number(item, "amount", "guarantee_amount"),
                method=_text(item, "method", "guarantee_method"),
                status=_text(item, "status", "guarantee_status"),
                as_of_date=_date(item, "as_of_date", "date"),
                evidence_ids=guarantee_ids,
            )
            for item in _record_group(guarantee_source, "guarantees")
        )
        employee_count = _integer(employment_source, "employee_count")
        if employee_count is None:
            employee_count = _integer(operations, "employee_count")
        insured = _integer(employment_source, "insured_employee_count", "insured_count")
        profile = CompanyProfile(
            company_name=subject.company_name,
            unified_social_credit_code=subject.unified_social_credit_code,
            established_date=_date(company, "established_on", "established_date", "estiblish_time"),
            registration_status=subject.registration_status
            or _text(company, "registration_status", "reg_status", "status"),
            registered_address=_text(company, "registered_address", "reg_location", "address"),
            registered_capital=_number(
                company, "registered_capital", "registered_capital_cny", "reg_capital"
            ),
            paid_in_capital=_number(
                company, "paid_in_capital", "paid_in_capital_cny", "actual_capital"
            ),
            capital_currency=_text(company, "capital_currency")
            or (
                "CNY"
                if _first(company, "registered_capital_cny", "paid_in_capital_cny") is not None
                else None
            ),
            legal_representative=_text(company, "legal_representative", "legal_person_name"),
            industry=_text(company, "industry", "industry_name"),
            main_business=_text(company, "main_business", "business_scope"),
            employee_count=employee_count,
            insured_employee_count=insured,
            employee_period=_text(employment_source, "employee_period", "year", "as_of_date"),
            shareholders=shareholders,
            evidence_ids=tuple(
                dict.fromkeys(
                    (
                        *(registration_ids if snapshot is not None else company_ids),
                        *shareholder_ids,
                        *annual_ids,
                    )
                )
            ),
        )
        product_source = _mapping(source.get("products", operations))
        supply_source = _mapping(source.get("suppliers_customers", operations))
        product_ids = _submodule_evidence_ids(snapshot, "products", operation_ids)
        supply_ids = _submodule_evidence_ids(snapshot, "suppliers_customers", operation_ids)
        product_rows = _record_group(product_source, "products")
        customer_rows = _counterparty_rows(supply_source, "customers")
        supplier_rows = _counterparty_rows(supply_source, "suppliers")
        customers = tuple(
            Counterparty(
                name=_text(item, "name", "customer_name") or "未披露名称",
                period=_text(item, "period", "year"),
                amount=_number(item, "amount", "sales_amount"),
                ratio=_number(item, "ratio", "sales_ratio"),
                relationship=_text(item, "relationship"),
                evidence_ids=supply_ids,
            )
            for item in customer_rows
        )
        suppliers = tuple(
            Counterparty(
                name=_text(item, "name", "supplier_name") or "未披露名称",
                period=_text(item, "period", "year"),
                amount=_number(item, "amount", "purchase_amount"),
                ratio=_number(item, "ratio", "purchase_ratio"),
                relationship=_text(item, "relationship"),
                evidence_ids=supply_ids,
            )
            for item in supplier_rows
        )
        customer_top1, customer_top5 = _concentration(customers, supply_source)
        _, supplier_top5 = _concentration(suppliers, supply_source)
        trend = _mapping(industry_source.get("industry_trend", industry_source))
        business = BusinessAnalysis(
            main_business=profile.main_business,
            products=tuple(
                BusinessProduct(
                    name=_text(item, "name", "product_name") or "未披露名称",
                    category=_text(item, "category"),
                    description=_text(item, "description"),
                    evidence_ids=product_ids,
                )
                for item in product_rows
            ),
            industry_trend=IndustryTrend(
                period=_text(trend, "period", "year"),
                growth_rate=_number(trend, "growth_rate"),
                previous_growth_rate=_number(trend, "previous_growth_rate"),
                outlook=_text(trend, "outlook"),
                evidence_ids=peer_ids,
            ),
            customers=customers,
            suppliers=suppliers,
            customer_top1_ratio=customer_top1,
            customer_top5_ratio=customer_top5,
            supplier_top5_ratio=supplier_top5,
            raw_material_cost_change=CostChange(
                period=_text(operations, "raw_material_cost_period"),
                change_ratio=_number(operations, "raw_material_cost_change_ratio"),
                description=_text(operations, "raw_material_cost_change"),
                evidence_ids=operation_ids,
            ),
            equipment=tuple(
                Equipment(
                    name=_text(item, "name", "equipment_name") or "未披露名称",
                    count=_integer(item, "count", "quantity"),
                    evidence_ids=operation_ids,
                )
                for item in _record_group(operations, "equipment")
            ),
            capacity_utilization=CapacityUtilization(
                period=_text(operations, "capacity_period"),
                ratio=_number(operations, "capacity_utilization", "capacity_utilization_ratio"),
                evidence_ids=operation_ids,
            ),
            evidence_ids=tuple(dict.fromkeys((*product_ids, *supply_ids, *peer_ids))),
        )
        periods = _financial_periods(
            tuple(
                _mapping(source.get(key, operations if key == "financial_summary" else {}))
                for key in (
                    "financial_summary",
                    "balance_sheet",
                    "income_statement",
                    "cash_flow_statement",
                )
            ),
            financial_ids,
        )
        graph = _relationship_graph(
            subject.company_name,
            shareholders,
            actual,
            related,
            transactions,
            guarantees,
        )
        relationship_ids = tuple(
            dict.fromkeys(
                (
                    *shareholder_ids,
                    *controller_ids,
                    *owner_ids,
                    *investment_ids,
                    *transaction_ids,
                    *guarantee_ids,
                )
            )
        )
        report = ProductReport(
            company_profile=profile,
            ownership=Ownership(
                shareholders=shareholders,
                actual_controllers=actual,
                beneficial_owners=owners,
                control_depth=_disclosed_control_depth(controller_source),
                related_companies=related,
                related_transactions=transactions,
                guarantees=guarantees,
                equity_graph=graph,
                evidence_ids=relationship_ids,
            ),
            business_analysis=business,
            financial_analysis=FinancialAnalysis(periods=periods, evidence_ids=financial_ids),
            bank_flow_analysis=BankFlowAnalysis(
                relationship_graph=graph,
                evidence_ids=relationship_ids,
            ),
            external_verification=ExternalVerification(
                registration=_verification(
                    frozenset({"registration", "registration_changes", "simple_deregistration"}),
                    ("company.", "governance."),
                    frozenset({"identity", "governance"}),
                    coverage,
                    evidence,
                    findings,
                    registration_ids,
                ),
                judicial=_verification(
                    frozenset(
                        {
                            "consumption_restrictions",
                            "dishonest_enforcement",
                            "executions",
                            "judicial_documents",
                            "hearing_notices",
                        }
                    ),
                    ("judicial.",),
                    frozenset({"judicial"}),
                    coverage,
                    evidence,
                    findings,
                    judicial_ids,
                ),
                tax=_verification(
                    frozenset({"tax_credit", "tax_arrears", "tax_violations"}),
                    ("tax.",),
                    frozenset({"tax"}),
                    coverage,
                    evidence,
                    findings,
                    tax_ids,
                ),
                public_opinion=_verification(
                    frozenset({"public_opinion"}),
                    ("public_opinion.", "news."),
                    frozenset({"public_opinion"}),
                    coverage,
                    evidence,
                    findings,
                    opinion_ids,
                ),
                evidence_ids=tuple(
                    dict.fromkeys((*registration_ids, *judicial_ids, *tax_ids, *opinion_ids))
                ),
            ),
        )
        supports: dict[str, tuple[str, ...]] = {}
        if snapshot is not None:
            catalog_ids = set(ACQUISITION_CATALOG.acquisition_ids)
            for item in snapshot.submodules:
                if item.submodule_id not in catalog_ids:
                    continue
                fields = ACQUISITION_CATALOG.get(item.submodule_id).report_fields
                for identity in (*item.evidence_ids, *item.supplemental_evidence_ids):
                    supports[identity] = tuple(
                        dict.fromkeys((*supports.get(identity, ()), *fields))
                    )
        else:
            for identity in company_ids:
                supports[identity] = (
                    "report.company_profile",
                    "report.ownership",
                    "report.external_verification.registration",
                )
            for identity in operation_ids:
                supports[identity] = (
                    "report.company_profile.employee_count",
                    "report.business_analysis",
                    "report.financial_analysis.periods",
                    "report.external_verification.tax",
                )
            for identity in judicial_ids:
                supports[identity] = ("report.external_verification.judicial",)
            for identity in peer_ids:
                supports[identity] = ("report.business_analysis.industry_trend",)
        return ProductFactBundle(
            report=report,
            evidence=_public_evidence(evidence, supports),
            verified_empty_fields=verified_empty_fields,
            source_missing_fields=source_missing_fields,
        )
