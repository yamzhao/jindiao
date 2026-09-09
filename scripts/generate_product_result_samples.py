"""Regenerate the documented prototype-v1 response samples without network access."""
# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.results import DueDiligenceRequest
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 7, tzinfo=UTC)
COMPANY_NAME = "乐视网信息技术（北京）股份有限公司"
OUTPUT_ROOT = Path("docs/api/samples")

FULL_BUSINESS_CONTEXT: dict[str, object] = {
    "reporting_org": "城东支行",
    "reporting_date": "2026-09-07",
    "business_product": "流动资金贷款",
    "customer_manager": "王某某",
    "application_type": "新增授信",
    "application_amount": 50_000_000,
    "application_term_months": 12,
    "fund_use": "采购原材料",
    "suggested_amount": 40_000_000,
    "suggested_interest_rate": "LPR + 85BP",
    "suggested_credit_term_months": 36,
    "suggested_loan_term_months": 12,
    "fund_use_detail": "用于采购原材料及日常经营周转",
    "guarantee_methods": ["actual_controller"],
    "repayment_methods": ["monthly_interest_bullet_principal"],
    "repayment_source": "主营业务销售回款",
    "unified_credit": "本笔纳入统一授信管理",
    "investigation_location": "企业主要生产经营场所",
    "bank_flow": {
        "period_start": "2026-01-01",
        "period_end": "2026-06-30",
        "account_count": 2,
        "transaction_count": 168,
        "total_inflow": 62_000_000,
        "total_outflow": 59_000_000,
        "operating_receipts": 55_000_000,
        "revenue_comparison_amount": 60_000_000,
        "monthly_totals": [
            {
                "month": "2026-01",
                "inflow": 9_000_000,
                "outflow": 8_500_000,
                "operating_receipts": 8_000_000,
            },
            {
                "month": "2026-02",
                "inflow": 9_500_000,
                "outflow": 9_000_000,
                "operating_receipts": 8_500_000,
            },
            {
                "month": "2026-03",
                "inflow": 10_000_000,
                "outflow": 9_500_000,
                "operating_receipts": 9_000_000,
            },
            {
                "month": "2026-04",
                "inflow": 10_500_000,
                "outflow": 10_000_000,
                "operating_receipts": 9_500_000,
            },
            {
                "month": "2026-05",
                "inflow": 11_000_000,
                "outflow": 10_500_000,
                "operating_receipts": 10_000_000,
            },
            {
                "month": "2026-06",
                "inflow": 12_000_000,
                "outflow": 11_500_000,
                "operating_receipts": 10_000_000,
            },
        ],
        "repayment_gap_inputs": {
            "period": "2026-H1",
            "operating_cash_flow": 8_000_000,
            "available_cash": 5_000_000,
            "unused_credit": 10_000_000,
            "short_term_debt": 12_000_000,
            "guarantee_exposure": 0,
            "assumptions": "静态测算；担保敞口按或有代偿全额计入",
        },
        "source_reference": "customer-manager://bank-flow/2026-H1",
    },
    "credit_info": {
        "as_of_date": "2026-06-30",
        "bank_count": 3,
        "total_credit_limit": 65_000_000,
        "used_credit_amount": 40_300_000,
        "overdue_count": 0,
        "source_reference": "customer-manager://credit-summary/2026-06-30",
    },
    "internal_record": {
        "as_of_date": "2026-06-30",
        "is_first_credit": False,
        "relationship_summary": "存量客户，历史合作正常",
        "source_reference": "customer-manager://internal-record/2026-06-30",
    },
}


async def generate() -> None:
    cases: tuple[tuple[str, str, str, dict[str, object]], ...] = (
        (
            "product-result-full-input.json",
            "sample-complete-request",
            "sample-complete-run",
            FULL_BUSINESS_CONTEXT,
        ),
        (
            "product-result-missing-input.json",
            "sample-partial-request",
            "sample-partial-run",
            {},
        ),
    )
    with TemporaryDirectory(prefix="jindiao-product-samples-") as temporary:
        for filename, request_id, run_id, business_context in cases:
            service = DueDiligenceService(
                settings=Settings(  # type: ignore[call-arg]
                    _env_file=None,
                    model_provider="offline_mock",
                    model_name="deterministic-sample",
                    agent_runtime_mode="deterministic_harness",
                    data_source_mode="mock",
                    artifact_root=Path(temporary) / run_id,
                ),
                scenarios=ScenarioRepository(Path("mock_data/scenarios")),
                clock=lambda: NOW,
            )
            result = await service.run(
                DueDiligenceRequest.model_validate(
                    {
                        "enterprise": {"company_name": COMPANY_NAME},
                        "scenario_id": "normal-enterprise",
                        "business_context": business_context,
                    }
                ),
                request_id=request_id,
                run_id=run_id,
            )
            (OUTPUT_ROOT / filename).write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


if __name__ == "__main__":
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    asyncio.run(generate())
