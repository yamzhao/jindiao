from __future__ import annotations

from pathlib import Path

import pytest

from jindiao.contracts.reporting import (
    ReportCatalog,
    ReportModuleDefinition,
    ReportSubmoduleDefinition,
)
from jindiao.reporting.catalog import load_report_catalog

CATALOG_PATH = Path("config/report-catalog-v1.json")


def _load_catalog() -> ReportCatalog:
    return load_report_catalog(CATALOG_PATH)


def test_report_catalog_declares_exactly_eight_modules_and_forty_eight_submodules() -> None:
    catalog = _load_catalog()

    assert catalog.catalog_version == "report-catalog-v1"
    assert catalog.module_count == 8
    assert catalog.submodule_count == 48
    assert catalog.module_ids == (
        "report-summary",
        "risk-summary",
        "company-profile",
        "judicial-risk",
        "operational-risk",
        "operations-analysis",
        "related-parties",
        "peer-analysis",
    )
    assert {module.module_id: len(module.submodules) for module in catalog.modules} == {
        "report-summary": 0,
        "risk-summary": 0,
        "company-profile": 6,
        "judicial-risk": 9,
        "operational-risk": 12,
        "operations-analysis": 13,
        "related-parties": 4,
        "peer-analysis": 4,
    }
    assert len(set(catalog.submodule_ids)) == 48


def test_annual_report_social_security_is_not_a_report_submodule() -> None:
    catalog = _load_catalog()

    assert "annual_reports" in catalog.submodule_ids
    assert "annual_report_social_security" not in catalog.submodule_ids
    assert catalog.module_for_submodule("annual_reports").module_id == "operations-analysis"


def test_report_catalog_rejects_duplicate_submodules_and_declared_count_drift() -> None:
    duplicate = ReportSubmoduleDefinition(submodule_id="registration", title="工商登记")
    with pytest.raises(ValueError, match="submodule ids must be unique"):
        ReportCatalog(
            schema_version=1,
            catalog_version="invalid",
            module_count=1,
            submodule_count=2,
            modules=(
                ReportModuleDefinition(
                    module_id="company-profile",
                    title="企业基本信息",
                    submodules=(duplicate, duplicate),
                ),
            ),
        )

    with pytest.raises(ValueError, match="declared module_count"):
        ReportCatalog(
            schema_version=1,
            catalog_version="invalid",
            module_count=8,
            submodule_count=1,
            modules=(
                ReportModuleDefinition(
                    module_id="company-profile",
                    title="企业基本信息",
                    submodules=(duplicate,),
                ),
            ),
        )
