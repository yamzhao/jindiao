"""Independent deterministic reviewer for Evidence and proposed Findings."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime

from pydantic import Field, JsonValue

from jindiao.contracts.base import ContractModel
from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.evidence import Evidence
from jindiao.contracts.investigation import (
    Finding,
    FindingStatus,
    ReviewDecision,
    ReviewIssue,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _issue_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _has_negative_amount(value: object, *, amount_key: bool = False) -> bool:
    if isinstance(value, Mapping):
        return any(
            _has_negative_amount(item, amount_key="amount" in str(key).casefold())
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_negative_amount(item, amount_key=amount_key) for item in value)
    return (
        amount_key and isinstance(value, int | float) and not isinstance(value, bool) and value < 0
    )


def _target_agent(domain: str) -> str:
    if domain == "governance":
        return "governance-agent"
    if domain == "judicial":
        return "judicial-compliance-agent"
    return "operations-peer-agent"


class ConflictGroup(ContractModel):
    conflict_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=2)
    finding_ids: tuple[str, ...]
    values: tuple[JsonValue, ...] = Field(min_length=2)


class ReviewOutcome(ContractModel):
    findings: tuple[Finding, ...]
    issues: tuple[ReviewIssue, ...]
    conflicts: tuple[ConflictGroup, ...]
    decision: ReviewDecision


class EvidenceReviewer:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock

    def review(
        self,
        *,
        subject: ResolvedSubject,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
        report_as_of: date,
    ) -> ReviewOutcome:
        issues: list[ReviewIssue] = []
        blocked: set[str] = set()
        evidence_by_id = {item.evidence_id: item for item in evidence}
        findings_by_evidence: dict[str, list[str]] = defaultdict(list)
        for finding in findings:
            for evidence_id in finding.evidence_ids:
                findings_by_evidence[evidence_id].append(finding.finding_id)

        conflicts = self._conflicts(subject, evidence, findings_by_evidence)
        for conflict in conflicts:
            blocked.update(conflict.finding_ids)
            issues.append(
                ReviewIssue(
                    issue_id=conflict.conflict_id,
                    issue_type=f"evidence_conflict:{conflict.field}",
                    message=f"Conflicting values for {conflict.field}",
                    finding_ids=conflict.finding_ids,
                    evidence_ids=conflict.evidence_ids,
                    target_agent=_target_agent(conflict.field.split(".", 1)[0]),
                )
            )

        for finding in findings:
            unknown = set(finding.evidence_ids) - set(evidence_by_id)
            if not finding.evidence_ids or unknown:
                blocked.add(finding.finding_id)
                issues.append(
                    ReviewIssue(
                        issue_id=_issue_id("insufficient", finding.finding_id),
                        issue_type="insufficient_evidence",
                        message="Finding lacks resolvable evidence",
                        finding_ids=(finding.finding_id,),
                        evidence_ids=tuple(sorted(unknown)),
                        target_agent=_target_agent(finding.domain),
                    )
                )
            if finding.subject_id != subject.subject_id:
                blocked.add(finding.finding_id)
                issues.append(
                    ReviewIssue(
                        issue_id=_issue_id("subject", finding.finding_id),
                        issue_type="subject_mismatch",
                        message="Finding subject differs from resolved subject",
                        finding_ids=(finding.finding_id,),
                        evidence_ids=finding.evidence_ids,
                        target_agent=_target_agent(finding.domain),
                    )
                )

        for item in evidence:
            impacted = tuple(findings_by_evidence[item.evidence_id])
            domain = item.supports_fields[0].split(".", 1)[0]
            if item.subject_id != subject.subject_id:
                blocked.update(impacted)
            if item.as_of_date is not None and item.as_of_date > report_as_of:
                blocked.update(impacted)
                issues.append(
                    ReviewIssue(
                        issue_id=_issue_id("future", item.evidence_id),
                        issue_type="future_evidence",
                        message="Evidence is newer than the frozen report date",
                        finding_ids=impacted,
                        evidence_ids=(item.evidence_id,),
                        target_agent=_target_agent(domain),
                    )
                )
            if _has_negative_amount(item.value):
                blocked.update(impacted)
                issues.append(
                    ReviewIssue(
                        issue_id=_issue_id("amount", item.evidence_id),
                        issue_type="invalid_amount",
                        message="Evidence contains a negative amount",
                        finding_ids=impacted,
                        evidence_ids=(item.evidence_id,),
                        target_agent=_target_agent(domain),
                    )
                )

        duplicate_groups: dict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            duplicate_groups[
                _canonical(
                    {
                        "subject_id": finding.subject_id,
                        "domain": finding.domain,
                        "claim": finding.claim,
                        "value": finding.value,
                    }
                )
            ].append(finding)
        for group in duplicate_groups.values():
            if len(group) < 2:
                continue
            ids = tuple(item.finding_id for item in group)
            blocked.update(ids)
            issues.append(
                ReviewIssue(
                    issue_id=_issue_id("duplicate", *ids),
                    issue_type="duplicate_finding",
                    message="Semantically duplicate Findings require consolidation",
                    finding_ids=ids,
                    evidence_ids=tuple(
                        dict.fromkeys(
                            evidence_id for item in group for evidence_id in item.evidence_ids
                        )
                    ),
                    target_agent=_target_agent(group[0].domain),
                )
            )

        reviewed = tuple(
            finding.model_copy(
                update={
                    "status": (
                        FindingStatus.UNCONFIRMED
                        if finding.finding_id in blocked
                        else FindingStatus.ACCEPTED
                    )
                }
            )
            for finding in findings
        )
        accepted_ids = tuple(
            item.finding_id for item in reviewed if item.status is FindingStatus.ACCEPTED
        )
        return ReviewOutcome(
            findings=reviewed,
            issues=tuple(issues),
            conflicts=conflicts,
            decision=ReviewDecision(
                accepted_finding_ids=accepted_ids,
                rejected_finding_ids=(),
                unresolved_issue_ids=tuple(item.issue_id for item in issues),
                reviewed_at=self._clock(),
            ),
        )

    @staticmethod
    def _conflicts(
        subject: ResolvedSubject,
        evidence: tuple[Evidence, ...],
        findings_by_evidence: Mapping[str, list[str]],
    ) -> tuple[ConflictGroup, ...]:
        by_field: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            for field in item.supports_fields:
                by_field[field].append(item)
        conflicts: list[ConflictGroup] = []
        for field, items in sorted(by_field.items()):
            values = {_canonical(item.value) for item in items}
            if len(items) < 2 or len(values) < 2:
                continue
            evidence_ids = tuple(item.evidence_id for item in items)
            finding_ids = tuple(
                dict.fromkeys(
                    finding_id
                    for evidence_id in evidence_ids
                    for finding_id in findings_by_evidence.get(evidence_id, [])
                )
            )
            conflicts.append(
                ConflictGroup(
                    conflict_id=_issue_id("conflict", subject.subject_id, field, *evidence_ids),
                    subject_id=subject.subject_id,
                    field=field,
                    evidence_ids=evidence_ids,
                    finding_ids=finding_ids,
                    values=tuple(item.value for item in items),
                )
            )
        return tuple(conflicts)


__all__ = ["ConflictGroup", "EvidenceReviewer", "ReviewOutcome"]
