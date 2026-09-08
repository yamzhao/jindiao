"""Versioned acquisition plan independent from the public report shape."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel


class AcquisitionItem(ContractModel):
    acquisition_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    domain: Literal["governance", "judicial", "operations", "relationships", "peers"]
    report_fields: tuple[str, ...] = Field(min_length=1)
    default_enabled: bool = True

    @model_validator(mode="after")
    def validate_report_fields(self) -> AcquisitionItem:
        if len(self.report_fields) != len(set(self.report_fields)):
            raise ValueError("acquisition report fields must be unique")
        return self


class AcquisitionCatalog(ContractModel):
    schema_version: Literal[1]
    catalog_version: str = Field(min_length=1)
    items: tuple[AcquisitionItem, ...] = Field(min_length=1)

    @property
    def acquisition_ids(self) -> tuple[str, ...]:
        return tuple(item.acquisition_id for item in self.items)

    @property
    def default_plan_ids(self) -> tuple[str, ...]:
        return tuple(item.acquisition_id for item in self.items if item.default_enabled)

    def get(self, acquisition_id: str) -> AcquisitionItem:
        for item in self.items:
            if item.acquisition_id == acquisition_id:
                return item
        raise KeyError(acquisition_id)

    def plan(self, required_ids: tuple[str, ...] = ()) -> tuple[str, ...]:
        unknown = set(required_ids) - set(self.acquisition_ids)
        if unknown:
            raise ValueError(f"unknown acquisition dependencies: {sorted(unknown)}")
        requested = {*self.default_plan_ids, *required_ids}
        return tuple(item for item in self.acquisition_ids if item in requested)

    @model_validator(mode="after")
    def validate_identity(self) -> AcquisitionCatalog:
        if len(self.acquisition_ids) != len(set(self.acquisition_ids)):
            raise ValueError("acquisition ids must be unique")
        return self


__all__ = ["AcquisitionCatalog", "AcquisitionItem"]
