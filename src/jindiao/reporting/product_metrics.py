"""Deterministic calculations for the prototype product report."""

from __future__ import annotations

from datetime import date, datetime
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.business import AbnormalTransaction, BankFlowInput
from jindiao.contracts.product import (
    BankFlowAnalysis,
    Counterparty,
    FinancialAnalysis,
    FinancialPeriod,
    FinancialRatios,
    IndicatorComparison,
    ProductEvidence,
    Reconciliation,
    RepaymentGap,
)


class ConcentrationInput(ContractModel):
    """Evidence boundary needed to calculate a Top-N concentration."""

    counterparties: tuple[Counterparty, ...]
    period: str
    denominator: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    ranked_complete_top_n: Annotated[int, Field(gt=0)] | None = None
    ratios_confirmed_same_denominator: bool = False


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    assert denominator is not None
    return numerator / denominator


def _yoy(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    assert prior is not None
    return (current - prior) / abs(prior) * 100


def _percent(numerator: float | None, denominator: float | None) -> float | None:
    value = _ratio(numerator, denominator)
    return value * 100 if value is not None else None


def _period_range(value: str) -> tuple[date, date] | None:
    parts = value.split("/")
    if len(parts) != 2:
        return None
    try:
        start, end = (date.fromisoformat(part) for part in parts)
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _comparable(current: FinancialPeriod, prior: FinancialPeriod) -> bool:
    if (
        not current.entity_scope.strip()
        or not prior.entity_scope.strip()
        or current.entity_scope != prior.entity_scope
        or current.period_type != prior.period_type
    ):
        return False
    if current.period_type == "annual":
        return (
            current.period.isdigit()
            and prior.period.isdigit()
            and int(current.period) == int(prior.period) + 1
        )
    if current.period_type == "quarter":
        try:
            current_year, current_quarter = current.period.split("-Q")
            prior_year, prior_quarter = prior.period.split("-Q")
            years_are_consecutive = int(current_year) == int(prior_year) + 1
        except ValueError:
            return False
        return current_quarter == prior_quarter and years_are_consecutive
    current_range = _period_range(current.period)
    prior_range = _period_range(prior.period)
    if current_range is None or prior_range is None:
        return False
    current_start, current_end = current_range
    prior_start, prior_end = prior_range
    return (
        current_start.year == prior_start.year + 1
        and current_end.year == prior_end.year + 1
        and (current_start.month, current_start.day) == (prior_start.month, prior_start.day)
        and (current_end.month, current_end.day) == (prior_end.month, prior_end.day)
    )


def _prior_period(
    current: FinancialPeriod,
    periods: tuple[FinancialPeriod, ...],
) -> FinancialPeriod | None:
    return next((candidate for candidate in periods if _comparable(current, candidate)), None)


def _calculated_period(period: FinancialPeriod, prior: FinancialPeriod | None) -> FinancialPeriod:
    balance = period.balance_sheet
    income = period.income_statement
    prior_balance = prior.balance_sheet if prior else None
    prior_income = prior.income_statement if prior else None
    revenue = income.revenue
    average_receivables = None
    if (
        balance.accounts_receivable is not None
        and prior_balance is not None
        and prior_balance.accounts_receivable is not None
    ):
        average_receivables = (balance.accounts_receivable + prior_balance.accounts_receivable) / 2
    period_range = _period_range(period.period) if period.period_type == "interim" else None
    period_days = (
        365
        if period.period_type == "annual"
        else 90
        if period.period_type == "quarter"
        else (period_range[1] - period_range[0]).days + 1
        if period_range
        else None
    )
    calculated = FinancialRatios(
        revenue_yoy=_yoy(income.revenue, prior_income.revenue if prior_income else None),
        net_profit_yoy=_yoy(income.net_profit, prior_income.net_profit if prior_income else None),
        total_assets_yoy=_yoy(
            balance.total_assets,
            prior_balance.total_assets if prior_balance else None,
        ),
        accounts_receivable_yoy=_yoy(
            balance.accounts_receivable,
            prior_balance.accounts_receivable if prior_balance else None,
        ),
        gross_margin=_percent(
            income.revenue - income.operating_cost
            if income.revenue is not None and income.operating_cost is not None
            else None,
            income.revenue,
        ),
        debt_to_asset_ratio=_percent(balance.total_liabilities, balance.total_assets),
        current_ratio=_ratio(balance.current_assets, balance.current_liabilities),
        receivable_turnover_days=(
            average_receivables / revenue * period_days
            if average_receivables is not None
            and revenue is not None
            and revenue != 0
            and period_days is not None
            else None
        ),
        interest_coverage_ratio=_ratio(income.ebit, income.interest_expense),
    )
    return period.model_copy(update={"ratios": calculated})


def _reconcile(periods: tuple[FinancialPeriod, ...], tolerance: float) -> Reconciliation:
    scopes = {period.entity_scope for period in periods}
    if len(scopes) > 1 or not scopes or any(not scope.strip() for scope in scopes):
        return Reconciliation(explanation="财务期间主体口径不一致, 无法勾稽")
    differences: list[float] = []
    evidence_ids: list[str] = []
    for period in periods:
        balance = period.balance_sheet
        if None in (balance.total_assets, balance.total_liabilities, balance.total_equity):
            return Reconciliation(explanation="资产负债表恒等式字段不完整")
        assert balance.total_assets is not None
        assert balance.total_liabilities is not None
        assert balance.total_equity is not None
        allowed = max(tolerance, abs(balance.total_assets) * 1e-6)
        difference = abs(balance.total_assets - balance.total_liabilities - balance.total_equity)
        differences.append(difference - allowed)
        evidence_ids.extend(period.evidence_ids)
    if not periods:
        return Reconciliation()
    if any(difference > 0 for difference in differences):
        return Reconciliation(
            status="failed",
            explanation="资产不等于负债与所有者权益之和",
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        )
    return Reconciliation(
        status="passed",
        explanation="资产等于负债与所有者权益之和",
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
    )


def calculate_financials(
    periods: tuple[FinancialPeriod, ...],
    *,
    identity_tolerance: float = 1.0,
) -> FinancialAnalysis:
    """Calculate ratios from yuan-denominated financial statements."""
    calculated = tuple(
        _calculated_period(period, _prior_period(period, periods)) for period in periods
    )
    metrics: tuple[
        Literal[
            "revenue_yoy",
            "net_profit_yoy",
            "total_assets_yoy",
            "accounts_receivable_yoy",
            "gross_margin",
            "debt_to_asset_ratio",
            "current_ratio",
            "receivable_turnover_days",
            "interest_coverage_ratio",
        ],
        ...,
    ] = (
        "revenue_yoy",
        "net_profit_yoy",
        "total_assets_yoy",
        "accounts_receivable_yoy",
        "gross_margin",
        "debt_to_asset_ratio",
        "current_ratio",
        "receivable_turnover_days",
        "interest_coverage_ratio",
    )
    comparisons = tuple(
        IndicatorComparison(
            metric=metric,
            period=period.period,
            value=getattr(period.ratios, metric),
            unit=(
                "天"
                if metric == "receivable_turnover_days"
                else "%"
                if metric.endswith("_yoy")
                or metric
                in {
                    "gross_margin",
                    "debt_to_asset_ratio",
                }
                else "倍"
            ),
            evidence_ids=period.evidence_ids,
        )
        for period in calculated
        for metric in metrics
        if getattr(period.ratios, metric) is not None
    )
    return FinancialAnalysis(
        status="complete" if periods else "unavailable",
        periods=calculated,
        reconciliation=_reconcile(periods, identity_tolerance),
        indicator_comparisons=comparisons,
    )


def calculate_concentration(value: ConcentrationInput | None) -> tuple[float | None, float | None]:
    """Return Top-1 and Top-5 percentages only within an explicit evidence boundary."""
    if value is None or value.ranked_complete_top_n is None:
        return None, None
    if any(row.period != value.period for row in value.counterparties):
        return None, None
    rows = list(value.counterparties)
    ratios: list[float]
    if value.denominator is not None and all(row.amount is not None for row in rows):
        ratios = [row.amount / value.denominator * 100 for row in rows if row.amount is not None]
    elif value.ratios_confirmed_same_denominator and all(row.ratio is not None for row in rows):
        ratios = [row.ratio for row in rows if row.ratio is not None]
    else:
        return None, None
    top1 = ratios[0] if ratios and value.ranked_complete_top_n >= 1 else None
    top5 = sum(ratios[:5]) if len(ratios) >= 5 and value.ranked_complete_top_n >= 5 else None
    return top1, top5


def _month_number(month: str) -> int:
    year, number = month.split("-")
    return int(year) * 12 + int(number)


def _recent_change(value: BankFlowInput, *, aggregate_consistent: bool) -> float | None:
    rows = sorted(value.monthly_totals, key=lambda row: row.month)
    if (
        len(rows) < 6
        or not aggregate_consistent
        or value.period_start is None
        or value.period_end is None
    ):
        return None
    rows = rows[-6:]
    start_month = value.period_start.year * 12 + value.period_start.month
    end_month = value.period_end.year * 12 + value.period_end.month
    row_months = [_month_number(row.month) for row in rows]
    if row_months != list(range(end_month - 5, end_month + 1)) or row_months[0] < start_month:
        return None
    if any(
        _month_number(right.month) - _month_number(left.month) != 1
        for left, right in pairwise(rows)
    ):
        return None
    if any(row.inflow is None for row in rows):
        return None
    previous = sum(row.inflow for row in rows[:3] if row.inflow is not None)
    recent = sum(row.inflow for row in rows[3:] if row.inflow is not None)
    return _yoy(recent, previous)


def _repayment_gap(value: BankFlowInput, evidence_ids: tuple[str, ...]) -> RepaymentGap:
    inputs = value.repayment_gap_inputs
    if inputs is None:
        return RepaymentGap()
    numeric_fields = (
        inputs.operating_cash_flow,
        inputs.available_cash,
        inputs.unused_credit,
        inputs.short_term_debt,
        inputs.guarantee_exposure,
    )
    assumptions = (inputs.assumptions or "").lower()
    static_explicit = "静态" in assumptions or "static" in assumptions
    compact_assumptions = "".join(assumptions.split())
    full_exposure_explicit = (
        "担保敞口按或有代偿全额计入" in compact_assumptions
        or "fullguaranteeexposureincludedascontingentcompensation" in compact_assumptions
    )
    net = None
    if (
        bool(inputs.period and inputs.period.strip())
        and all(item is not None for item in numeric_fields)
        and static_explicit
        and full_exposure_explicit
    ):
        assert inputs.operating_cash_flow is not None
        assert inputs.available_cash is not None
        assert inputs.unused_credit is not None
        assert inputs.short_term_debt is not None
        assert inputs.guarantee_exposure is not None
        net = (
            inputs.operating_cash_flow
            + inputs.available_cash
            + inputs.unused_credit
            - inputs.short_term_debt
            - inputs.guarantee_exposure
        )
    return RepaymentGap(**inputs.model_dump(), net_balance=net, evidence_ids=evidence_ids)


def calculate_bank_flow(
    value: BankFlowInput | None,
    evidence_ids: tuple[str, ...] = (),
) -> BankFlowAnalysis:
    """Normalize caller-owned bank data without adding verification claims."""
    if value is None:
        return BankFlowAnalysis()
    full_period = value.period_start is not None and value.period_end is not None
    monthly_inflows = [row.inflow for row in value.monthly_totals]
    known_monthly_inflow = sum(item for item in monthly_inflows if item is not None)
    aggregate_consistent = True
    if value.total_inflow is not None and monthly_inflows:
        if known_monthly_inflow > value.total_inflow + 1:
            aggregate_consistent = False
        elif all(item is not None for item in monthly_inflows):
            aggregate_consistent = abs(value.total_inflow - known_monthly_inflow) <= 1
    operating_ratio = None
    revenue_difference = None
    if full_period and aggregate_consistent:
        ratio = _ratio(value.operating_receipts, value.total_inflow)
        operating_ratio = ratio * 100 if ratio is not None else None
        if value.total_inflow is not None and value.revenue_comparison_amount not in (None, 0):
            assert value.revenue_comparison_amount is not None
            revenue_difference = (
                (value.total_inflow - value.revenue_comparison_amount)
                / value.revenue_comparison_amount
                * 100
            )
    abnormal = tuple(
        AbnormalTransaction(**item.model_dump(exclude={"evidence_ids"}), evidence_ids=evidence_ids)
        for item in value.abnormal_transactions
    )
    return BankFlowAnalysis(
        status="complete" if full_period else "partial",
        period_start=value.period_start,
        period_end=value.period_end,
        account_count=value.account_count,
        transaction_count=value.transaction_count,
        total_inflow=value.total_inflow,
        total_outflow=value.total_outflow,
        revenue_match_difference_ratio=revenue_difference,
        operating_receipts_ratio=operating_ratio,
        recent_three_month_change_ratio=_recent_change(
            value,
            aggregate_consistent=aggregate_consistent,
        ),
        monthly_totals=value.monthly_totals,
        abnormal_transactions=abnormal,
        repayment_gap=_repayment_gap(value, evidence_ids) if full_period else RepaymentGap(),
        evidence_ids=evidence_ids,
    )


def derive_product_evidence(
    *,
    evidence_id: str,
    summary: str,
    supports_fields: tuple[str, ...],
    derived_from: tuple[str, ...],
    parent_mock: bool,
    queried_at: datetime,
    data_as_of: date | None = None,
) -> ProductEvidence:
    """Create explicit lineage for a deterministic derived value."""
    if not derived_from or any(not item.strip() for item in derived_from):
        raise ValueError("derived_from must contain non-empty parent evidence ids")
    return ProductEvidence(
        id=evidence_id,
        source_type="derived",
        source_label="确定性计算",
        summary=summary,
        source_ref=f"derived://{evidence_id}",
        data_as_of=data_as_of,
        queried_at=queried_at,
        supports_fields=supports_fields,
        derived_from=derived_from,
        is_mock=parent_mock,
    )


__all__ = [
    "ConcentrationInput",
    "calculate_bank_flow",
    "calculate_concentration",
    "calculate_financials",
    "derive_product_evidence",
]
