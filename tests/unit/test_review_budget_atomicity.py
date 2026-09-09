from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_submission_blackboard import snapshot

from jindiao.agents.multi_investigator import ROLE_AGENT_IDS
from jindiao.application.errors import AgentExecutionError
from jindiao.investigation import CHECK_CATALOG
from jindiao.orchestration.base import BudgetLedger, RunBudget
from jindiao.orchestration.investigation_team_tools import InvestigationTeamState


class YieldingLedger(BudgetLedger):
    async def claim_repair_round(self, operation: str) -> None:
        # Retain real accounting while exercising an asynchronous commit boundary.
        await asyncio.sleep(0)
        await super().claim_repair_round(operation)


def setup(bound: bool, *, yielding: bool = False) -> tuple[InvestigationTeamState, Any]:
    ledger_class = YieldingLedger if yielding else BudgetLedger
    ledger = ledger_class(
        RunBudget(
            max_tool_calls=30,
            max_concurrency=2,
            timeout_seconds=30,
            max_repair_rounds=2,
            max_schema_retries=4,
        )
    )
    state = InvestigationTeamState.create(
        runtime_key="review-budget-test",
        run_id="review-budget-test",
        snapshot=snapshot(),
        check_catalog=CHECK_CATALOG,
        role_agent_ids=ROLE_AGENT_IDS,
        reviewer_agent_id="reviewer-agent",
        prompt_versions_by_member={
            member: "budget-test-v1"
            for member in ("leader", *ROLE_AGENT_IDS.values(), "reviewer-agent")
        },
        budget_ledger=ledger,
    )
    tool = (
        state.tools_for("reviewer-agent")[1]
        if bound
        else state._build_submit_review_tool("reviewer-agent")
    )
    return state, tool


def payload(state: InvestigationTeamState, bound: bool, *, version: int = 1) -> dict[str, Any]:
    review: dict[str, Any] = {
        "issues": [
            {
                "issue_id": f"issue-{version}",
                "issue_type": "evidence",
                "message": "Synthetic correction",
                "check_ids": ["registration-status-normal"],
                "evidence_ids": ["ev-registration"],
                "target_agent": "corporate-agent",
            }
        ],
        "repair_tasks": [
            {
                "repair_id": f"repair-{version}",
                "issue_ids": [f"issue-{version}"],
                "target_agent": "corporate-agent",
                "requested_fields": ["decision_summary"],
                "required_evidence": ["ev-registration"],
                "attempt": 1,
                "max_attempts": 2,
            }
        ],
    }
    if not bound:
        review.update(
            snapshot_id=state.snapshot.snapshot_id,
            snapshot_sha256=state.snapshot.snapshot_sha256,
            subject_id=state.snapshot.subject.subject_id,
            check_catalog_version=CHECK_CATALOG.catalog_version,
            prompt_version="budget-test-v1",
            review_version=version,
        )
    return {"review": review}


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
@pytest.mark.parametrize("invalid", ["check", "evidence", "target"])
async def test_rejected_review_preserves_repair_budget_but_counts_invalid_attempt(
    bound: bool,
    invalid: str,
) -> None:
    state, tool = setup(bound)
    bad = payload(state, bound)
    if invalid == "check":
        bad["review"]["issues"][0]["check_ids"] = ["unknown-check"]
    elif invalid == "evidence":
        bad["review"]["issues"][0]["evidence_ids"] = ["unknown-evidence"]
    else:
        bad["review"]["issues"][0]["target_agent"] = "unauthorized-agent"
        bad["review"]["repair_tasks"][0]["target_agent"] = "unauthorized-agent"
    with pytest.raises(ValueError):
        await tool.invoke(bad)
    assert state.submission_board.reviews == ()
    usage = state.budget_ledger.snapshot()
    assert usage.repair_rounds == 0
    assert usage.schema_retries == 1
    assert usage.tool_calls == 1
    accepted = await tool.invoke(payload(state, bound))
    assert accepted["accepted"] is True
    assert state.budget_ledger.snapshot().repair_rounds == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_concurrent_identical_review_charges_once(bound: bool) -> None:
    state, tool = setup(bound, yielding=True)
    data = payload(state, bound)
    receipts = await asyncio.gather(tool.invoke(data), tool.invoke(data))
    assert sorted(r["idempotent_replay"] for r in receipts) == [False, True]
    assert len(state.submission_board.reviews) == 1
    assert state.budget_ledger.snapshot().repair_rounds == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_valid_review_limit_remains_enforced_without_partial_commit(bound: bool) -> None:
    state, tool = setup(bound)
    for version in (1, 2):
        accepted = await tool.invoke(payload(state, bound, version=version))
        replay = await tool.invoke(payload(state, bound, version=version))
        assert accepted["accepted"] and replay["idempotent_replay"]
        assert state.budget_ledger.snapshot().repair_rounds == version
    with pytest.raises(AgentExecutionError, match="repair-round budget exhausted"):
        await tool.invoke(payload(state, bound, version=3))
    assert len(state.submission_board.reviews) == 2
    assert state.budget_ledger.snapshot().repair_rounds == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_final_review_without_repairs_does_not_charge_at_limit(bound: bool) -> None:
    state, tool = setup(bound)
    for version in (1, 2):
        await tool.invoke(payload(state, bound, version=version))
    data = payload(state, bound, version=3)
    data["review"].update(issues=[], repair_tasks=[])
    await tool.invoke(data)
    assert state.budget_ledger.snapshot().repair_rounds == 2
    assert state.submission_board.latest_review is not None
    assert state.submission_board.latest_review.repair_tasks == ()


@pytest.mark.asyncio
async def test_invalid_review_version_never_charges_repair_budget() -> None:
    state, tool = setup(False)
    with pytest.raises(ValueError, match="version must advance"):
        await tool.invoke(payload(state, False, version=2))
    assert state.budget_ledger.snapshot().repair_rounds == 0
    assert state.budget_ledger.snapshot().schema_retries == 1
    assert not state.submission_board.reviews
