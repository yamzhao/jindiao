from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.investigation import (
    Finding,
    FindingStatus,
    InvestigationPlan,
    InvestigationTask,
    RepairTask,
    ReviewDecision,
    ReviewIssue,
    RiskClass,
    Severity,
    TaskStatus,
)


def test_plan_requires_unique_tasks_and_known_dependencies() -> None:
    identity = InvestigationTask(
        task_id="identity",
        domain="identity",
        assigned_agent="leader",
        expected_outputs=("subject",),
    )
    judicial = InvestigationTask(
        task_id="judicial",
        domain="judicial",
        assigned_agent="judicial-agent",
        expected_outputs=("findings",),
        dependencies=("identity",),
    )

    plan = InvestigationPlan(
        plan_id="plan-1",
        subject_id="tyc:123",
        tasks=(identity, judicial),
        max_tool_calls=20,
        max_concurrency=2,
    )

    assert plan.tasks[1].dependencies == ("identity",)
    with pytest.raises(ValidationError):
        InvestigationPlan(
            plan_id="bad",
            subject_id="tyc:123",
            tasks=(judicial,),
            max_tool_calls=20,
            max_concurrency=2,
        )


def test_skipped_task_requires_a_reason() -> None:
    with pytest.raises(ValidationError):
        InvestigationTask(
            task_id="peer",
            domain="peer",
            assigned_agent="operations-agent",
            expected_outputs=("peer_metrics",),
            status=TaskStatus.SKIPPED,
        )


def test_accepted_finding_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        Finding(
            finding_id="finding-1",
            subject_id="tyc:123",
            domain="judicial",
            claim="企业存在被执行记录",
            risk_class=RiskClass.ATTENTION,
            severity=Severity.HIGH,
            status=FindingStatus.ACCEPTED,
            evidence_ids=(),
        )


def test_repair_task_attempt_cannot_exceed_budget() -> None:
    with pytest.raises(ValidationError):
        RepairTask(
            repair_id="repair-1",
            issue_ids=("issue-1",),
            target_agent="judicial-agent",
            requested_fields=("case_date",),
            required_evidence=("裁判日期",),
            attempt=3,
            max_attempts=2,
        )


def test_review_decision_keeps_accepted_and_unresolved_sets_disjoint() -> None:
    issue = ReviewIssue(
        issue_id="issue-1",
        issue_type="conflict",
        message="金额不一致",
        finding_ids=("finding-1", "finding-2"),
        evidence_ids=("ev-1", "ev-2"),
        target_agent="judicial-agent",
    )
    assert issue.resolved is False

    decision = ReviewDecision(
        accepted_finding_ids=("finding-1",),
        rejected_finding_ids=("finding-2",),
        unresolved_issue_ids=("issue-1",),
        reviewed_at=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert decision.accepted_finding_ids == ("finding-1",)

    with pytest.raises(ValidationError):
        ReviewDecision(
            accepted_finding_ids=("finding-1",),
            rejected_finding_ids=("finding-1",),
            unresolved_issue_ids=(),
            reviewed_at=datetime(2026, 9, 3, tzinfo=UTC),
        )
