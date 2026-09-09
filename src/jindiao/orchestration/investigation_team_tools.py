"""Per-member business tools for the in-process AgentTeams investigation runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import register_tool_provider

from jindiao.contracts.acquisition import EnterpriseContextSnapshot
from jindiao.contracts.base import ContractModel
from jindiao.contracts.investigation import DueDiligenceCheckCatalog
from jindiao.investigation import (
    AssignmentBlackboard,
    SnapshotReadGrant,
    SnapshotReadToolset,
    SubmissionBlackboard,
    SubmissionGrant,
)
from jindiao.security import redact_json

if TYPE_CHECKING:
    from jindiao.orchestration.base import BudgetLedger


INVESTIGATION_RUNTIME_TOOL_TYPE = "jindiao.investigation_runtime_tools"


class ReadInvestigationProgressInput(ContractModel):
    pass


class ReadCheckSubmissionsInput(ContractModel):
    pass


@dataclass(slots=True)
class InvestigationTeamState:
    """Live, non-serializable authority shared by one in-process team."""

    runtime_key: str
    run_id: str
    snapshot: EnterpriseContextSnapshot
    check_catalog: DueDiligenceCheckCatalog
    role_agent_ids: dict[str, str]
    reviewer_agent_id: str
    prompt_versions_by_member: dict[str, str]
    budget_ledger: BudgetLedger
    assignment_board: AssignmentBlackboard
    submission_board: SubmissionBlackboard
    snapshot_readers: dict[str, SnapshotReadToolset]

    @classmethod
    def create(
        cls,
        *,
        runtime_key: str,
        run_id: str,
        snapshot: EnterpriseContextSnapshot,
        check_catalog: DueDiligenceCheckCatalog,
        role_agent_ids: dict[str, str],
        reviewer_agent_id: str,
        prompt_versions_by_member: dict[str, str],
        budget_ledger: BudgetLedger,
    ) -> InvestigationTeamState:
        if not runtime_key.strip():
            raise ValueError("investigation team runtime key cannot be empty")
        assignment_board = AssignmentBlackboard(
            run_id=run_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            subject_id=snapshot.subject.subject_id,
            check_catalog=check_catalog,
            role_agent_ids=role_agent_ids,
            budget_ledger=budget_ledger,
        )
        expected_members = {
            assignment_board.leader_agent_id,
            *role_agent_ids.values(),
            reviewer_agent_id,
        }
        if set(prompt_versions_by_member) != expected_members or any(
            not value.strip() for value in prompt_versions_by_member.values()
        ):
            raise ValueError("investigation team requires one Prompt version per exact member")
        submission_grants: list[SubmissionGrant] = []
        grants_by_agent: dict[str, list[SnapshotReadGrant]] = {
            agent_id: [] for agent_id in role_agent_ids.values()
        }
        for definition in check_catalog.checks:
            if not definition.enabled:
                continue
            agent_id = role_agent_ids[definition.owner_role]
            task_id = f"check:{definition.check_id}"
            submission_grants.append(
                SubmissionGrant(
                    agent_id=agent_id,
                    task_id=task_id,
                    check_ids=(definition.check_id,),
                )
            )
            grants_by_agent[agent_id].append(
                SnapshotReadGrant(
                    task_id=task_id,
                    check_ids=(definition.check_id,),
                    allowed_submodule_ids=tuple(
                        dict.fromkeys(
                            (
                                *definition.required_submodule_ids,
                                *definition.optional_submodule_ids,
                            )
                        )
                    ),
                )
            )
        submission_board = SubmissionBlackboard(
            run_id=run_id,
            snapshot=snapshot,
            check_catalog=check_catalog,
            grants=tuple(submission_grants),
            reviewer_agent_ids=(reviewer_agent_id,),
            budget_ledger=budget_ledger,
        )
        readers = {
            agent_id: SnapshotReadToolset(
                snapshot=snapshot,
                run_id=run_id,
                agent_id=agent_id,
                grants=tuple(grants),
                budget_ledger=budget_ledger,
            )
            for agent_id, grants in grants_by_agent.items()
        }
        return cls(
            runtime_key=runtime_key,
            run_id=run_id,
            snapshot=snapshot,
            check_catalog=check_catalog,
            role_agent_ids=dict(role_agent_ids),
            reviewer_agent_id=reviewer_agent_id,
            prompt_versions_by_member=dict(prompt_versions_by_member),
            budget_ledger=budget_ledger,
            assignment_board=assignment_board,
            submission_board=submission_board,
            snapshot_readers=readers,
        )

    def tools_for(self, member_name: str) -> list[Any]:
        evidence_aliases = self._evidence_aliases()
        if member_name == self.assignment_board.leader_agent_id:
            return [
                self.assignment_board.build_submit_canonical_assignments_tool(
                    leader_agent_id=member_name,
                    prompt_version=self.prompt_versions_by_member[member_name],
                ),
                self._build_read_progress_tool(member_name),
            ]
        if member_name in self.snapshot_readers:
            assigned_context_tool = self.snapshot_readers[member_name].build_tools(
                evidence_aliases=evidence_aliases
            )[0]
            return [
                assigned_context_tool,
                self.submission_board.build_submit_bound_check_result_tool(
                    agent_id=member_name,
                    prompt_version=self.prompt_versions_by_member[member_name],
                    evidence_aliases=evidence_aliases,
                ),
            ]
        if member_name == self.reviewer_agent_id:
            return [
                self._build_read_submissions_tool(member_name),
                self.submission_board.build_submit_bound_review_tool(
                    reviewer_agent_id=member_name,
                    prompt_version=self.prompt_versions_by_member[member_name],
                ),
            ]
        raise ValueError(
            f"member {member_name!r} is not authorized for investigation runtime tools"
        )

    def _evidence_aliases(self) -> dict[str, str]:
        """Expose compact citation IDs consistently to every specialist tool."""

        evidence_ids = {item.evidence_id for item in self.snapshot.evidence}
        prefix = "e"
        while any(f"{prefix}{index}" in evidence_ids for index in range(len(evidence_ids))):
            prefix = "_" + prefix
        return {
            item.evidence_id: f"{prefix}{index}"
            for index, item in enumerate(self.snapshot.evidence)
        }

    def _build_read_progress_tool(self, member_name: str) -> Any:
        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{member_name}.read-progress",
                name="read_investigation_progress",
                description=(
                    "Read fixed-check submission and review progress without accessing "
                    "snapshot facts directly."
                ),
                input_params=ReadInvestigationProgressInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def read_investigation_progress() -> dict[str, object]:
            await self.budget_ledger.claim_tool_call("read_investigation_progress")
            accepted = self.submission_board.accepted_results
            submitted = {item.check_id for item in accepted}
            enabled = {item.check_id for item in self.check_catalog.checks if item.enabled}
            value = {
                "assignment_submitted": self.assignment_board.plan is not None,
                "submitted_check_ids": sorted(submitted),
                "missing_check_ids": sorted(enabled - submitted),
                "latest_submission_versions": {
                    item.check_id: item.submission_version for item in accepted
                },
                "latest_review": (
                    self.submission_board.latest_review.model_dump(mode="json")
                    if self.submission_board.latest_review is not None
                    else None
                ),
            }
            safe = redact_json(value)
            if not isinstance(safe, dict):
                raise TypeError("investigation progress output must be an object")
            return dict(safe)

        return read_investigation_progress

    def _build_read_submissions_tool(self, member_name: str) -> Any:
        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{member_name}.read-submissions",
                name="read_check_submissions",
                description=(
                    "Read the latest authoritative fixed-check submissions for independent review."
                ),
                input_params=ReadCheckSubmissionsInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def read_check_submissions() -> dict[str, object]:
            await self.budget_ledger.claim_tool_call("read_check_submissions")
            accepted = self.submission_board.accepted_results
            submitted = {item.check_id for item in accepted}
            enabled = {item.check_id for item in self.check_catalog.checks if item.enabled}
            value = {
                "snapshot_id": self.snapshot.snapshot_id,
                "snapshot_sha256": self.snapshot.snapshot_sha256,
                "subject_id": self.snapshot.subject.subject_id,
                "check_catalog_version": self.check_catalog.catalog_version,
                "results": [item.model_dump(mode="json") for item in accepted],
                "missing_check_ids": sorted(enabled - submitted),
                "previous_review": (
                    self.submission_board.latest_review.model_dump(mode="json")
                    if self.submission_board.latest_review is not None
                    else None
                ),
            }
            safe = redact_json(value)
            if not isinstance(safe, dict):
                raise TypeError("check submission output must be an object")
            return dict(safe)

        return read_check_submissions

    def _build_submit_review_tool(self, member_name: str) -> Any:
        return self.submission_board.build_submit_review_tool(
            reviewer_agent_id=member_name, charge_repair_round=True
        )


_RUNTIME_STATES: dict[str, InvestigationTeamState] = {}


def register_investigation_team_state(state: InvestigationTeamState) -> None:
    existing = _RUNTIME_STATES.get(state.runtime_key)
    if existing is not None and existing is not state:
        raise ValueError(f"investigation runtime already registered: {state.runtime_key}")
    _RUNTIME_STATES[state.runtime_key] = state


def unregister_investigation_team_state(runtime_key: str) -> None:
    _RUNTIME_STATES.pop(runtime_key, None)


def get_investigation_team_state(runtime_key: str) -> InvestigationTeamState:
    state = _RUNTIME_STATES.get(runtime_key)
    if state is None:
        raise ValueError(f"investigation runtime is not registered: {runtime_key}")
    return state


def build_investigation_runtime_tools(
    params: dict[str, object],
    context: BuildContext,
) -> list[Any]:
    runtime_key = str(params.get("runtime_key") or "").strip()
    state = get_investigation_team_state(runtime_key)
    member_name = str(context.member_name or "").strip()
    if not member_name:
        raise ValueError("investigation runtime tools require an exact member name")
    return state.tools_for(member_name)


def register_investigation_runtime_tool_provider() -> None:
    register_tool_provider(
        INVESTIGATION_RUNTIME_TOOL_TYPE,
        build_investigation_runtime_tools,
    )


__all__ = [
    "INVESTIGATION_RUNTIME_TOOL_TYPE",
    "InvestigationTeamState",
    "build_investigation_runtime_tools",
    "get_investigation_team_state",
    "register_investigation_runtime_tool_provider",
    "register_investigation_team_state",
    "unregister_investigation_team_state",
]
