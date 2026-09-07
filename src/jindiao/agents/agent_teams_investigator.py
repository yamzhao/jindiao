"""Formal multi-investigator execution through openJiuwen AgentTeams."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.execution import ExecutionCost, RunTermination, RunTerminationReason
from jindiao.contracts.investigation import CheckResult, FactEvidenceRef, RiskItem
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.investigation import CHECK_CATALOG, CheckAssignmentPlan
from jindiao.investigation.blackboard import ReviewSubmission
from jindiao.orchestration.base import (
    BudgetLedger,
    CancellationToken,
    TeamRuntimeEvent,
    check_cancellation,
)
from jindiao.orchestration.investigation_team_tools import (
    InvestigationTeamState,
    register_investigation_runtime_tool_provider,
    register_investigation_team_state,
    unregister_investigation_team_state,
)
from jindiao.orchestration.team_runtime import OpenJiuwenTeamRuntime
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.prompts import PromptBundle

from .multi_investigator import AGENT_ROLE_IDS, ROLE_AGENT_IDS


@dataclass(frozen=True, slots=True)
class AgentTeamsInvestigatorRun:
    assignments: CheckAssignmentPlan
    check_results: tuple[CheckResult, ...]
    agent_results: tuple[AgentInvestigationResult, ...]
    reviews: tuple[ReviewSubmission, ...]
    events: tuple[TeamRuntimeEvent, ...]
    investigation_cost: ExecutionCost
    termination: RunTermination


class AgentTeamsInvestigatorTeam:
    """Use AgentTeams task/message mechanics as the multi business control plane."""

    leader_agent_id = "leader"
    reviewer_agent_id = "reviewer-agent"

    def __init__(
        self,
        *,
        prompt_bundle: PromptBundle,
        runtime: OpenJiuwenTeamRuntime | None = None,
    ) -> None:
        self._prompt_bundle = prompt_bundle
        self._runtime = runtime or OpenJiuwenTeamRuntime()

    async def run(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model_api_key: str,
        model_base_url: str,
        timeout_seconds: float,
        model_temperature: float = 0,
        event_sink: Callable[[TeamRuntimeEvent], Awaitable[None]] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentTeamsInvestigatorRun:
        check_cancellation(cancellation_token)
        runtime_key = f"{run_id}:{snapshot.snapshot_id}:{snapshot.snapshot_sha256}"
        state = InvestigationTeamState.create(
            runtime_key=runtime_key,
            run_id=run_id,
            snapshot=snapshot,
            check_catalog=CHECK_CATALOG,
            role_agent_ids=ROLE_AGENT_IDS,
            reviewer_agent_id=self.reviewer_agent_id,
            prompt_versions_by_member={
                self.leader_agent_id: self._prompt_bundle.investigation("leader").prompt_version,
                **{
                    agent_id: self._prompt_bundle.investigation(role).prompt_version
                    for role, agent_id in ROLE_AGENT_IDS.items()
                },
                self.reviewer_agent_id: self._prompt_bundle.investigation(
                    "reviewer"
                ).prompt_version,
            },
            budget_ledger=budget_ledger,
        )
        register_investigation_runtime_tool_provider()
        register_investigation_team_state(state)
        spec = build_due_diligence_team_spec(
            team_name=f"jindiao-{run_id}",
            model_name=model_name,
            model_provider=model_provider,
            model_base_url=model_base_url,
            model_api_key=model_api_key,
            model_temperature=model_temperature,
            model_timeout_seconds=max(1, round(timeout_seconds)),
            max_review_rounds=budget_ledger.budget.max_repair_rounds,
            prompt_bundle=self._prompt_bundle,
            investigation_runtime_key=runtime_key,
        )
        session_id = f"{run_id}:multi-agent-teams"
        events: list[TeamRuntimeEvent] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                # Native task-board completion is not a committed business review.
                # Recover only the review/repair tail, never re-acquire evidence or
                # reset the shared ledger, deadline, assignments or submissions.
                for attempt in range(3):
                    check_cancellation(cancellation_token)
                    self._check_budget(state)
                    attempt_spec = (
                        spec
                        if not attempt
                        else spec.model_copy(
                            update={"team_name": f"jindiao-{run_id}-recovery-{attempt}"}
                        )
                    )
                    stream_kwargs: dict[str, Any] = {
                        "session_id": session_id
                        if not attempt
                        else f"{session_id}:recovery:{attempt}"
                    }
                    if "cancellation_token" in inspect.signature(self._runtime.stream).parameters:
                        stream_kwargs["cancellation_token"] = cancellation_token
                    query = self._runtime_query(snapshot=snapshot, run_id=run_id)
                    if attempt:
                        query += (
                            " 本次是同一业务 Run 的审核尾部故障恢复, 不是首轮调查。"
                            "先 build_team 建立本次预定义团队, 再 read_investigation_progress。"
                            "15 项首轮结果已在权威黑板, 禁止重做首轮或清空历史结果。"
                            "若无 latest_review, 创建独立 Reviewer scheduled 任务; "
                            "若有 RepairTask, 先为目标 Agent 创建定向返工任务, "
                            "确认新提交被接受后再创建独立复核任务。"
                            "Reviewer 必须 read_check_submissions 并成功 submit_review 后"
                            "才能 member_complete_task; 自然语言回复不算审核提交。"
                            "不得代替 Reviewer、抹除返工或增加预算。"
                        )
                        recovery = TeamRuntimeEvent(
                            event_type="team.recovery.started",
                            member_name=self.leader_agent_id,
                            payload={"attempt": attempt, "max_recovery_attempts": 2},
                        )
                        events.append(recovery)
                        if event_sink is not None:
                            await event_sink(recovery)
                    source = self._runtime.stream(attempt_spec, {"query": query}, **stream_kwargs)
                    try:
                        async for event in source:
                            check_cancellation(cancellation_token)
                            self._check_budget(state)
                            if event.event_type == "team.completed":
                                event = event.model_copy(
                                    update={"event_type": "team.runtime.ended"}
                                )
                            events.append(event)
                            if event_sink is not None:
                                await event_sink(event)
                            if self._business_complete(state):
                                completed_event = TeamRuntimeEvent(
                                    event_type="team.completed",
                                    member_name=self.leader_agent_id,
                                    payload={
                                        "submitted_check_count": len(
                                            state.submission_board.accepted_results
                                        ),
                                        "review_version": (
                                            state.submission_board.latest_review.review_version
                                            if state.submission_board.latest_review is not None
                                            else 0
                                        ),
                                    },
                                )
                                events.append(completed_event)
                                if event_sink is not None:
                                    await event_sink(completed_event)
                                await self._drain_runtime_after_business_completion(
                                    source,
                                    events=events,
                                    run_timeout_seconds=timeout_seconds,
                                    event_sink=event_sink,
                                )
                                break
                    finally:
                        close = getattr(source, "aclose", None)
                        if close is not None:
                            await close()
                    self._check_budget(state)
                    if self._business_complete(state) or (
                        state.assignment_board.plan is None
                        or {item.check_id for item in state.submission_board.accepted_results}
                        != set(CHECK_CATALOG.check_ids)
                    ):
                        break
        except TimeoutError as error:
            accepted = state.submission_board.accepted_results
            raise AgentExecutionError(
                "AgentTeams investigation deadline exceeded",
                details={
                    "run_id": run_id,
                    "timeout_seconds": timeout_seconds,
                    "assignment_submitted": state.assignment_board.plan is not None,
                    "submission_versions": {
                        item.check_id: item.submission_version for item in accepted
                    },
                    "review_versions": [
                        item.review_version for item in state.submission_board.reviews
                    ],
                },
            ) from error
        finally:
            unregister_investigation_team_state(runtime_key)

        assignments = state.assignment_board.plan
        if assignments is None:
            raise AgentExecutionError("AgentTeams Leader did not submit assignments")
        check_results = state.submission_board.accepted_results
        submitted = {item.check_id for item in check_results}
        missing = set(CHECK_CATALOG.check_ids) - submitted
        if missing:
            raise AgentExecutionError(
                "AgentTeams specialists did not finish every fixed check",
                details={"missing_check_ids": sorted(missing)},
            )
        reviews = state.submission_board.reviews
        if not reviews:
            raise AgentExecutionError("AgentTeams Reviewer did not submit a review")
        if reviews[-1].repair_tasks:
            raise AgentExecutionError("AgentTeams review ended with unresolved repair tasks")
        agent_results = self._agent_results(
            assignments=assignments,
            checks=check_results,
            reviews=reviews,
        )
        return AgentTeamsInvestigatorRun(
            assignments=assignments,
            check_results=check_results,
            agent_results=agent_results,
            reviews=reviews,
            events=tuple(events),
            investigation_cost=budget_ledger.to_execution_cost(),
            termination=RunTermination(
                reason=RunTerminationReason.COMPLETED,
                completed_task_ids=tuple(item.task_id for item in assignments.assignments),
            ),
        )

    @staticmethod
    def _check_budget(state: InvestigationTeamState) -> None:
        usage = state.budget_ledger.snapshot()
        if usage.exhausted_reason:
            raise AgentExecutionError(
                usage.exhausted_reason, details={"budget": usage.model_dump(mode="json")}
            )
        if usage.deadline_remaining_ms <= 0:
            raise TimeoutError("investigation budget deadline exceeded")

    @staticmethod
    def _business_complete(state: InvestigationTeamState) -> bool:
        submitted = {item.check_id for item in state.submission_board.accepted_results}
        latest_review = state.submission_board.latest_review
        return (
            submitted == set(CHECK_CATALOG.check_ids)
            and latest_review is not None
            and not latest_review.repair_tasks
        )

    @staticmethod
    async def _drain_runtime_after_business_completion(
        source: object,
        *,
        events: list[TeamRuntimeEvent],
        run_timeout_seconds: float,
        event_sink: Callable[[TeamRuntimeEvent], Awaitable[None]] | None = None,
    ) -> None:
        """Give scheduled members a bounded chance to finish coordination cleanup."""

        grace_seconds = min(10.0, max(0.1, run_timeout_seconds * 0.05))
        try:
            async with asyncio.timeout(grace_seconds):
                async for event in source:  # type: ignore[attr-defined]
                    if event.event_type == "team.completed":
                        event = event.model_copy(update={"event_type": "team.runtime.ended"})
                    events.append(event)
                    if event_sink is not None:
                        await event_sink(event)
        except TimeoutError:
            # Closing the source stops the team and deterministically cancels
            # any residual coordination task after business completion.
            return

    @staticmethod
    def _runtime_query(
        *,
        snapshot: EnterpriseContextSnapshot,
        run_id: str,
    ) -> str:
        return (
            "执行固定核查团队流程。仅使用 System Prompt 中的目录定义和已注册工具。"
            f" run_id={run_id}; snapshot_id={snapshot.snapshot_id}; "
            f"snapshot_sha256={snapshot.snapshot_sha256}; "
            f"subject_id={snapshot.subject.subject_id}; "
            f"report_as_of={snapshot.report_as_of.isoformat()}."
        )

    def _agent_results(
        self,
        *,
        assignments: CheckAssignmentPlan,
        checks: tuple[CheckResult, ...],
        reviews: tuple[ReviewSubmission, ...],
    ) -> tuple[AgentInvestigationResult, ...]:
        leader_prompt = self._prompt_bundle.investigation("leader").prompt_version
        leader = AgentInvestigationResult(
            agent_id=self.leader_agent_id,
            role="leader",
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(item.task_id for item in assignments.assignments),
            prompt_version=leader_prompt,
        )
        specialists = tuple(
            self._specialist_result(
                agent_id=agent_id,
                role=role,
                assignments=assignments,
                checks=checks,
            )
            for agent_id, role in AGENT_ROLE_IDS.items()
        )
        reviewer = AgentInvestigationResult(
            agent_id=self.reviewer_agent_id,
            role="reviewer",
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(f"review:{item.review_version}" for item in reviews),
            prompt_version=self._prompt_bundle.investigation("reviewer").prompt_version,
        )
        return (leader, *specialists, reviewer)

    def _specialist_result(
        self,
        *,
        agent_id: str,
        role: str,
        assignments: CheckAssignmentPlan,
        checks: tuple[CheckResult, ...],
    ) -> AgentInvestigationResult:
        role_checks = tuple(
            item for item in checks if CHECK_CATALOG.get(item.check_id).owner_role == role
        )
        risk_by_id: dict[str, RiskItem] = {}
        fact_by_key: dict[tuple[str, str], FactEvidenceRef] = {}
        for check in role_checks:
            for risk in check.risk_items:
                risk_by_id.setdefault(risk.risk_id, risk)
            for fact in check.fact_evidence_refs:
                fact_by_key.setdefault((fact.evidence_id, fact.fact_path), fact)
        return AgentInvestigationResult(
            agent_id=agent_id,
            role=role,
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(
                item.task_id
                for item in assignments.assignments
                if item.assigned_agent_id == agent_id
            ),
            check_results=role_checks,
            risk_items=tuple(risk_by_id.values()),
            fact_evidence_refs=tuple(fact_by_key.values()),
            prompt_version=self._prompt_bundle.investigation(role).prompt_version,
        )


__all__ = ["AgentTeamsInvestigatorRun", "AgentTeamsInvestigatorTeam"]
