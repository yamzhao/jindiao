"""Optional caller-owned application information; monetary amounts are CNY yuan."""

from __future__ import annotations

from datetime import date as Date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ContractModel

Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Number = Annotated[float, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Months = Annotated[int, Field(gt=0)]
Percentage = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
GuaranteeMethod = Literal[
    "legal_representative",
    "actual_controller",
    "legal_representative_spouse",
    "actual_controller_spouse",
]
RepaymentMethod = Literal[
    "equal_payment",
    "monthly_interest_bullet_principal",
    "monthly_interest_periodic_principal",
    "equal_principal",
    "other",
]


class MonthlyTotal(ContractModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    inflow: Amount | None = None
    outflow: Amount | None = None
    operating_receipts: Amount | None = None


class AbnormalTransaction(ContractModel):
    date: Date | None = None
    direction: Literal["in", "out"]
    counterparty: str | None = None
    amount: Amount | None = None
    description: str = ""
    verification_status: Literal["verified", "unverified"] = "unverified"
    evidence_ids: tuple[str, ...] = ()


class RepaymentGapInput(ContractModel):
    period: str | None = None
    operating_cash_flow: Number | None = None
    available_cash: Amount | None = None
    unused_credit: Amount | None = None
    short_term_debt: Amount | None = None
    guarantee_exposure: Amount | None = None
    assumptions: str | None = None


class BankFlowInput(ContractModel):
    period_start: Date | None = None
    period_end: Date | None = None
    account_count: Count | None = None
    transaction_count: Count | None = None
    total_inflow: Amount | None = None
    total_outflow: Amount | None = None
    operating_receipts: Amount | None = None
    revenue_comparison_amount: Amount | None = None
    monthly_totals: tuple[MonthlyTotal, ...] = ()
    abnormal_transactions: tuple[AbnormalTransaction, ...] = ()
    repayment_gap_inputs: RepaymentGapInput | None = None
    source_reference: str | None = None

    @model_validator(mode="after")
    def validate_period(self) -> BankFlowInput:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("bank flow period start must precede end")
        months = [item.month for item in self.monthly_totals]
        if len(months) != len(set(months)):
            raise ValueError("bank flow months must be unique")
        if any(item.evidence_ids for item in self.abnormal_transactions):
            raise ValueError("caller cannot supply server evidence ids")
        return self


class CreditInfoInput(ContractModel):
    as_of_date: Date | None = None
    bank_count: Count | None = None
    total_credit_limit: Amount | None = None
    used_credit_amount: Amount | None = None
    overdue_count: Count | None = None
    source_reference: str | None = None


class InternalRecordInput(ContractModel):
    as_of_date: Date | None = None
    is_first_credit: bool | None = None
    relationship_summary: str | None = None
    source_reference: str | None = None


class ApplicationFields(ContractModel):
    reporting_org: str | None = None
    reporting_date: Date | None = None
    business_product: str | None = None
    customer_manager: str | None = None
    application_type: str | None = None
    application_amount: Amount | None = None
    application_term_months: Months | None = None
    fund_use: str | None = None
    suggested_amount: Amount | None = None
    suggested_interest_rate: str | None = None
    suggested_credit_term_months: Months | None = None
    suggested_loan_term_months: Months | None = None
    fund_use_detail: str | None = None
    guarantee_methods: tuple[GuaranteeMethod, ...] = ()
    repayment_methods: tuple[RepaymentMethod, ...] = ()
    repayment_source: str | None = None
    unified_credit: str | None = None
    investigation_location: str | None = None


class BusinessContext(ApplicationFields):
    bank_flow: BankFlowInput | None = None
    credit_info: CreditInfoInput | None = None
    internal_record: InternalRecordInput | None = None
