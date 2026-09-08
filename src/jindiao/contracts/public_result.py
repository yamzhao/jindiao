"""Version dispatch for stored results; legacy payloads are read without conversion."""

from __future__ import annotations

from .product import ProductResult
from .results import DueDiligenceResult as LegacyDueDiligenceResult

PublicResult = ProductResult | LegacyDueDiligenceResult


def parse_public_result(value: object) -> PublicResult:
    if isinstance(value, dict) and "schema_version" in value:
        return ProductResult.model_validate(value)
    return LegacyDueDiligenceResult.model_validate(value)
