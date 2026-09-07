"""Targeted, bounded repair-task generation."""

from __future__ import annotations

from jindiao.contracts.investigation import Finding, FindingStatus, RepairTask, ReviewIssue


class RepairCoordinator:
    def __init__(self, *, max_rounds: int) -> None:
        if max_rounds < 0:
            raise ValueError("max_rounds cannot be negative")
        self._max_rounds = max_rounds

    def create_tasks(
        self,
        issues: tuple[ReviewIssue, ...],
        *,
        attempt: int,
    ) -> tuple[RepairTask, ...]:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        if attempt > self._max_rounds:
            return ()
        tasks = []
        for issue in issues:
            if issue.resolved:
                continue
            field = (
                issue.issue_type.split(":", 1)[1]
                if issue.issue_type.startswith("evidence_conflict:")
                else issue.issue_type
            )
            tasks.append(
                RepairTask(
                    repair_id=f"repair-{attempt}-{issue.issue_id}",
                    issue_ids=(issue.issue_id,),
                    target_agent=issue.target_agent or "reviewer-agent",
                    requested_fields=(field,),
                    required_evidence=issue.evidence_ids or ("additional_evidence",),
                    attempt=attempt,
                    max_attempts=self._max_rounds,
                )
            )
        return tuple(tasks)

    @staticmethod
    def finalize_unresolved(
        findings: tuple[Finding, ...],
        issues: tuple[ReviewIssue, ...],
    ) -> tuple[Finding, ...]:
        affected = {
            finding_id for issue in issues if not issue.resolved for finding_id in issue.finding_ids
        }
        return tuple(
            finding.model_copy(update={"status": FindingStatus.UNCONFIRMED})
            if finding.finding_id in affected
            else finding
            for finding in findings
        )


__all__ = ["RepairCoordinator"]
