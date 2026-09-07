"""Capability-aware bounded investigation planning."""

from __future__ import annotations

from collections.abc import Mapping, Set

from jindiao.contracts.entities import ResolvedSubject
from jindiao.contracts.investigation import (
    InvestigationPlan,
    InvestigationTask,
    TaskStatus,
)

from .base import RunBudget

_ASSIGNEES = {
    "governance": "governance-agent",
    "judicial": "judicial-compliance-agent",
    "operations": "operations-peer-agent",
    "peers": "operations-peer-agent",
}


class CapabilityAwarePlanner:
    """Produce an explicit DAG; unavailable work remains visible as skipped."""

    def create_plan(
        self,
        *,
        subject: ResolvedSubject,
        domain_capabilities: Mapping[str, tuple[str, ...]],
        mock_domains: Set[str],
        budget: RunBudget,
    ) -> InvestigationPlan:
        tasks: list[InvestigationTask] = []
        review_dependencies: list[str] = []
        for domain, specialist in _ASSIGNEES.items():
            task_id = f"investigate-{domain}"
            capabilities = domain_capabilities.get(domain, ())
            if capabilities:
                task = InvestigationTask(
                    task_id=task_id,
                    domain=domain,
                    assigned_agent=specialist,
                    capability=",".join(capabilities),
                    inputs={"subject_id": subject.subject_id},
                    expected_outputs=("evidence", "findings", "coverage"),
                )
                review_dependencies.append(task_id)
            elif domain in mock_domains:
                task = InvestigationTask(
                    task_id=task_id,
                    domain=domain,
                    assigned_agent="deepsearch-agent",
                    capability="mock_fallback",
                    inputs={"subject_id": subject.subject_id},
                    expected_outputs=("mock_evidence", "coverage"),
                )
                review_dependencies.append(task_id)
            else:
                task = InvestigationTask(
                    task_id=task_id,
                    domain=domain,
                    assigned_agent=specialist,
                    inputs={"subject_id": subject.subject_id},
                    expected_outputs=("coverage_gap",),
                    status=TaskStatus.SKIPPED,
                    skipped_reason="capability_and_mock_absent",
                )
            tasks.append(task)
        tasks.append(
            InvestigationTask(
                task_id="review-evidence",
                domain="review",
                assigned_agent="reviewer-agent",
                expected_outputs=("review_decision", "review_issues"),
                dependencies=tuple(review_dependencies),
            )
        )
        return InvestigationPlan(
            plan_id=f"plan:{subject.subject_id}",
            subject_id=subject.subject_id,
            tasks=tuple(tasks),
            max_tool_calls=budget.max_tool_calls,
            max_concurrency=budget.max_concurrency,
        )


__all__ = ["CapabilityAwarePlanner"]
