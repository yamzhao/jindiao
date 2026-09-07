"""Whitelisted reporting policy and feedback command contracts (not HTTP routes)."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from .base import ContractModel

PolicyVersion = Annotated[StrictStr, Field(pattern=r"^1\.1\.(0|[1-9][0-9]*)$", max_length=64)]
Sha256 = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
SectionId = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9-]*$")]
EvidenceId = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class ReportPolicy(ContractModel):
    """Only placement may evolve; prompts, facts and risk rules are not policy fields."""

    schema_version: Literal[1] = 1
    gap_placement: Literal["appendix_only", "section_and_appendix"] = "appendix_only"

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_schema(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReportingPolicyBinding(ContractModel):
    """Serializable immutable binding intended to be captured at Run acceptance."""

    version: PolicyVersion
    revision: StrictInt = Field(ge=0)
    policy: ReportPolicy
    policy_sha256: Sha256

    @classmethod
    def freeze(cls, policy: ReportPolicy, *, version: str, revision: int) -> Self:
        return cls(version=version, revision=revision, policy=policy, policy_sha256=policy.sha256)

    @model_validator(mode="after")
    def require_matching_hash(self) -> Self:
        if self.policy_sha256 != self.policy.sha256:
            raise ValueError("reporting policy hash mismatch")
        return self


class ReportFeedbackRequest(ContractModel):
    kind: Literal["gap_disclosure_placement"]
    text: StrictStr = Field(min_length=1, max_length=2000)
    target_section_ids: tuple[SectionId, ...] = Field(min_length=1, max_length=8)
    evidence_ids: tuple[EvidenceId, ...] = Field(default=(), max_length=32)
    source: StrictStr = Field(default="user_review", min_length=1, max_length=80)

    @field_validator("text", "source")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("feedback text and source must not be blank")
        return value

    @field_validator("target_section_ids", "evidence_ids")
    @classmethod
    def require_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(not value.strip() for value in values):
            raise ValueError("feedback references must be unique and nonblank")
        return values


class _ReleaseRequest(ContractModel):
    expected_active_revision: StrictInt = Field(ge=0)
    scope: Literal["workspace"]
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("release reason must not be blank")
        return value


class ActivateReportingPolicyRequest(_ReleaseRequest):
    expected_evaluation_sha256: Sha256


class RollbackReportingPolicyRequest(_ReleaseRequest):
    target_version: PolicyVersion


__all__ = [
    "ActivateReportingPolicyRequest",
    "ReportFeedbackRequest",
    "ReportPolicy",
    "ReportingPolicyBinding",
    "RollbackReportingPolicyRequest",
]
