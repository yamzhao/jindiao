"""Prompt-driven multi-investigator team over one frozen context snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.base import ContractModel
from jindiao.contracts.execution import RunTermination, RunTerminationReason
from jindiao.contracts.investigation import (
    DueDiligenceCheckDefinition,
    FactEvidenceRef,
    RiskItem,
)
from jindiao.contracts.results import (
    AgentInvestigationResult,
    AgentResultPhase,
    AgentStatus,
)
from jindiao.investigation import (
    CHECK_CATALOG,
    AssignmentBlackboard,
    CheckAssignment,
    CheckAssignmentPlan,
    SnapshotReadGrant,
    SnapshotReadToolset,
    SubmissionBlackboard,
    SubmissionGrant,
)
from jindiao.investigation.blackboard import ReviewSubmission
from jindiao.orchestration.base import BudgetLedger
from jindiao.orchestration.budgeted_model import BudgetedModel
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.prompts import PromptBundle, PromptInvocation

if TYPE_CHECKING:
    from jindiao.contracts.execution import ExecutionCost
    from jindiao.orchestration.agent_runtime import (
        AgentExecutionEvent,
        AgentExecutionRuntime,
    )


ROLE_AGENT_IDS = {
    "corporate": "corporate-agent",
    "judicial-compliance": "judicial-compliance-agent",
    "financial-operations": "financial-operations-agent",
    "related-peer": "related-peer-agent",
}
AGENT_ROLE_IDS = {agent_id: role for role, agent_id in ROLE_AGENT_IDS.items()}


class ReadCheckSubmissionsInput(ContractModel):
    pass


@dataclass(frozen=True, slots=True)
class _AgentRun:
    result: AgentInvestigationResult
    events: tuple[AgentExecutionEvent, ...]


@dataclass(frozen=True, slots=True)
class MultiInvestigatorRun:
    assignments: CheckAssignmentPlan
    agent_results: tuple[AgentInvestigationResult, ...]
    reviews: tuple[ReviewSubmission, ...]
    events: tuple[AgentExecutionEvent, ...]
    team_spec: Any
    investigation_cost: ExecutionCost
    termination: RunTermination


class MultiInvestigatorTeam:
    """Run Leader, four bounded specialists, and an independent Reviewer."""

    leader_agent_id = "leader"
    reviewer_agent_id = "reviewer-agent"

    def __init__(self, *, prompt_bundle: PromptBundle) -> None:
        self._prompt_bundle = prompt_bundle
        self._check_catalog = CHECK_CATALOG

    async def run(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        models: Mapping[str, object] | None = None,
        model_api_key: str = "",
        model_base_url: str = "",
        timeout_seconds: float,
    ) -> MultiInvestigatorRun:
        model_by_agent = dict(models or {})
        assignment_board = AssignmentBlackboard(
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            subject_id=snapshot.subject.subject_id,
            check_catalog=self._check_catalog,
            role_agent_ids=ROLE_AGENT_IDS,
            budget_ledger=budget_ledger,
        )
        leader_run = await self._run_leader(
            snapshot=snapshot,
            assignment_board=assignment_board,
            runtime=runtime,
            budget_ledger=budget_ledger,
            run_id=run_id,
            model_name=model_name,
            model_provider=model_provider,
            model=model_by_agent.get(self.leader_agent_id),
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            timeout_seconds=timeout_seconds,
        )
        assignment_plan = assignment_board.plan
        if assignment_plan is None:
            raise AgentExecutionError("multi Leader did not submit fixed-check assignments")

        submission_board = SubmissionBlackboard(
            run_id=run_id,
            snapshot=snapshot,
            check_catalog=self._check_catalog,
            grants=tuple(
                SubmissionGrant(
                    agent_id=item.assigned_agent_id,
                    task_id=item.task_id,
                    check_ids=(item.check_id,),
                )
                for item in assignment_plan.assignments
            ),
            reviewer_agent_ids=(self.reviewer_agent_id,),
            budget_ledger=budget_ledger,
        )
        specialist_runs = await self._run_specialists(
            assignments=assignment_plan.assignments,
            snapshot=snapshot,
            submission_board=submission_board,
            runtime=runtime,
            budget_ledger=budget_ledger,
            run_id=run_id,
            model_name=model_name,
            model_provider=model_provider,
            models=model_by_agent,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            timeout_seconds=timeout_seconds,
        )
        submitted = {item.check_id for item in submission_board.accepted_results}
        missing = set(self._check_catalog.check_ids) - submitted
        if missing:
            raise AgentExecutionError(
                "multi specialists missing fixed checks",
                details={"missing_check_ids": sorted(missing)},
            )
        reviewer_run, review = await self._run_reviewer(
            snapshot=snapshot,
            submission_board=submission_board,
            review_version=1,
            runtime=runtime,
            budget_ledger=budget_ledger,
            run_id=run_id,
            model_name=model_name,
            model_provider=model_provider,
            model=model_by_agent.get(self.reviewer_agent_id),
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            timeout_seconds=timeout_seconds,
        )
        reviews = [review]
        reviewer_runs = [reviewer_run]
        repair_runs: list[_AgentRun] = []
        while review.repair_tasks:
            await budget_ledger.claim_repair_round("multi.reviewer.repair")
            issue_by_id = {item.issue_id: item for item in review.issues}
            repair_assignments: dict[str, dict[str, CheckAssignment]] = {}
            repair_payloads: dict[str, list[dict[str, object]]] = {}
            for repair in review.repair_tasks:
                try:
                    issues = tuple(issue_by_id[item] for item in repair.issue_ids)
                except KeyError as error:
                    raise AgentExecutionError(
                        "multi repair references an unknown review issue"
                    ) from error
                check_ids = {check_id for issue in issues for check_id in issue.check_ids}
                if not check_ids:
                    raise AgentExecutionError("multi repair does not identify a fixed check")
                owned = {
                    item.check_id: item
                    for item in assignment_plan.assignments
                    if item.check_id in check_ids and item.assigned_agent_id == repair.target_agent
                }
                if set(owned) != check_ids:
                    raise AgentExecutionError(
                        "multi repair target does not own every referenced check"
                    )
                repair_assignments.setdefault(repair.target_agent, {}).update(owned)
                repair_payloads.setdefault(repair.target_agent, []).append(
                    repair.model_dump(mode="json")
                )
            for agent_id, assigned in repair_assignments.items():
                before_versions: dict[str, int] = {}
                for check_id in assigned:
                    previous = submission_board.latest_result(check_id)
                    if previous is None:
                        raise AgentExecutionError(
                            "multi repair references a missing prior submission"
                        )
                    before_versions[check_id] = previous.submission_version
                repair_run = await self._run_specialist(
                    agent_id=agent_id,
                    role=AGENT_ROLE_IDS[agent_id],
                    assignments=tuple(assigned.values()),
                    snapshot=snapshot,
                    submission_board=submission_board,
                    runtime=runtime,
                    budget_ledger=budget_ledger,
                    run_id=run_id,
                    model_name=model_name,
                    model_provider=model_provider,
                    model=model_by_agent.get(agent_id),
                    model_api_key=model_api_key,
                    model_base_url=model_base_url,
                    timeout_seconds=timeout_seconds,
                    repair_context={
                        "review_version": review.review_version,
                        "repair_tasks": repair_payloads[agent_id],
                    },
                )
                for check_id, previous_version in before_versions.items():
                    latest = submission_board.latest_result(check_id)
                    if latest is None or latest.submission_version != previous_version + 1:
                        raise AgentExecutionError(
                            "multi repair did not create a versioned resubmission",
                            details={"agent_id": agent_id, "check_id": check_id},
                        )
                repair_runs.append(repair_run)
            next_version = review.review_version + 1
            reviewer_run, review = await self._run_reviewer(
                snapshot=snapshot,
                submission_board=submission_board,
                review_version=next_version,
                runtime=runtime,
                budget_ledger=budget_ledger,
                run_id=run_id,
                model_name=model_name,
                model_provider=model_provider,
                model=model_by_agent.get(self.reviewer_agent_id),
                model_api_key=model_api_key,
                model_base_url=model_base_url,
                timeout_seconds=timeout_seconds,
            )
            reviews.append(review)
            reviewer_runs.append(reviewer_run)

        all_events = (
            *leader_run.events,
            *(event for item in specialist_runs for event in item.events),
            *(event for item in repair_runs for event in item.events),
            *(event for item in reviewer_runs for event in item.events),
        )
        specialist_results = tuple(
            self._agent_result(
                agent_id=agent_id,
                role=AGENT_ROLE_IDS[agent_id],
                task_ids=tuple(
                    item.task_id
                    for item in assignment_plan.assignments
                    if item.assigned_agent_id == agent_id
                ),
                checks=tuple(
                    result
                    for item in assignment_plan.assignments
                    if item.assigned_agent_id == agent_id
                    if (result := submission_board.latest_result(item.check_id)) is not None
                ),
                prompt_version=next(
                    item.result.prompt_version
                    for item in specialist_runs
                    if item.result.agent_id == agent_id
                ),
            )
            for agent_id in AGENT_ROLE_IDS
        )
        reviewer_result = AgentInvestigationResult(
            agent_id=self.reviewer_agent_id,
            role="reviewer",
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=tuple(f"review:{item.review_version}" for item in reviews),
            prompt_version=reviewer_runs[-1].result.prompt_version,
        )
        team_spec = build_due_diligence_team_spec(
            team_name=f"jindiao-{run_id}",
            model_name=model_name,
            model_provider=(model_provider if model_api_key and model_base_url else None),
            model_base_url=(model_base_url if model_api_key else None),
            model_api_key=(model_api_key if model_base_url else None),
            max_review_rounds=budget_ledger.budget.max_repair_rounds,
            prompt_bundle=self._prompt_bundle,
        )
        task_ids = tuple(item.task_id for item in assignment_plan.assignments)
        return MultiInvestigatorRun(
            assignments=assignment_plan,
            agent_results=(leader_run.result, *specialist_results, reviewer_result),
            reviews=tuple(reviews),
            events=all_events,
            team_spec=team_spec,
            investigation_cost=budget_ledger.to_execution_cost(),
            termination=RunTermination(
                reason=RunTerminationReason.COMPLETED,
                completed_task_ids=task_ids,
            ),
        )

    async def _run_leader(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        assignment_board: AssignmentBlackboard,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None,
        model_api_key: str,
        model_base_url: str,
        timeout_seconds: float,
    ) -> _AgentRun:
        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        checks = tuple(item for item in self._check_catalog.checks if item.enabled)
        invocation = self._invocation(
            role="leader",
            runtime_data=self._identity_payload(snapshot=snapshot, run_id=run_id),
            checks=checks,
        )
        agent = self._build_agent(
            agent_id=self.leader_agent_id,
            name="Investigation Leader",
            invocation=invocation,
            model_name=model_name,
            model_provider=model_provider,
            model=model,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            budget_ledger=budget_ledger,
            max_iterations=4,
            abilities=(
                assignment_board.build_submit_assignments_tool(
                    leader_agent_id=self.leader_agent_id
                ),
            ),
        )
        task_ids = tuple(f"check:{item.check_id}" for item in checks)
        events = tuple(
            [
                event
                async for event in runtime.stream(
                    agent,
                    AgentExecutionRequest(
                        run_id=run_id,
                        agent_id=self.leader_agent_id,
                        role="leader",
                        phase=AgentResultPhase.INVESTIGATION,
                        session_id=f"{run_id}:{self.leader_agent_id}",
                        query=invocation.user_payload_json,
                        task_ids=task_ids,
                        prompt_version=invocation.prompt_version,
                        prompt_sha256=invocation.prompt_sha256,
                        timeout_seconds=timeout_seconds,
                    ),
                )
            ]
        )
        self._raise_if_budget_exhausted(budget_ledger, agent_id=self.leader_agent_id)
        if assignment_board.plan is None:
            raise AgentExecutionError("multi Leader ended without assignment plan")
        return _AgentRun(
            result=AgentInvestigationResult(
                agent_id=self.leader_agent_id,
                role="leader",
                phase=AgentResultPhase.INVESTIGATION,
                status=AgentStatus.COMPLETED,
                task_ids=task_ids,
                prompt_version=invocation.prompt_version,
            ),
            events=events,
        )

    async def _run_specialists(
        self,
        *,
        assignments: tuple[CheckAssignment, ...],
        snapshot: EnterpriseContextSnapshot,
        submission_board: SubmissionBlackboard,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        models: Mapping[str, object],
        model_api_key: str,
        model_base_url: str,
        timeout_seconds: float,
    ) -> tuple[_AgentRun, ...]:
        grouped = {
            agent_id: tuple(item for item in assignments if item.assigned_agent_id == agent_id)
            for agent_id in AGENT_ROLE_IDS
        }
        tasks = [
            asyncio.create_task(
                self._run_specialist(
                    agent_id=agent_id,
                    role=AGENT_ROLE_IDS[agent_id],
                    assignments=grouped[agent_id],
                    snapshot=snapshot,
                    submission_board=submission_board,
                    runtime=runtime,
                    budget_ledger=budget_ledger,
                    run_id=run_id,
                    model_name=model_name,
                    model_provider=model_provider,
                    model=models.get(agent_id),
                    model_api_key=model_api_key,
                    model_base_url=model_base_url,
                    timeout_seconds=timeout_seconds,
                )
            )
            for agent_id in AGENT_ROLE_IDS
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return tuple(cast(_AgentRun, outcome) for outcome in outcomes)

    async def _run_specialist(
        self,
        *,
        agent_id: str,
        role: str,
        assignments: tuple[CheckAssignment, ...],
        snapshot: EnterpriseContextSnapshot,
        submission_board: SubmissionBlackboard,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None,
        model_api_key: str,
        model_base_url: str,
        timeout_seconds: float,
        repair_context: dict[str, object] | None = None,
    ) -> _AgentRun:
        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        if not assignments:
            raise AgentExecutionError(f"multi specialist has no assignments: {agent_id}")
        checks = tuple(self._check_catalog.get(item.check_id) for item in assignments)
        invocation = self._invocation(
            role=role,
            runtime_data={
                **self._identity_payload(snapshot=snapshot, run_id=run_id),
                "assignments": [item.model_dump(mode="json") for item in assignments],
                **({"repair": repair_context} if repair_context is not None else {}),
            },
            checks=checks,
        )
        reader = SnapshotReadToolset(
            snapshot=snapshot,
            run_id=run_id,
            agent_id=agent_id,
            grants=tuple(
                SnapshotReadGrant(
                    task_id=item.task_id,
                    check_ids=(item.check_id,),
                    allowed_submodule_ids=(
                        *item.required_submodule_ids,
                        *item.optional_submodule_ids,
                    ),
                )
                for item in assignments
            ),
            budget_ledger=budget_ledger,
        )
        _, submodule_tool, evidence_tool = reader.build_tools()
        agent = self._build_agent(
            agent_id=agent_id,
            name=f"{role} Investigator",
            invocation=invocation,
            model_name=model_name,
            model_provider=model_provider,
            model=model,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            budget_ledger=budget_ledger,
            max_iterations=len(assignments) * 3 + 2,
            abilities=(
                submodule_tool,
                evidence_tool,
                submission_board.build_submit_check_result_tool(agent_id=agent_id),
            ),
        )
        task_ids = tuple(item.task_id for item in assignments)
        events = tuple(
            [
                event
                async for event in runtime.stream(
                    agent,
                    AgentExecutionRequest(
                        run_id=run_id,
                        agent_id=agent_id,
                        role=role,
                        phase=AgentResultPhase.INVESTIGATION,
                        session_id=f"{run_id}:{agent_id}",
                        query=invocation.user_payload_json,
                        task_ids=task_ids,
                        prompt_version=invocation.prompt_version,
                        prompt_sha256=invocation.prompt_sha256,
                        timeout_seconds=timeout_seconds,
                    ),
                )
            ]
        )
        self._raise_if_budget_exhausted(budget_ledger, agent_id=agent_id)
        results = tuple(
            result
            for item in assignments
            if (result := submission_board.latest_result(item.check_id)) is not None
        )
        if len(results) != len(assignments):
            raise AgentExecutionError(
                "multi specialist missing assigned checks",
                details={"agent_id": agent_id, "task_ids": list(task_ids)},
            )
        return _AgentRun(
            result=self._agent_result(
                agent_id=agent_id,
                role=role,
                task_ids=task_ids,
                checks=results,
                prompt_version=invocation.prompt_version,
            ),
            events=events,
        )

    async def _run_reviewer(
        self,
        *,
        snapshot: EnterpriseContextSnapshot,
        submission_board: SubmissionBlackboard,
        review_version: int,
        runtime: AgentExecutionRuntime,
        budget_ledger: BudgetLedger,
        run_id: str,
        model_name: str,
        model_provider: str,
        model: object | None,
        model_api_key: str,
        model_base_url: str,
        timeout_seconds: float,
    ) -> tuple[_AgentRun, ReviewSubmission]:
        from jindiao.orchestration.agent_runtime import AgentExecutionRequest

        invocation = self._invocation(
            role="reviewer",
            runtime_data={
                **self._identity_payload(snapshot=snapshot, run_id=run_id),
                "review_version": review_version,
                "submitted_check_ids": [
                    item.check_id for item in submission_board.accepted_results
                ],
            },
            checks=tuple(item for item in self._check_catalog.checks if item.enabled),
        )

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{run_id}.{self.reviewer_agent_id}.read-submissions",
                name="read_check_submissions",
                description="Read all accepted fixed-check submissions for independent review.",
                input_params=ReadCheckSubmissionsInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def read_check_submissions() -> dict[str, object]:
            await budget_ledger.claim_tool_call("read_check_submissions")
            return {
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "items": [
                    item.model_dump(mode="json") for item in submission_board.accepted_results
                ],
            }

        agent = self._build_agent(
            agent_id=self.reviewer_agent_id,
            name="Independent Reviewer",
            invocation=invocation,
            model_name=model_name,
            model_provider=model_provider,
            model=model,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
            budget_ledger=budget_ledger,
            max_iterations=8,
            abilities=(
                read_check_submissions,
                submission_board.build_submit_review_tool(reviewer_agent_id=self.reviewer_agent_id),
            ),
        )
        task_id = f"review:{review_version}"
        events = tuple(
            [
                event
                async for event in runtime.stream(
                    agent,
                    AgentExecutionRequest(
                        run_id=run_id,
                        agent_id=self.reviewer_agent_id,
                        role="reviewer",
                        phase=AgentResultPhase.INVESTIGATION,
                        session_id=f"{run_id}:{self.reviewer_agent_id}:{review_version}",
                        query=invocation.user_payload_json,
                        task_ids=(task_id,),
                        prompt_version=invocation.prompt_version,
                        prompt_sha256=invocation.prompt_sha256,
                        timeout_seconds=timeout_seconds,
                    ),
                )
            ]
        )
        self._raise_if_budget_exhausted(
            budget_ledger,
            agent_id=self.reviewer_agent_id,
        )
        review = submission_board.latest_review
        if review is None or review.review_version != review_version:
            raise AgentExecutionError("multi Reviewer ended without structured review")
        return (
            _AgentRun(
                result=AgentInvestigationResult(
                    agent_id=self.reviewer_agent_id,
                    role="reviewer",
                    phase=AgentResultPhase.INVESTIGATION,
                    status=AgentStatus.COMPLETED,
                    task_ids=(task_id,),
                    prompt_version=invocation.prompt_version,
                ),
                events=events,
            ),
            review,
        )

    def _build_agent(
        self,
        *,
        agent_id: str,
        name: str,
        invocation: PromptInvocation,
        model_name: str,
        model_provider: str,
        model: object | None,
        model_api_key: str,
        model_base_url: str,
        budget_ledger: BudgetLedger,
        max_iterations: int,
        abilities: tuple[Any, ...],
    ) -> ReActAgent:
        agent = ReActAgent(AgentCard(id=agent_id, name=name)).configure(
            ReActAgentConfig(
                model_name=model_name,
                model_provider=model_provider,
                api_key=model_api_key,
                api_base=model_base_url,
                prompt_template=[{"role": "system", "content": invocation.system_prompt}],
                max_iterations=max_iterations,
                parallel_tool_calls=False,
            )
        )
        if not isinstance(agent, ReActAgent):
            raise RuntimeError("openJiuwen did not build a ReActAgent")
        underlying_model = model if model is not None else agent._get_llm()
        agent.set_llm(cast(Any, BudgetedModel(underlying_model, budget_ledger=budget_ledger)))
        registrations = [agent.ability_manager.add_ability(item.card, item) for item in abilities]
        if not all(item.added for item in registrations):
            agent.ability_manager.teardown_tools()
            raise AgentExecutionError(f"multi Agent Tool registration failed: {agent_id}")
        return agent

    def _invocation(
        self,
        *,
        role: str,
        runtime_data: dict[str, object],
        checks: tuple[DueDiligenceCheckDefinition, ...],
    ) -> PromptInvocation:
        base = self._prompt_bundle.investigation(role)
        sections = [base.system_prompt]
        versions = [base.prompt_version]
        for check in checks:
            artifact = self._prompt_bundle.check_prompt(check.prompt_template_id)
            definition = json.dumps(
                check.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            sections.append(f"## {check.check_id}\n{definition}\n\n{artifact.system_prompt}")
            versions.append(f"{check.check_id}:{artifact.version}")
        system_prompt = "\n\n".join(sections)
        return PromptInvocation(
            phase=AgentResultPhase.INVESTIGATION,
            role=role,
            prompt_version="+".join(versions),
            prompt_sha256=hashlib.sha256(system_prompt.encode()).hexdigest(),
            system_prompt=system_prompt,
            user_payload_json=json.dumps(
                runtime_data,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    @staticmethod
    def _identity_payload(
        *,
        snapshot: EnterpriseContextSnapshot,
        run_id: str,
    ) -> dict[str, object]:
        return {
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "subject_id": snapshot.subject.subject_id,
            "report_as_of": snapshot.report_as_of.isoformat(),
            "check_catalog_version": CHECK_CATALOG.catalog_version,
        }

    @staticmethod
    def _raise_if_budget_exhausted(
        budget_ledger: BudgetLedger,
        *,
        agent_id: str,
    ) -> None:
        reason = budget_ledger.snapshot().exhausted_reason
        if reason is not None:
            raise AgentExecutionError(reason, details={"agent_id": agent_id})

    @staticmethod
    def _agent_result(
        *,
        agent_id: str,
        role: str,
        task_ids: tuple[str, ...],
        checks: tuple[Any, ...],
        prompt_version: str,
    ) -> AgentInvestigationResult:
        risk_by_id: dict[str, RiskItem] = {}
        fact_by_key: dict[tuple[str, str], FactEvidenceRef] = {}
        for result in checks:
            for risk in result.risk_items:
                risk_by_id.setdefault(risk.risk_id, risk)
            for fact in result.fact_evidence_refs:
                fact_by_key.setdefault((fact.evidence_id, fact.fact_path), fact)
        return AgentInvestigationResult(
            agent_id=agent_id,
            role=role,
            phase=AgentResultPhase.INVESTIGATION,
            status=AgentStatus.COMPLETED,
            task_ids=task_ids,
            check_results=checks,
            risk_items=tuple(risk_by_id.values()),
            fact_evidence_refs=tuple(fact_by_key.values()),
            prompt_version=prompt_version,
        )


__all__ = [
    "AGENT_ROLE_IDS",
    "ROLE_AGENT_IDS",
    "MultiInvestigatorRun",
    "MultiInvestigatorTeam",
    "ReadCheckSubmissionsInput",
]
