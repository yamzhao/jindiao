"""Validated manifest contract for immutable local scenarios."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from jindiao.contracts.base import ContractModel


class ScenarioFileRole(StrEnum):
    """Controls which process may read a manifest file."""

    RUNTIME = "runtime"
    EXPECTED = "expected"


class EnterpriseKey(ContractModel):
    """Stable identifiers accepted when selecting a scenario."""

    company_name: str | None = None
    unified_social_credit_code: str | None = None
    aliases: tuple[str, ...] = ()

    @field_validator("company_name", "unified_social_credit_code")
    @classmethod
    def strip_optional_identifier(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(alias.strip() for alias in aliases if alias.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("enterprise aliases must be unique")
        return normalized

    @model_validator(mode="after")
    def require_identifier(self) -> EnterpriseKey:
        if not self.company_name and not self.unified_social_credit_code:
            raise ValueError("enterprise key requires a company name or credit code")
        return self


class ScenarioFile(ContractModel):
    """One hash-pinned file declared by a scenario manifest."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: ScenarioFileRole = ScenarioFileRole.RUNTIME

    @model_validator(mode="after")
    def validate_safe_role_path(self) -> ScenarioFile:
        pure_path = PurePosixPath(self.path)
        if (
            "\\" in self.path
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or "." in pure_path.parts
            or self.path != pure_path.as_posix()
        ):
            raise ValueError("scenario file path must be a normalized relative POSIX path")
        is_expected = pure_path.parts[0] == "expected"
        if is_expected != (self.role is ScenarioFileRole.EXPECTED):
            raise ValueError("scenario file role must agree with its expected/ path")
        if self.path == "manifest.json":
            raise ValueError("manifest cannot list itself")
        return self


class ScenarioManifest(ContractModel):
    """Versioned, content-addressed manifest for one enterprise scenario."""

    model_config = ContractModel.model_config | {
        "json_schema_extra": {
            "$id": "https://openjiuwen.example/schemas/jindiao/scenario-manifest-v1.json"
        }
    }

    schema_version: Literal[1]
    scenario_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    enterprise_key: EnterpriseKey
    version: str = Field(pattern=r"^v\d+(?:\.\d+){0,2}$")
    as_of_date: date
    files: tuple[ScenarioFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_file_paths(self) -> ScenarioManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("scenario file paths must be unique")
        if not any(item.role is ScenarioFileRole.RUNTIME for item in self.files):
            raise ValueError("scenario must declare at least one runtime file")
        return self

    @property
    def runtime_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files if item.role is ScenarioFileRole.RUNTIME)

    @property
    def expected_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files if item.role is ScenarioFileRole.EXPECTED)


__all__ = ["EnterpriseKey", "ScenarioFile", "ScenarioFileRole", "ScenarioManifest"]
