"""Leader-owned fixed-check assignment contracts and validation board."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from openjiuwen.core.foundation.tool import ToolCard, tool
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from jindiao.contracts.base import ContractModel
from jindiao.contracts.investigation import DueDiligenceCheckCatalog


class CheckAssignment(ContractModel):
    task_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    assigned_agent_id: str = Field(min_length=1)
    required_submodule_ids: tuple[str, ...] = Field(min_length=1)
    optional_submodule_ids: tuple[str, ...] = ()
    prompt_template_id: str = Field(min_length=1)
    output_schema_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_submodule_scope(self) -> CheckAssignment:
        all_submodules = (*self.required_submodule_ids, *self.optional_submodule_ids)
        if len(all_submodules) != len(set(all_submodules)):
            raise ValueError("assignment submodule scope must be unique")
        return self


class CheckAssignmentPlan(ContractModel):
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject_id: str = Field(min_length=1)
    check_catalog_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    assignment_version: int = Field(ge=1)
    assignments: tuple[CheckAssignment, ...] = Field(min_length=1)


class AssignmentReceipt(ContractModel):
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    assignment_version: int = Field(ge=1)
    assignment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted: bool
    idempotent_replay: bool
    accepted_at: AwareDatetime


class SubmitCheckAssignmentsInput(ContractModel):
    plan: CheckAssignmentPlan


class SubmitCanonicalCheckAssignmentsInput(ContractModel):
    """Empty input because the Run already binds the complete fixed catalog."""


class AssignmentBudget(Protocol):
    async def claim_tool_call(self, operation: str) -> None: ...

    async def claim_schema_retry(self, operation: str) -> None: ...


class AssignmentBlackboard:
    """Accept one complete, catalog-exact assignment plan from the Leader."""

    def __init__(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        snapshot_sha256: str,
        subject_id: str,
        check_catalog: DueDiligenceCheckCatalog,
        role_agent_ids: Mapping[str, str],
        leader_agent_id: str = "leader",
        budget_ledger: AssignmentBudget | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not all((run_id, snapshot_id, snapshot_sha256, subject_id)):
            raise ValueError("assignment board identity fields cannot be empty")
        enabled_roles = {item.owner_role for item in check_catalog.checks if item.enabled}
        missing_roles = enabled_roles - set(role_agent_ids)
        if missing_roles:
            raise ValueError(f"assignment board has no Agent for roles: {sorted(missing_roles)}")
        self.run_id = run_id
        self.snapshot_id = snapshot_id
        self.snapshot_sha256 = snapshot_sha256
        self.subject_id = subject_id
        self.check_catalog = check_catalog
        self.role_agent_ids = dict(role_agent_ids)
        self.leader_agent_id = leader_agent_id
        self._budget_ledger = budget_ledger
        self._clock = clock
        self._plan: CheckAssignmentPlan | None = None

    @property
    def plan(self) -> CheckAssignmentPlan | None:
        return self._plan

    async def submit(
        self,
        *,
        plan: CheckAssignmentPlan,
        leader_agent_id: str,
    ) -> AssignmentReceipt:
        if leader_agent_id != self.leader_agent_id:
            raise ValueError("only the configured leader can submit assignments")
        validated = CheckAssignmentPlan.model_validate(plan.model_dump(mode="json"))
        self._validate_plan(validated)
        digest = self._digest(validated)
        if self._plan is not None:
            if self._plan != validated:
                raise ValueError("assignment version collision has different content")
            return self._receipt(validated, digest=digest, replay=True)
        if validated.assignment_version != 1:
            raise ValueError("initial assignment plan version must be 1")
        self._plan = validated
        return self._receipt(validated, digest=digest, replay=False)

    def build_submit_assignments_tool(self, *, leader_agent_id: str) -> Any:
        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{leader_agent_id}.submit-assignments",
                name="submit_check_assignments",
                description="Submit exactly one owner assignment for every enabled fixed check.",
                input_params=SubmitCheckAssignmentsInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_check_assignments(plan: dict[str, object]) -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_check_assignments")
            try:
                parsed = CheckAssignmentPlan.model_validate(plan)
            except ValidationError:
                if self._budget_ledger is not None:
                    await self._budget_ledger.claim_schema_retry("submit_check_assignments")
                raise
            receipt = await self.submit(
                plan=parsed,
                leader_agent_id=leader_agent_id,
            )
            return receipt.model_dump(mode="json")

        return submit_check_assignments

    def build_submit_canonical_assignments_tool(
        self,
        *,
        leader_agent_id: str,
        prompt_version: str,
    ) -> Any:
        """Let the Leader trigger the catalog-exact plan without copying its envelope."""

        if not prompt_version.strip():
            raise ValueError("canonical assignment tool requires a prompt version")

        @tool(  # type: ignore[untyped-decorator]
            card=ToolCard(
                id=f"jindiao.{self.run_id}.{leader_agent_id}.submit-canonical-assignments",
                name="submit_check_assignments",
                description=(
                    "Submit the Run-bound owner assignment for every enabled fixed check. "
                    "This tool takes no arguments because identity, catalog, scope and owners "
                    "are immutable Run configuration."
                ),
                input_params=SubmitCanonicalCheckAssignmentsInput.model_json_schema(),
                stateless=False,
                idempotent=True,
                parallel_safe=False,
            )
        )
        async def submit_check_assignments() -> dict[str, object]:
            if self._budget_ledger is not None:
                await self._budget_ledger.claim_tool_call("submit_check_assignments")
            plan = self._canonical_plan(prompt_version=prompt_version)
            receipt = await self.submit(plan=plan, leader_agent_id=leader_agent_id)
            return receipt.model_dump(mode="json")

        return submit_check_assignments

    def _canonical_plan(self, *, prompt_version: str) -> CheckAssignmentPlan:
        return CheckAssignmentPlan(
            run_id=self.run_id,
            snapshot_id=self.snapshot_id,
            snapshot_sha256=self.snapshot_sha256,
            subject_id=self.subject_id,
            check_catalog_version=self.check_catalog.catalog_version,
            prompt_version=prompt_version,
            assignment_version=1,
            assignments=tuple(
                CheckAssignment(
                    task_id=f"check:{definition.check_id}",
                    check_id=definition.check_id,
                    assigned_agent_id=self.role_agent_ids[definition.owner_role],
                    required_submodule_ids=definition.required_submodule_ids,
                    optional_submodule_ids=definition.optional_submodule_ids,
                    prompt_template_id=definition.prompt_template_id,
                    output_schema_version=definition.output_schema_version,
                )
                for definition in self.check_catalog.checks
                if definition.enabled
            ),
        )

    def _validate_plan(self, plan: CheckAssignmentPlan) -> None:
        if (
            plan.run_id != self.run_id
            or plan.snapshot_id != self.snapshot_id
            or plan.snapshot_sha256 != self.snapshot_sha256
            or plan.subject_id != self.subject_id
        ):
            raise ValueError("assignment plan run/snapshot/subject identity does not match")
        if plan.check_catalog_version != self.check_catalog.catalog_version:
            raise ValueError("assignment plan CheckCatalog version does not match")

        check_ids = tuple(item.check_id for item in plan.assignments)
        task_ids = tuple(item.task_id for item in plan.assignments)
        if len(check_ids) != len(set(check_ids)) or len(task_ids) != len(set(task_ids)):
            raise ValueError("assignment plan contains duplicate check or task")
        enabled = tuple(item for item in self.check_catalog.checks if item.enabled)
        expected_ids = tuple(item.check_id for item in enabled)
        if set(check_ids) != set(expected_ids):
            raise ValueError("assignment coverage must equal every enabled fixed check")
        by_id = {item.check_id: item for item in plan.assignments}
        for definition in enabled:
            assignment = by_id[definition.check_id]
            expected_owner = self.role_agent_ids[definition.owner_role]
            if assignment.assigned_agent_id != expected_owner:
                raise ValueError(f"assignment owner does not match catalog: {definition.check_id}")
            if assignment.task_id != f"check:{definition.check_id}":
                raise ValueError("assignment task id does not match fixed check")
            if (
                assignment.required_submodule_ids != definition.required_submodule_ids
                or assignment.optional_submodule_ids != definition.optional_submodule_ids
                or assignment.prompt_template_id != definition.prompt_template_id
                or assignment.output_schema_version != definition.output_schema_version
            ):
                raise ValueError(f"assignment scope does not match catalog: {definition.check_id}")

    @staticmethod
    def _digest(plan: CheckAssignmentPlan) -> str:
        payload = json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _receipt(
        self,
        plan: CheckAssignmentPlan,
        *,
        digest: str,
        replay: bool,
    ) -> AssignmentReceipt:
        return AssignmentReceipt(
            run_id=self.run_id,
            snapshot_id=self.snapshot_id,
            assignment_version=plan.assignment_version,
            assignment_sha256=digest,
            accepted=True,
            idempotent_replay=replay,
            accepted_at=self._clock(),
        )


__all__ = [
    "AssignmentBlackboard",
    "AssignmentReceipt",
    "CheckAssignment",
    "CheckAssignmentPlan",
    "SubmitCanonicalCheckAssignmentsInput",
    "SubmitCheckAssignmentsInput",
]
