from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jindiao.investigation import (
    CHECK_CATALOG,
    AssignmentBlackboard,
    CheckAssignment,
    CheckAssignmentPlan,
)
from jindiao.orchestration import BudgetLedger, RunBudget

NOW = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
ROLE_AGENTS = {
    "corporate": "corporate-agent",
    "judicial-compliance": "judicial-compliance-agent",
    "financial-operations": "financial-operations-agent",
    "related-peer": "related-peer-agent",
}


def budget() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(
            max_tool_calls=10,
            max_concurrency=1,
            timeout_seconds=30,
            max_repair_rounds=1,
        )
    )


def assignment_plan() -> CheckAssignmentPlan:
    return CheckAssignmentPlan(
        run_id="run-assignments",
        snapshot_id="snapshot:assignments",
        snapshot_sha256="a" * 64,
        subject_id="tyc:assignments",
        check_catalog_version=CHECK_CATALOG.catalog_version,
        prompt_version="investigation-core-v1+leader-v1",
        assignment_version=1,
        assignments=tuple(
            CheckAssignment(
                task_id=f"check:{check.check_id}",
                check_id=check.check_id,
                assigned_agent_id=ROLE_AGENTS[check.owner_role],
                required_submodule_ids=check.required_submodule_ids,
                optional_submodule_ids=check.optional_submodule_ids,
                prompt_template_id=check.prompt_template_id,
                output_schema_version=check.output_schema_version,
            )
            for check in CHECK_CATALOG.checks
            if check.enabled
        ),
    )


def board(*, ledger: BudgetLedger | None = None) -> AssignmentBlackboard:
    return AssignmentBlackboard(
        run_id="run-assignments",
        snapshot_id="snapshot:assignments",
        snapshot_sha256="a" * 64,
        subject_id="tyc:assignments",
        check_catalog=CHECK_CATALOG,
        role_agent_ids=ROLE_AGENTS,
        budget_ledger=ledger,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_leader_assignment_tool_accepts_exactly_one_owner_for_every_enabled_check() -> None:
    ledger = budget()
    target = board(ledger=ledger)
    tool = target.build_submit_assignments_tool(leader_agent_id="leader")
    plan = assignment_plan()

    receipt = await tool.invoke({"plan": plan.model_dump(mode="json")})
    replay = await target.submit(plan=plan, leader_agent_id="leader")

    assert tool.card.name == "submit_check_assignments"
    assert receipt["accepted"] is True
    assert replay.idempotent_replay is True
    assert target.plan == plan
    assert tuple(item.check_id for item in plan.assignments) == CHECK_CATALOG.check_ids
    assert ledger.snapshot().tool_calls == 1


@pytest.mark.asyncio
async def test_runtime_bound_assignment_tool_injects_immutable_plan_fields() -> None:
    ledger = budget()
    target = board(ledger=ledger)
    tool = target.build_submit_canonical_assignments_tool(
        leader_agent_id="leader",
        prompt_version="investigation-core-v1+leader-v3",
    )

    receipt = await tool.invoke({})
    replay = await tool.invoke({})

    assert tool.card.input_params["properties"] == {}
    assert receipt["accepted"] is True
    assert receipt["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert target.plan is not None
    assert target.plan.run_id == target.run_id
    assert target.plan.snapshot_id == target.snapshot_id
    assert target.plan.snapshot_sha256 == target.snapshot_sha256
    assert target.plan.subject_id == target.subject_id
    assert target.plan.check_catalog_version == CHECK_CATALOG.catalog_version
    assert target.plan.prompt_version == "investigation-core-v1+leader-v3"
    assert tuple(item.check_id for item in target.plan.assignments) == CHECK_CATALOG.check_ids
    assert ledger.snapshot().tool_calls == 2


@pytest.mark.asyncio
async def test_assignment_board_rejects_missing_duplicate_wrong_owner_and_scope() -> None:
    plan = assignment_plan()
    target = board()

    with pytest.raises(ValueError, match="coverage"):
        await target.submit(
            plan=plan.model_copy(update={"assignments": plan.assignments[:-1]}),
            leader_agent_id="leader",
        )

    duplicate = plan.model_copy(update={"assignments": (*plan.assignments, plan.assignments[0])})
    with pytest.raises(ValueError, match="duplicate"):
        await target.submit(plan=duplicate, leader_agent_id="leader")

    wrong_owner = plan.assignments[0].model_copy(
        update={"assigned_agent_id": "financial-operations-agent"}
    )
    with pytest.raises(ValueError, match="owner"):
        await target.submit(
            plan=plan.model_copy(update={"assignments": (wrong_owner, *plan.assignments[1:])}),
            leader_agent_id="leader",
        )

    wrong_scope = plan.assignments[0].model_copy(
        update={"required_submodule_ids": ("annual_reports",)}
    )
    with pytest.raises(ValueError, match="scope"):
        await target.submit(
            plan=plan.model_copy(update={"assignments": (wrong_scope, *plan.assignments[1:])}),
            leader_agent_id="leader",
        )


@pytest.mark.asyncio
async def test_only_leader_can_submit_assignment_plan() -> None:
    with pytest.raises(ValueError, match="leader"):
        await board().submit(
            plan=assignment_plan(),
            leader_agent_id="corporate-agent",
        )
