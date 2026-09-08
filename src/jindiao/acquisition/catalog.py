"""Load the product acquisition catalog."""

from __future__ import annotations

from pathlib import Path

from jindiao.contracts.acquisition_catalog import AcquisitionCatalog
from jindiao.contracts.product import ProductReport
from jindiao.paths import project_root

DEFAULT_ACQUISITION_CATALOG_PATH = project_root() / "config" / "acquisition-catalog-v1.json"


def load_acquisition_catalog(
    path: Path = DEFAULT_ACQUISITION_CATALOG_PATH,
) -> AcquisitionCatalog:
    catalog = AcquisitionCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    for item in catalog.items:
        for path_value in item.report_fields:
            parts = path_value.split(".")
            if len(parts) not in {2, 3} or parts[0] != "report":
                raise ValueError(f"invalid product report field path: {path_value}")
            module = ProductReport.model_fields.get(parts[1])
            if module is None:
                raise ValueError(f"unknown product report module: {path_value}")
            module_type = module.annotation
            module_fields = getattr(module_type, "model_fields", {})
            if len(parts) == 3 and (
                not isinstance(module_fields, dict) or parts[2] not in module_fields
            ):
                raise ValueError(f"unknown product report field: {path_value}")
    return catalog


ACQUISITION_CATALOG = load_acquisition_catalog()

__all__ = [
    "ACQUISITION_CATALOG",
    "DEFAULT_ACQUISITION_CATALOG_PATH",
    "load_acquisition_catalog",
]
