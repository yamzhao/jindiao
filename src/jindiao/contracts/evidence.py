"""Evidence, source state, and coverage contracts."""

from __future__ import annotations

from collections import Counter
from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from .base import ContractModel


class SourceType(StrEnum):
    TIANYANCHA = "tianyancha"
    PUBLIC_WEB = "public_web"
    MOCK = "mock"
    DERIVED = "derived"
    USER_INPUT = "user_input"


class SourceStatus(StrEnum):
    VERIFIED_RECORDS = "verified_records"
    VERIFIED_EMPTY = "verified_empty"
    CAPABILITY_ABSENT = "capability_absent"
    SOURCE_ERROR = "source_error"
    DEGRADED_MOCK = "degraded_mock"


class CoverageCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class CoverageGapReason(StrEnum):
    PAGINATION_TRUNCATED = "pagination_truncated"
    MISSING_FIELDS = "missing_fields"
    MISSING_PERIOD = "missing_period"
    SINGLE_SOURCE = "single_source"
    SOURCE_UNAVAILABLE = "source_unavailable"


class EvidenceReference(ContractModel):
    evidence_id: str = Field(min_length=1)
    supports_fields: tuple[str, ...] = Field(min_length=1)


class Evidence(ContractModel):
    evidence_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    value: JsonValue
    subject_id: str = Field(min_length=1)
    source_type: SourceType
    source_status: SourceStatus = SourceStatus.VERIFIED_RECORDS
    source_tool: str | None = None
    source_record_id: str | None = None
    queried_at: AwareDatetime
    as_of_date: date | None = None
    confidence: float = Field(ge=0, le=1)
    is_mock: bool
    supports_fields: tuple[str, ...] = Field(min_length=1)
    raw_ref: str = Field(min_length=1)
    source_chain: tuple[str, ...] = ()
    source_title: str | None = None
    source_publisher: str | None = None
    source_parameters_hash: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_source_marking(self) -> Evidence:
        if self.source_type is SourceType.MOCK:
            if not self.is_mock:
                raise ValueError("mock evidence must set is_mock=true")
            if not self.raw_ref.startswith("mock://"):
                raise ValueError("mock evidence must use a mock:// raw_ref")
        if self.source_type is SourceType.TIANYANCHA and self.is_mock:
            raise ValueError("tianyancha evidence cannot be marked as mock")
        if self.source_type is SourceType.PUBLIC_WEB:
            if self.is_mock:
                raise ValueError("public web evidence cannot be marked as mock")
            if not self.raw_ref.startswith(("http://", "https://")):
                raise ValueError("public web evidence must use an HTTP raw_ref")
            if not self.source_title or not self.source_publisher or not self.content_hash:
                raise ValueError("public web evidence requires title, publisher and content hash")
        if self.source_type is SourceType.DERIVED and not self.source_chain:
            raise ValueError("derived evidence requires an explicit source chain")
        return self


class CoverageItem(ContractModel):
    domain: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    status: SourceStatus
    record_count: int = Field(default=0, ge=0)
    error: str | None = None
    fallback_reason: str | None = None
    completeness: CoverageCompleteness = CoverageCompleteness.COMPLETE
    gap_reasons: tuple[CoverageGapReason, ...] = ()

    @model_validator(mode="after")
    def validate_record_count(self) -> CoverageItem:
        if self.status is SourceStatus.VERIFIED_RECORDS and self.record_count == 0:
            raise ValueError("verified_records coverage requires at least one record")
        if (
            self.status
            in {
                SourceStatus.VERIFIED_EMPTY,
                SourceStatus.CAPABILITY_ABSENT,
                SourceStatus.SOURCE_ERROR,
            }
            and self.record_count != 0
        ):
            raise ValueError(f"{self.status.value} coverage cannot have records")
        if self.completeness is CoverageCompleteness.COMPLETE and self.gap_reasons:
            raise ValueError("complete coverage cannot declare gap reasons")
        if self.completeness is CoverageCompleteness.PARTIAL and not self.gap_reasons:
            raise ValueError("partial coverage requires at least one gap reason")
        return self


class CoverageSummary(ContractModel):
    total_items: int = Field(ge=0)
    completed_items: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)
    status_counts: dict[SourceStatus, int]
    items: tuple[CoverageItem, ...]

    @classmethod
    def from_items(cls, items: list[CoverageItem]) -> CoverageSummary:
        counts = Counter(item.status for item in items)
        complete_statuses = {
            SourceStatus.VERIFIED_RECORDS,
            SourceStatus.VERIFIED_EMPTY,
            SourceStatus.DEGRADED_MOCK,
        }
        completed = sum(counts[status] for status in complete_statuses)
        total = len(items)
        return cls(
            total_items=total,
            completed_items=completed,
            ratio=completed / total if total else 0,
            status_counts={status: counts[status] for status in SourceStatus},
            items=tuple(items),
        )


__all__ = [
    "CoverageCompleteness",
    "CoverageGapReason",
    "CoverageItem",
    "CoverageSummary",
    "Evidence",
    "EvidenceReference",
    "SourceStatus",
    "SourceType",
]
