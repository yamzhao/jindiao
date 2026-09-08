from datetime import UTC, date, datetime
from typing import Literal

import pytest

from jindiao.contracts.business import (
    AbnormalTransaction,
    BankFlowInput,
    MonthlyTotal,
    RepaymentGapInput,
)
from jindiao.contracts.product import BalanceSheet, Counterparty, FinancialPeriod, IncomeStatement
from jindiao.reporting.product_metrics import (
    ConcentrationInput,
    calculate_bank_flow,
    calculate_concentration,
    calculate_financials,
    derive_product_evidence,
)


def financial_period(
    period: str,
    *,
    period_type: Literal["annual", "quarter", "interim"] = "annual",
    scope: str = "consolidated",
    revenue: float | None = None,
    profit: float | None = None,
    assets: float | None = None,
    liabilities: float | None = None,
    equity: float | None = None,
    receivables: float | None = None,
) -> FinancialPeriod:
    return FinancialPeriod(
        period=period,
        period_type=period_type,
        entity_scope=scope,
        evidence_ids=(f"ev-{period}",),
        balance_sheet=BalanceSheet(
            total_assets=assets,
            total_liabilities=liabilities,
            total_equity=equity,
            current_assets=60,
            current_liabilities=30,
            accounts_receivable=receivables,
        ),
        income_statement=IncomeStatement(
            revenue=revenue,
            operating_cost=60 if revenue is not None else None,
            net_profit=profit,
            ebit=-10,
            interest_expense=5,
        ),
    )


def test_financials_compute_ratios_yoy_and_identity() -> None:
    prior = financial_period(
        "2024",
        revenue=100,
        profit=-20,
        assets=100,
        liabilities=40,
        equity=60,
        receivables=20,
    )
    current = financial_period(
        "2025",
        revenue=120,
        profit=-10,
        assets=110,
        liabilities=50,
        equity=60,
        receivables=30,
    )

    result = calculate_financials((prior, current))

    ratios = result.periods[1].ratios
    assert ratios.revenue_yoy == pytest.approx(20)
    assert ratios.net_profit_yoy == pytest.approx(50)
    assert ratios.gross_margin == pytest.approx(50)
    assert ratios.debt_to_asset_ratio == pytest.approx(50 / 110 * 100)
    assert ratios.current_ratio == 2
    assert ratios.receivable_turnover_days == pytest.approx(25 / 120 * 365)
    assert ratios.interest_coverage_ratio == -2
    assert result.reconciliation.status == "passed"


@pytest.mark.parametrize(
    ("prior_period", "current_period", "period_type"),
    [("2024-Q4", "2025-Q3", "quarter"), ("2024", "2025-Q1", "annual")],
)
def test_financials_do_not_compare_non_comparable_periods(
    prior_period: str,
    current_period: str,
    period_type: Literal["annual", "quarter", "interim"],
) -> None:
    prior = financial_period(prior_period, period_type=period_type, revenue=100)
    current = financial_period(current_period, period_type="quarter", revenue=120)
    result = calculate_financials((prior, current))
    assert result.periods[1].ratios.revenue_yoy is None


def test_financials_handle_zero_denominators_and_quarter_days() -> None:
    prior = financial_period("2024-Q1", period_type="quarter", revenue=0, receivables=10)
    current = financial_period("2025-Q1", period_type="quarter", revenue=100, receivables=20)
    result = calculate_financials((prior, current))
    ratios = result.periods[1].ratios
    assert ratios.revenue_yoy is None
    assert ratios.receivable_turnover_days == pytest.approx(15 / 100 * 90)


def test_interim_comparison_requires_same_calendar_window_and_uses_actual_days() -> None:
    prior = financial_period(
        "2024-01-01/2024-03-31",
        period_type="interim",
        revenue=100,
        receivables=10,
    )
    shifted = financial_period(
        "2025-04-01/2025-06-30",
        period_type="interim",
        revenue=120,
        receivables=20,
    )
    comparable = financial_period(
        "2025-01-01/2025-03-31",
        period_type="interim",
        revenue=120,
        receivables=20,
    )
    shifted_result = calculate_financials((prior, shifted))
    comparable_result = calculate_financials((prior, comparable))
    assert shifted_result.periods[1].ratios.revenue_yoy is None
    assert comparable_result.periods[1].ratios.revenue_yoy == 20
    assert comparable_result.periods[1].ratios.receivable_turnover_days == pytest.approx(
        15 / 120 * 90
    )


def test_financials_mark_missing_or_inconsistent_identity_inconclusive() -> None:
    missing = financial_period("2024", assets=100, liabilities=40)
    inconsistent_scope = financial_period(
        "2025",
        scope="parent",
        assets=100,
        liabilities=50,
        equity=40,
    )
    result = calculate_financials((missing, inconsistent_scope))
    assert result.reconciliation.status == "inconclusive"


def test_financials_fail_identity_outside_conservative_tolerance() -> None:
    period = financial_period("2025", assets=100, liabilities=50, equity=48.99)
    assert calculate_financials((period,)).reconciliation.status == "failed"


def test_financials_do_not_pass_reconciliation_with_empty_scope() -> None:
    prior = financial_period("2024", scope="", revenue=100, assets=100, liabilities=40, equity=60)
    period = financial_period("2025", scope="", revenue=120, assets=100, liabilities=40, equity=60)
    result = calculate_financials((prior, period))
    assert result.reconciliation.status == "inconclusive"
    assert result.periods[1].ratios.revenue_yoy is None


def test_receivable_turnover_comparison_uses_days_unit() -> None:
    prior = financial_period("2024", revenue=100, receivables=10)
    current = financial_period("2025", revenue=120, receivables=20)
    result = calculate_financials((prior, current))
    comparison = next(
        item
        for item in result.indicator_comparisons
        if item.metric == "receivable_turnover_days" and item.period == "2025"
    )
    assert comparison.unit == "天"


def test_concentration_requires_complete_ranked_sample_and_denominator() -> None:
    partial = ConcentrationInput(
        counterparties=(
            Counterparty(name="A", period="2025", amount=30, evidence_ids=("e1",)),
            Counterparty(name="B", period="2025", amount=20, evidence_ids=("e2",)),
        ),
        period="2025",
        denominator=100,
        ranked_complete_top_n=None,
    )
    assert calculate_concentration(partial) == (None, None)

    complete = partial.model_copy(update={"ranked_complete_top_n": 5})
    assert calculate_concentration(complete) == (30, None)


def test_concentration_can_sum_confirmed_same_period_ratios() -> None:
    value = ConcentrationInput(
        counterparties=tuple(
            Counterparty(name=chr(65 + index), period="2025", ratio=ratio)
            for index, ratio in enumerate((30, 20, 10, 5, 4))
        ),
        period="2025",
        ranked_complete_top_n=5,
        ratios_confirmed_same_denominator=True,
    )
    assert calculate_concentration(value) == (30, 69)


def test_concentration_rejects_mixed_period_ranking() -> None:
    value = ConcentrationInput(
        counterparties=(
            Counterparty(name="A", period="2024", amount=50),
            Counterparty(name="B", period="2025", amount=30),
        ),
        period="2025",
        denominator=100,
        ranked_complete_top_n=1,
    )
    assert calculate_concentration(value) == (None, None)


def test_bank_flow_calculates_complete_period_and_repayment_gap() -> None:
    item = BankFlowInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        total_inflow=200,
        operating_receipts=150,
        revenue_comparison_amount=180,
        monthly_totals=tuple(
            MonthlyTotal(month=f"2025-{month:02d}", inflow=value)
            for month, value in enumerate((20, 30, 30, 30, 40, 50), start=1)
        ),
        abnormal_transactions=(AbnormalTransaction(direction="in", description="large"),),
        repayment_gap_inputs=RepaymentGapInput(
            period="2025-H1",
            operating_cash_flow=30,
            available_cash=20,
            unused_credit=10,
            short_term_debt=40,
            guarantee_exposure=5,
            assumptions="静态测算; 担保敞口按或有代偿全额计入",
        ),
    )
    result = calculate_bank_flow(item, evidence_ids=("bank-1",))
    assert result.operating_receipts_ratio == 75
    assert result.revenue_match_difference_ratio == pytest.approx(20 / 180 * 100)
    assert result.recent_three_month_change_ratio == 50
    assert result.repayment_gap.net_balance == 15
    assert result.abnormal_transactions[0].verification_status == "unverified"
    assert result.abnormal_transactions[0].evidence_ids == ("bank-1",)


def test_bank_flow_absent_incomplete_or_conflicting_inputs_produce_nulls() -> None:
    assert calculate_bank_flow(None).operating_receipts_ratio is None
    incomplete = BankFlowInput(total_inflow=100, operating_receipts=50)
    assert calculate_bank_flow(incomplete).operating_receipts_ratio is None

    conflicting = BankFlowInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        total_inflow=999,
        operating_receipts=50,
        monthly_totals=tuple(
            MonthlyTotal(month=f"2025-{month:02d}", inflow=10) for month in range(1, 7)
        ),
    )
    result = calculate_bank_flow(conflicting)
    assert result.operating_receipts_ratio is None
    assert result.recent_three_month_change_ratio is None


def test_repayment_gap_rejects_empty_period_and_conflicting_guarantee_assumption() -> None:
    base = BankFlowInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        repayment_gap_inputs=RepaymentGapInput(
            period="",
            operating_cash_flow=100,
            available_cash=20,
            unused_credit=10,
            short_term_debt=40,
            guarantee_exposure=40,
            assumptions="static; contingent compensation included",
        ),
    )
    assert calculate_bank_flow(base).repayment_gap.net_balance is None
    gap_inputs = base.repayment_gap_inputs
    assert gap_inputs is not None
    conflicting = base.model_copy(
        update={
            "repayment_gap_inputs": gap_inputs.model_copy(
                update={
                    "period": "2025-H1",
                    "assumptions": "static: no contingent compensation assumed",
                }
            )
        }
    )
    assert calculate_bank_flow(conflicting).repayment_gap.net_balance is None

    for assumptions in (
        "静态测算, 不考虑或有代偿",
        "静态测算, 假设担保敞口按10%发生代偿",
        "静态测算; 担保敞口不全额计入或有代偿",
        "static; full guarantee exposure is not included as contingent compensation",
        "static guarantee",
    ):
        invalid = conflicting.model_copy(
            update={
                "repayment_gap_inputs": gap_inputs.model_copy(
                    update={"period": "2025-H1", "assumptions": assumptions}
                )
            }
        )
        assert calculate_bank_flow(invalid).repayment_gap.net_balance is None


def test_partial_months_cannot_exceed_total_inflow() -> None:
    item = BankFlowInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 6, 30),
        total_inflow=20,
        operating_receipts=10,
        monthly_totals=(
            MonthlyTotal(month="2025-01", inflow=90),
            MonthlyTotal(month="2025-02", inflow=None),
        ),
    )
    result = calculate_bank_flow(item)
    assert result.operating_receipts_ratio is None
    assert result.recent_three_month_change_ratio is None


def test_bank_flow_six_month_change_rejects_gaps_and_zero_previous_window() -> None:
    for values in ((0, 0, 0, 2, 2, 2), (1, 1, 1, 2, 2, 2)):
        item = BankFlowInput(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 7, 31),
            monthly_totals=tuple(
                MonthlyTotal(month=month, inflow=value)
                for month, value in zip(
                    ("2025-01", "2025-02", "2025-03", "2025-05", "2025-06", "2025-07"),
                    values,
                    strict=True,
                )
            ),
        )
        assert calculate_bank_flow(item).recent_three_month_change_ratio is None

    consecutive = item.model_copy(
        update={
            "period_end": date(2025, 6, 30),
            "monthly_totals": tuple(
                MonthlyTotal(month=f"2025-{month:02d}", inflow=value)
                for month, value in enumerate((1, 1, 1, 2, 2, 2), start=1)
            ),
        }
    )
    assert calculate_bank_flow(consecutive).recent_three_month_change_ratio == 100


def test_bank_flow_six_month_window_is_anchored_to_period_boundaries() -> None:
    item = BankFlowInput(
        period_start=date(2025, 1, 1),
        period_end=date(2025, 7, 31),
        monthly_totals=tuple(
            MonthlyTotal(month=f"2025-{month:02d}", inflow=value)
            for month, value in enumerate((1, 1, 1, 2, 2, 2), start=1)
        ),
    )
    assert calculate_bank_flow(item).recent_three_month_change_ratio is None

    before_start = item.model_copy(
        update={
            "period_start": date(2025, 2, 1),
            "period_end": date(2025, 6, 30),
        }
    )
    assert calculate_bank_flow(before_start).recent_three_month_change_ratio is None


def test_derived_evidence_records_lineage() -> None:
    evidence = derive_product_evidence(
        evidence_id="derived:gross-margin:2025",
        summary="2025 gross margin = 50%",
        supports_fields=("report.financial_analysis.periods.1.ratios.gross_margin",),
        derived_from=("income-2025",),
        parent_mock=False,
        queried_at=datetime(2025, 7, 1, tzinfo=UTC),
        data_as_of=date(2025, 6, 30),
    )
    assert evidence.source_type == "derived"
    assert evidence.derived_from == ("income-2025",)
    assert evidence.is_mock is False


def test_derived_evidence_rejects_empty_lineage_and_propagates_mock() -> None:
    with pytest.raises(ValueError, match="derived_from"):
        derive_product_evidence(
            evidence_id="derived:x",
            summary="x",
            supports_fields=("report.x",),
            derived_from=(),
            parent_mock=False,
            queried_at=datetime(2025, 7, 1, tzinfo=UTC),
        )
    evidence = derive_product_evidence(
        evidence_id="derived:x",
        summary="x",
        supports_fields=("report.x",),
        derived_from=("mock-parent",),
        parent_mock=True,
        queried_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    assert evidence.is_mock is True
