"""Enterprise input and resolved subject contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .base import ContractModel


class SubjectSource(StrEnum):
    TIANYANCHA = "tianyancha"
    MOCK = "mock"


class EnterpriseInput(ContractModel):
    company_name: str | None = None
    unified_social_credit_code: str | None = None
    region: str | None = None

    @field_validator("company_name", "unified_social_credit_code", "region", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("unified_social_credit_code")
    @classmethod
    def normalize_credit_code(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @model_validator(mode="after")
    def require_identifier(self) -> EnterpriseInput:
        if self.company_name is None and self.unified_social_credit_code is None:
            raise ValueError("company_name or unified_social_credit_code is required")
        return self


class CandidateSubject(ContractModel):
    subject_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    unified_social_credit_code: str | None = None
    region: str | None = None
    registration_status: str | None = None
    match_score: float = Field(ge=0, le=1)


class ResolvedSubject(ContractModel):
    subject_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    unified_social_credit_code: str | None = None
    region: str | None = None
    registration_status: str | None = None
    source: SubjectSource
    resolved_at: AwareDatetime


__all__ = ["CandidateSubject", "EnterpriseInput", "ResolvedSubject", "SubjectSource"]
