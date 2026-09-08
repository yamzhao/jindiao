from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

CHECK_CATALOG_PATH = Path("config/due-diligence-check-catalog-v1.json")


def _catalog_module() -> Any:
    try:
        from jindiao.investigation import catalog
    except ImportError:
        pytest.fail("DueDiligenceCheckCatalog loader is not implemented", pytrace=False)
    return catalog


def test_check_catalog_covers_fixed_due_diligence_decisions() -> None:
    module = _catalog_module()
    catalog = module.load_check_catalog(CHECK_CATALOG_PATH)

    assert catalog.catalog_version == "due-diligence-check-catalog-v1"
    assert catalog.acquisition_catalog_version == "acquisition-catalog-v1"
    assert set(catalog.check_ids) >= {
        "registration-status-normal",
        "registration-change-anomaly",
        "ownership-control-risk",
        "material-execution-risk",
        "dishonesty-and-consumption-restriction",
        "operation-abnormality",
        "administrative-compliance-penalties",
        "equity-encumbrance",
        "profitability-decline",
        "solvency-pressure",
        "cash-flow-pressure",
        "revenue-anomaly",
        "related-party-control-risk",
        "employment-scale-consistency",
        "supply-chain-concentration",
        "receivables-cashflow-divergence",
        "related-transaction-guarantee",
        "tax-public-opinion-risk",
        "bank-credit-summary-risk",
    }
    assert all(check.missing_data_policy.value == "inconclusive" for check in catalog.checks)


def test_check_catalog_uses_annual_report_social_security_only_where_relevant() -> None:
    module = _catalog_module()
    catalog = module.load_check_catalog(CHECK_CATALOG_PATH)

    profitability = catalog.get("profitability-decline")
    assert profitability.required_submodule_ids == ("financial_summary", "income_statement")
    assert "annual_reports" in profitability.optional_submodule_ids

    employment = catalog.get("employment-scale-consistency")
    assert employment.required_submodule_ids == ("annual_reports",)
    assert employment.owner_role == "financial-operations"


def test_check_catalog_rejects_unknown_acquisition_item(
    tmp_path: Path,
) -> None:
    module = _catalog_module()
    payload = json.loads(CHECK_CATALOG_PATH.read_text(encoding="utf-8"))
    payload["checks"][0]["required_submodule_ids"] = ["unknown_submodule"]
    invalid_path = tmp_path / "invalid-check-catalog.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown acquisition items"):
        module.load_check_catalog(invalid_path)


def test_check_catalog_rejects_duplicate_check_ids(tmp_path: Path) -> None:
    module = _catalog_module()
    payload = json.loads(CHECK_CATALOG_PATH.read_text(encoding="utf-8"))
    payload["checks"].append(dict(payload["checks"][0]))
    invalid_path = tmp_path / "duplicate-check-catalog.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="check ids must be unique"):
        module.load_check_catalog(invalid_path)
