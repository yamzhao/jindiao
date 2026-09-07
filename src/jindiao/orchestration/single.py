"""Sequential single-agent baseline using the shared investigation toolset."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from jindiao.application.context import RunContext
from jindiao.contracts.evidence import CoverageSummary
from jindiao.contracts.investigation import FindingStatus, ReviewIssue
from jindiao.contracts.results import (
    AgentStatus,
    AgentTrace,
    CollaborationSummary,
    OrchestrationMode,
)

from .base import (
    BudgetLedger,
    CancellationToken,
    InvestigationToolset,
    OrchestrationOutcome,
    RunBudget,
    RuntimeEventSink,
    check_cancellation,
    emit_runtime_event,
    investigate_with_cancellation,
    merge_section_data,
    require_deterministic_harness,
    resolve_subject_with_cancellation,
)

_DOMAINS = ("governance", "judicial", "operations", "peers")


class SingleAgentStrategy:
    """Non-formal deterministic baseline retained for fixtures and regression."""

    formal_agent_run = False

    def __init__(
        self,
        *,
        toolset: InvestigationToolset,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._toolset = toolset
        self._clock = clock

    @property
    def mode(self) -> OrchestrationMode:
        return OrchestrationMode.SINGLE

    async def execute(
        self,
        context: RunContext,
        *,
        budget: RunBudget,
        event_sink: RuntimeEventSink | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> OrchestrationOutcome:
        require_deterministic_harness(context, component=type(self).__name__)
        check_cancellation(cancellation_token)
        ledger = BudgetLedger(budget)
        started_at = self._clock()
        await ledger.claim_tool_call("resolve_subject")
        subject = await resolve_subject_with_cancellation(
            self._toolset,
            context,
            cancellation_token,
        )
        await emit_runtime_event(
            event_sink,
            "entity.resolved",
            payload={"subject": subject.model_dump(mode="json")},
        )
        await emit_runtime_event(
            event_sink,
            "plan.created",
            payload={"plan": {"mode": self.mode.value, "task_count": len(_DOMAINS)}},
        )
        await emit_runtime_event(
            event_sink,
            "agent.started",
            member_name="single-agent",
            payload={"task_ids": list(_DOMAINS)},
        )

        artifacts = []
        for domain in _DOMAINS:
            check_cancellation(cancellation_token)
            await ledger.claim_tool_call(f"investigate:{domain}")
            artifact = await investigate_with_cancellation(
                self._toolset,
                context,
                subject,
                domain,
                cancellation_token,
            )
            artifacts.append(artifact)
            for item in artifact.evidence:
                await emit_runtime_event(
                    event_sink,
                    "evidence.collected",
                    member_name="single-agent",
                    payload={"evidence": item.model_dump(mode="json")},
                )

        evidence = tuple(item for artifact in artifacts for item in artifact.evidence)
        available_evidence = {item.evidence_id for item in evidence}
        reviewed_findings = []
        issues: list[ReviewIssue] = []
        for finding in (item for artifact in artifacts for item in artifact.findings):
            missing = set(finding.evidence_ids) - available_evidence
            if not finding.evidence_ids or missing:
                issue_id = f"single-review-{finding.finding_id}"
                issues.append(
                    ReviewIssue(
                        issue_id=issue_id,
                        issue_type="insufficient_evidence",
                        message="Finding lacks resolvable evidence",
                        finding_ids=(finding.finding_id,),
                        evidence_ids=tuple(sorted(missing)),
                        target_agent="single-agent",
                    )
                )
                reviewed_findings.append(
                    finding.model_copy(update={"status": FindingStatus.UNCONFIRMED})
                )
            else:
                reviewed_findings.append(
                    finding.model_copy(update={"status": FindingStatus.ACCEPTED})
                )

        completed_at = self._clock()
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
        errors = tuple(item for artifact in artifacts for item in artifact.errors)
        task_ids = tuple(artifact.task_id for artifact in artifacts)
        trace = AgentTrace(
            agent_id="single-agent",
            role="single_baseline",
            task_ids=task_ids,
            status=AgentStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            evidence_count=len(evidence),
            error_codes=tuple(item.code.value for item in errors),
        )
        return OrchestrationOutcome(
            subject=subject,
            findings=tuple(reviewed_findings),
            evidence=evidence,
            coverage=CoverageSummary.from_items(
                [item for artifact in artifacts for item in artifact.coverage_items]
            ),
            review_issues=tuple(issues),
            review_completed=True,
            section_data=merge_section_data(tuple(artifacts)),
            agent_trace=(trace,),
            collaboration=CollaborationSummary(
                agent_count=1,
                task_count=len(artifacts),
                parallel_task_count=0,
                conflicts_detected=0,
                repairs_requested=0,
                repairs_completed=0,
            ),
            errors=errors,
            tool_calls=ledger.tool_calls,
        )


__all__ = ["SingleAgentStrategy"]
