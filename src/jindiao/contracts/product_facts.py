"""Typed boundary between source normalization and product report generation."""

from .base import ContractModel
from .product import MissingField, ProductEvidence, ProductReport


class ProductFactBundle(ContractModel):
    report: ProductReport
    evidence: tuple[ProductEvidence, ...]
    verified_empty_fields: tuple[str, ...] = ()
    source_missing_fields: tuple[MissingField, ...] = ()
