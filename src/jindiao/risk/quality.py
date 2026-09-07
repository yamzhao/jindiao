"""Deterministic coverage and decision-confidence assessment."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from pydantic import Field

from jindiao.contracts.base import ContractModel
from jindiao.contracts.evidence import (
    CoverageItem,
    CoverageSummary,
    Evidence,
    SourceStatus,
)
from jindiao.contracts.investigation import Finding, FindingStatus, ReviewIssue

_COMPLETE_STATUSES = {
    SourceStatus.VERIFIED_RECORDS,
    SourceStatus.VERIFIED_EMPTY,
    SourceStatus.DEGRADED_MOCK,
}
_GAP_STATUSES = {SourceStatus.CAPABILITY_ABSENT, SourceStatus.SOURCE_ERROR}


class DomainCoverage(ContractModel):
    """Coverage result for one investigation domain."""

    domain: str = Field(min_length=1)
    total_items: int = Field(ge=1)
    completed_items: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)


class QualityAssessment(ContractModel):
    """Inputs that explain the deterministic decision-confidence score."""

    coverage_ratio: float = Field(ge=0, le=1)
    evidence_completeness_ratio: float = Field(ge=0, le=1)
    freshness_ratio: float = Field(ge=0, le=1)
    unresolved_conflict_count: int = Field(ge=0)
    decision_confidence: float = Field(ge=0, le=1)
    domain_coverage: tuple[DomainCoverage, ...]
    pending_review_items: tuple[str, ...]


class QualityCalculator:
    """Calculate quality without asking a model to interpret missing data."""

    def __init__(self, *, freshness_days: int = 365) -> None:
        if freshness_days < 1:
            raise ValueError("freshness_days must be positive")
        self._freshness_days = freshness_days

    def assess(
        self,
        *,
        coverage: CoverageSummary,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        review_issues: tuple[ReviewIssue, ...],
        report_as_of: date,
    ) -> QualityAssessment:
        domain_coverage = self._domain_coverage(coverage.items)
        completeness = self._evidence_completeness(findings, evidence)
        freshness = self._freshness(coverage, evidence, report_as_of)
        unresolved = tuple(issue for issue in review_issues if not issue.resolved)
        conflicts = tuple(
            issue for issue in unresolved if "conflict" in issue.issue_type.casefold()
        )
        conflict_quality = 1.0 if not conflicts else 0.0

        confidence = round(
            (0.40 * coverage.ratio)
            + (0.25 * completeness)
            + (0.20 * freshness)
            + (0.15 * conflict_quality),
            4,
        )
        pending = [
            f"source_gap:{item.domain}:{item.capability}:{item.status.value}"
            for item in coverage.items
            if item.status in _GAP_STATUSES
        ]
        pending.extend(issue.issue_id for issue in unresolved)

        return QualityAssessment(
            coverage_ratio=coverage.ratio,
            evidence_completeness_ratio=completeness,
            freshness_ratio=freshness,
            unresolved_conflict_count=len(conflicts),
            decision_confidence=confidence,
            domain_coverage=domain_coverage,
            pending_review_items=tuple(dict.fromkeys(pending)),
        )

    @staticmethod
    def _domain_coverage(items: tuple[CoverageItem, ...]) -> tuple[DomainCoverage, ...]:
        grouped: dict[str, list[CoverageItem]] = defaultdict(list)
        for item in items:
            grouped[item.domain].append(item)
        return tuple(
            DomainCoverage(
                domain=domain,
                total_items=len(domain_items),
                completed_items=sum(item.status in _COMPLETE_STATUSES for item in domain_items),
                ratio=sum(item.status in _COMPLETE_STATUSES for item in domain_items)
                / len(domain_items),
            )
            for domain, domain_items in sorted(grouped.items())
        )

    @staticmethod
    def _evidence_completeness(
        findings: tuple[Finding, ...], evidence: tuple[Evidence, ...]
    ) -> float:
        accepted = tuple(
            finding for finding in findings if finding.status is FindingStatus.ACCEPTED
        )
        if not accepted:
            return 1.0
        available = {item.evidence_id for item in evidence}
        backed = sum(set(finding.evidence_ids) <= available for finding in accepted)
        return backed / len(accepted)

    def _freshness(
        self,
        coverage: CoverageSummary,
        evidence: tuple[Evidence, ...],
        report_as_of: date,
    ) -> float:
        if not evidence:
            return float(
                bool(coverage.items)
                and all(
                    item.status in {SourceStatus.VERIFIED_EMPTY, SourceStatus.DEGRADED_MOCK}
                    for item in coverage.items
                )
            )
        fresh = 0
        for item in evidence:
            effective_date = item.as_of_date or item.queried_at.date()
            age = (report_as_of - effective_date).days
            fresh += 0 <= age <= self._freshness_days
        return fresh / len(evidence)


__all__ = ["DomainCoverage", "QualityAssessment", "QualityCalculator"]
