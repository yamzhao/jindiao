"""Fixed product report contract. Internal execution artifacts are not public fields."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import (
    AwareDatetime,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from jindiao.security import redact_json

from .base import ContractModel
from .business import (
    AbnormalTransaction,
    Amount,
    ApplicationFields,
    Count,
    MonthlyTotal,
    Number,
    Percentage,
    RepaymentGapInput,
)
from .results import OrchestrationMode, RunStatus

MissingReason = Literal[
    "not_provided",
    "not_disclosed",
    "capability_absent",
    "source_error",
    "not_requested",
    "pagination_truncated",
    "missing_period",
    "conflict",
    "generation_failed",
]
EntityType = Literal["person", "company", "other"]


class MissingField(ContractModel):
    field: str = Field(min_length=1)
    reason: MissingReason
    message: str = Field(min_length=1)


class ReportModule(ContractModel):
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    analysis: str = ""
    evidence_ids: tuple[str, ...] = ()
    missing_fields: tuple[MissingField, ...] = ()


class BusinessPlan(ApplicationFields, ReportModule):
    company_name: str | None = None
    unified_social_credit_code: str | None = None
    industry: str | None = None
    generated_fields: tuple[str, ...] = ()


class Shareholder(ContractModel):
    name: str
    type: EntityType = "other"
    shareholding_ratio: Percentage | None = None
    subscribed_capital: Amount | None = None
    paid_in_capital: Amount | None = None
    capital_currency: str | None = None
    evidence_ids: tuple[str, ...] = ()


class FinancialHighlights(ContractModel):
    period: str | None = None
    revenue: Number | None = None
    revenue_yoy: Number | None = None
    net_profit: Number | None = None
    net_profit_yoy: Number | None = None
    debt_to_asset_ratio: Number | None = None


class CompanyProfile(ReportModule):
    company_name: str | None = None
    unified_social_credit_code: str | None = None
    established_date: date | None = None
    registration_status: str | None = None
    registered_address: str | None = None
    registered_capital: Amount | None = None
    paid_in_capital: Amount | None = None
    capital_currency: str | None = None
    legal_representative: str | None = None
    industry: str | None = None
    main_business: str | None = None
    employee_count: Count | None = None
    insured_employee_count: Count | None = None
    employee_period: str | None = None
    shareholders: tuple[Shareholder, ...] = ()
    financial_highlights: FinancialHighlights = Field(default_factory=FinancialHighlights)


class ControlPerson(ContractModel):
    name: str
    type: EntityType = "other"
    shareholding_ratio: Percentage | None = None
    identification_basis: str | None = None
    is_suspected: bool | None = None
    evidence_ids: tuple[str, ...] = ()


class OwnershipFeatures(ContractModel):
    foreign_involvement: bool | None = None
    state_owned_involvement: bool | None = None
    vie_structure: bool | None = None
    nominee_shareholding: bool | None = None


class RelatedCompany(ContractModel):
    company_id: str | None = None
    name: str
    relation: str
    shareholding_ratio: Percentage | None = None
    evidence_ids: tuple[str, ...] = ()


class RelatedTransaction(ContractModel):
    counterparty: str
    period: str | None = None
    transaction_type: str | None = None
    amount: Amount | None = None
    revenue_ratio: Percentage | None = None
    pricing_disclosure: str | None = None
    evidence_ids: tuple[str, ...] = ()


class Guarantee(ContractModel):
    guarantor: str | None = None
    guaranteed_party: str
    amount: Amount | None = None
    method: str | None = None
    status: str | None = None
    as_of_date: date | None = None
    evidence_ids: tuple[str, ...] = ()


class GraphNode(ContractModel):
    id: str
    name: str
    type: EntityType = "other"


class GraphEdge(ContractModel):
    source: str
    target: str
    relation: str
    shareholding_ratio: Percentage | None = None
    amount: Amount | None = None
    evidence_ids: tuple[str, ...] = ()


class RelationshipGraph(ContractModel):
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()

    @model_validator(mode="after")
    def validate_edges(self) -> RelationshipGraph:
        ids = {item.id for item in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("graph node ids must be unique")
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError("graph references unknown node")
        return self


class Ownership(ReportModule):
    shareholders: tuple[Shareholder, ...] = ()
    actual_controllers: tuple[ControlPerson, ...] = ()
    beneficial_owners: tuple[ControlPerson, ...] = ()
    control_depth: Count | None = None
    ownership_features: OwnershipFeatures = Field(default_factory=OwnershipFeatures)
    related_companies: tuple[RelatedCompany, ...] = ()
    related_transactions: tuple[RelatedTransaction, ...] = ()
    guarantees: tuple[Guarantee, ...] = ()
    equity_graph: RelationshipGraph = Field(default_factory=RelationshipGraph)


class BusinessProduct(ContractModel):
    name: str
    category: str | None = None
    description: str | None = None
    evidence_ids: tuple[str, ...] = ()


class IndustryTrend(ContractModel):
    period: str | None = None
    growth_rate: Number | None = None
    previous_growth_rate: Number | None = None
    outlook: str | None = None
    evidence_ids: tuple[str, ...] = ()


class Counterparty(ContractModel):
    name: str
    period: str | None = None
    amount: Amount | None = None
    ratio: Percentage | None = None
    relationship: str | None = None
    evidence_ids: tuple[str, ...] = ()


class CostChange(ContractModel):
    period: str | None = None
    change_ratio: Number | None = None
    description: str | None = None
    evidence_ids: tuple[str, ...] = ()


class Equipment(ContractModel):
    name: str
    count: Count | None = None
    evidence_ids: tuple[str, ...] = ()


class CapacityUtilization(ContractModel):
    period: str | None = None
    ratio: Percentage | None = None
    evidence_ids: tuple[str, ...] = ()


class BusinessAnalysis(ReportModule):
    main_business: str | None = None
    products: tuple[BusinessProduct, ...] = ()
    industry_trend: IndustryTrend = Field(default_factory=IndustryTrend)
    customers: tuple[Counterparty, ...] = ()
    suppliers: tuple[Counterparty, ...] = ()
    customer_top1_ratio: Percentage | None = None
    customer_top5_ratio: Percentage | None = None
    supplier_top5_ratio: Percentage | None = None
    raw_material_cost_change: CostChange = Field(default_factory=CostChange)
    equipment: tuple[Equipment, ...] = ()
    capacity_utilization: CapacityUtilization = Field(default_factory=CapacityUtilization)


class BalanceSheet(ContractModel):
    total_assets: Number | None = None
    total_liabilities: Number | None = None
    total_equity: Number | None = None
    current_assets: Number | None = None
    current_liabilities: Number | None = None
    accounts_receivable: Number | None = None
    cash_balance: Number | None = None


class IncomeStatement(ContractModel):
    revenue: Number | None = None
    operating_cost: Number | None = None
    net_profit: Number | None = None
    ebit: Number | None = None
    interest_expense: Number | None = None


class CashFlowStatement(ContractModel):
    operating_cash_flow: Number | None = None
    investing_cash_flow: Number | None = None
    financing_cash_flow: Number | None = None


class FinancialRatios(ContractModel):
    revenue_yoy: Number | None = None
    net_profit_yoy: Number | None = None
    total_assets_yoy: Number | None = None
    accounts_receivable_yoy: Number | None = None
    gross_margin: Number | None = None
    debt_to_asset_ratio: Number | None = None
    current_ratio: Number | None = None
    receivable_turnover_days: Number | None = None
    interest_coverage_ratio: Number | None = None


class FinancialPeriod(ContractModel):
    period: str
    period_type: Literal["annual", "quarter", "interim"] = "annual"
    entity_scope: str
    evidence_ids: tuple[str, ...] = ()
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    income_statement: IncomeStatement = Field(default_factory=IncomeStatement)
    cash_flow_statement: CashFlowStatement = Field(default_factory=CashFlowStatement)
    ratios: FinancialRatios = Field(default_factory=FinancialRatios)


class Reconciliation(ContractModel):
    status: Literal["passed", "failed", "inconclusive"] = "inconclusive"
    explanation: str = "缺少可核验的同口径财务数据"
    evidence_ids: tuple[str, ...] = ()


class IndicatorComparison(ContractModel):
    metric: Literal[
        "revenue_yoy",
        "net_profit_yoy",
        "total_assets_yoy",
        "accounts_receivable_yoy",
        "gross_margin",
        "debt_to_asset_ratio",
        "current_ratio",
        "receivable_turnover_days",
        "interest_coverage_ratio",
    ]
    period: str
    value: Number | None = None
    unit: str
    industry_value: Number | None = None
    warning_value: Number | None = None
    warning_operator: Literal["gt", "gte", "lt", "lte"] | None = None
    evidence_ids: tuple[str, ...] = ()


class FinancialAnalysis(ReportModule):
    periods: tuple[FinancialPeriod, ...] = ()
    reconciliation: Reconciliation = Field(default_factory=Reconciliation)
    indicator_comparisons: tuple[IndicatorComparison, ...] = ()


class RepaymentGap(RepaymentGapInput):
    net_balance: Number | None = None
    evidence_ids: tuple[str, ...] = ()


class BankFlowAnalysis(ReportModule):
    period_start: date | None = None
    period_end: date | None = None
    account_count: Count | None = None
    transaction_count: Count | None = None
    total_inflow: Amount | None = None
    total_outflow: Amount | None = None
    revenue_match_difference_ratio: Number | None = None
    operating_receipts_ratio: Percentage | None = None
    recent_three_month_change_ratio: Number | None = None
    monthly_totals: tuple[MonthlyTotal, ...] = ()
    abnormal_transactions: tuple[AbnormalTransaction, ...] = ()
    repayment_gap: RepaymentGap = Field(default_factory=RepaymentGap)
    relationship_graph: RelationshipGraph = Field(default_factory=RelationshipGraph)


class VerificationFact(ContractModel):
    description: str
    evidence_ids: tuple[str, ...] = ()


class Verification(ContractModel):
    status: Literal["passed", "attention", "inconclusive"] = "inconclusive"
    conclusion: str = "缺少可核验资料"
    facts: tuple[VerificationFact, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ExternalVerification(ReportModule):
    registration: Verification = Field(default_factory=Verification)
    judicial: Verification = Field(default_factory=Verification)
    credit: Verification = Field(default_factory=Verification)
    tax: Verification = Field(default_factory=Verification)
    public_opinion: Verification = Field(default_factory=Verification)
    internal_record: Verification = Field(default_factory=Verification)


class RiskPoints(ContractModel):
    finding_ids: tuple[str, ...] = ()


class ProductReport(ContractModel):
    business_plan: BusinessPlan = Field(default_factory=BusinessPlan)
    company_profile: CompanyProfile = Field(default_factory=CompanyProfile)
    ownership: Ownership = Field(default_factory=Ownership)
    business_analysis: BusinessAnalysis = Field(default_factory=BusinessAnalysis)
    financial_analysis: FinancialAnalysis = Field(default_factory=FinancialAnalysis)
    bank_flow_analysis: BankFlowAnalysis = Field(default_factory=BankFlowAnalysis)
    external_verification: ExternalVerification = Field(default_factory=ExternalVerification)
    risk_points: RiskPoints = Field(default_factory=RiskPoints)


class CheckLabel(ContractModel):
    id: str
    label: str


class EvidenceTag(ContractModel):
    evidence_id: str
    label: str


class RiskFinding(ContractModel):
    id: str
    title: str
    source_kind: Literal["fact", "check", "mixed"]
    check_items: tuple[CheckLabel, ...] = ()
    risk_fact: str
    evidence_tags: tuple[EvidenceTag, ...] = Field(min_length=1)
    historical_case: str | None = None
    historical_case_is_mock: bool = False

    @model_validator(mode="after")
    def validate_case(self) -> RiskFinding:
        if bool(self.check_items) != (self.source_kind in {"check", "mixed"}):
            raise ValueError("risk source kind must match executed checks")
        if len({tag.evidence_id for tag in self.evidence_tags}) != len(self.evidence_tags):
            raise ValueError("risk evidence tags must be unique")
        if bool(self.historical_case) != self.historical_case_is_mock:
            raise ValueError("generated historical case must be explicitly marked mock")
        if self.historical_case and not self.historical_case.startswith("模拟案例\uff1a"):
            raise ValueError("historical case must disclose its simulated nature")
        return self


class ProductEvidence(ContractModel):
    id: str
    source_type: Literal["tianyancha", "public_web", "user_input", "derived", "mock"]
    source_label: str
    source_tool: str | None = None
    summary: str
    source_ref: str
    data_as_of: date | None = None
    queried_at: AwareDatetime
    supports_fields: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> ProductEvidence:
        if self.source_type == "derived" and not self.derived_from:
            raise ValueError("derived evidence requires source parents")
        if self.source_type == "mock" and not self.is_mock:
            raise ValueError("mock source must disclose mock facts")
        if self.source_type in {"tianyancha", "public_web", "user_input"} and self.is_mock:
            raise ValueError("real source cannot be marked as mock")
        if self.id in self.derived_from:
            raise ValueError("evidence cannot derive from itself")
        return self


class ProductMeta(ContractModel):
    request_id: str
    run_id: str
    status: RunStatus
    mode: OrchestrationMode
    generated_at: AwareDatetime
    report_as_of: date
    report_version: str = "v1"
    is_mock: bool = False


class ProductSubject(ContractModel):
    subject_id: str
    company_name: str
    unified_social_credit_code: str | None = None


class ProductSummary(ContractModel):
    risk_count: Count = 0
    ai_suggestion: Literal["proceed", "manual_review", "stop"] = "manual_review"
    ai_suggestion_reason: str = "关键证据不足; 需人工复核"


class ProductResult(ContractModel):
    schema_version: Literal["prototype-v1"] = "prototype-v1"
    meta: ProductMeta
    subject: ProductSubject
    summary: ProductSummary = Field(default_factory=ProductSummary)
    report: ProductReport = Field(default_factory=ProductReport)
    risk_findings: tuple[RiskFinding, ...] = ()
    evidence: tuple[ProductEvidence, ...] = ()
    report_markdown: str

    @model_serializer(mode="wrap")
    def redact(self, handler: SerializerFunctionWrapHandler) -> object:
        return redact_json(handler(self))

    @model_validator(mode="after")
    def validate_references(self) -> ProductResult:
        if self.meta.status not in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
            raise ValueError("a product result must be completed or partial")
        if self.meta.is_mock != any(item.is_mock for item in self.evidence):
            raise ValueError("result mock flag must reflect facts, not simulated cases")
        ids = [item.id for item in self.risk_findings]
        if len(ids) != len(set(ids)):
            raise ValueError("risk ids must be unique")
        if self.summary.risk_count != len(ids):
            raise ValueError("risk count must match cards")
        if tuple(ids) != self.report.risk_points.finding_ids:
            raise ValueError("section 7 must reference risk cards in order")
        known = {item.id for item in self.evidence}
        if len(known) != len(self.evidence):
            raise ValueError("evidence ids must be unique")

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"evidence_ids", "derived_from"} and isinstance(child, list):
                        if not set(child) <= known:
                            raise ValueError("unknown evidence reference")
                    elif key == "evidence_id" and child not in known:
                        raise ValueError("unknown evidence reference")
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.report.model_dump(mode="json"))
        for item in (*self.risk_findings, *self.evidence):
            visit(item.model_dump(mode="json"))
        public = {
            "report": self.report.model_dump(mode="json"),
            "subject": self.subject.model_dump(mode="json"),
            "summary": self.summary.model_dump(mode="json"),
            "risk_findings": [item.model_dump(mode="json") for item in self.risk_findings],
        }
        by_id = {item.id: item for item in self.evidence}
        for item in self.evidence:
            for path in item.supports_fields:
                value: object = public
                for part in path.split("."):
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
                        value = value[int(part)]
                    else:
                        raise ValueError("evidence supports_fields must resolve to public fields")
            if item.derived_from and item.is_mock != any(
                by_id[e].is_mock for e in item.derived_from
            ):
                raise ValueError("derived evidence must inherit mock status")

        def parents(identity: str, ancestors: frozenset[str]) -> None:
            if identity in ancestors:
                raise ValueError("derived evidence graph has a cycle")
            for parent in by_id[identity].derived_from:
                parents(parent, ancestors | {identity})

        for identity in by_id:
            parents(identity, frozenset())
        return self
