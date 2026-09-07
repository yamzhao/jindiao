"""Load and validate the fixed due-diligence check catalog."""

from __future__ import annotations

from pathlib import Path

from jindiao.contracts.investigation import DueDiligenceCheckCatalog
from jindiao.contracts.reporting import ReportCatalog
from jindiao.paths import project_root
from jindiao.reporting.catalog import REPORT_CATALOG

DEFAULT_CHECK_CATALOG_PATH = project_root() / "config" / "due-diligence-check-catalog-v1.json"

REQUIRED_CHECK_IDS = frozenset(
    {
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
        "peer-performance-deviation",
        "employment-scale-consistency",
    }
)


def load_check_catalog(
    path: Path = DEFAULT_CHECK_CATALOG_PATH,
    *,
    report_catalog: ReportCatalog = REPORT_CATALOG,
) -> DueDiligenceCheckCatalog:
    catalog = DueDiligenceCheckCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    if catalog.report_catalog_version != report_catalog.catalog_version:
        raise ValueError("check catalog report_catalog_version does not match ReportCatalog")

    known_submodules = set(report_catalog.submodule_ids)
    known_sections = set(report_catalog.module_ids)
    for check in catalog.checks:
        unknown_submodules = (
            set(check.required_submodule_ids) | set(check.optional_submodule_ids)
        ) - known_submodules
        if unknown_submodules:
            raise ValueError(
                f"check {check.check_id} references unknown report submodules: "
                f"{sorted(unknown_submodules)}"
            )
        unknown_sections = set(check.report_section_ids) - known_sections
        if unknown_sections:
            raise ValueError(
                f"check {check.check_id} references unknown report sections: "
                f"{sorted(unknown_sections)}"
            )

    missing_checks = REQUIRED_CHECK_IDS - set(catalog.check_ids)
    if missing_checks:
        raise ValueError(f"check catalog is missing required checks: {sorted(missing_checks)}")
    return catalog


CHECK_CATALOG = load_check_catalog()


__all__ = [
    "CHECK_CATALOG",
    "DEFAULT_CHECK_CATALOG_PATH",
    "REQUIRED_CHECK_IDS",
    "load_check_catalog",
]
