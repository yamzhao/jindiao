"""Versioned report catalog loading and production-shape validation."""

from __future__ import annotations

from pathlib import Path

from jindiao.contracts.reporting import ReportCatalog
from jindiao.paths import project_root

DEFAULT_REPORT_CATALOG_PATH = project_root() / "config" / "report-catalog-v1.json"

EXPECTED_REPORT_MODULE_COUNTS = {
    "report-summary": 0,
    "risk-summary": 0,
    "company-profile": 6,
    "judicial-risk": 9,
    "operational-risk": 12,
    "operations-analysis": 13,
    "related-parties": 4,
    "peer-analysis": 4,
}


def load_report_catalog(path: Path = DEFAULT_REPORT_CATALOG_PATH) -> ReportCatalog:
    catalog = ReportCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    actual = {module.module_id: len(module.submodules) for module in catalog.modules}
    if actual != EXPECTED_REPORT_MODULE_COUNTS:
        raise ValueError(
            "report catalog must preserve the canonical 8-module/48-submodule distribution"
        )
    if catalog.module_count != 8 or catalog.submodule_count != 48:
        raise ValueError("report catalog must contain exactly 8 modules and 48 submodules")
    if "annual_report_social_security" in catalog.submodule_ids:
        raise ValueError("annual-report social-security data must not create a report submodule")
    if catalog.module_for_submodule("annual_reports").module_id != "operations-analysis":
        raise ValueError("annual_reports must belong to operations-analysis")
    return catalog


REPORT_CATALOG = load_report_catalog()


__all__ = [
    "DEFAULT_REPORT_CATALOG_PATH",
    "EXPECTED_REPORT_MODULE_COUNTS",
    "REPORT_CATALOG",
    "load_report_catalog",
]
