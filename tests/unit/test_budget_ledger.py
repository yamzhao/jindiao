from __future__ import annotations

import asyncio

import pytest

from jindiao.application.errors import AgentExecutionError
from jindiao.orchestration import BudgetLedger, RunBudget


def budget(**overrides: int) -> RunBudget:
    values = {
        "max_tool_calls": 3,
        "max_concurrency": 1,
        "timeout_seconds": 10,
        "max_repair_rounds": 1,
        "max_llm_requests": 2,
        "max_input_tokens": 5,
        "max_output_tokens": 5,
        "max_total_tokens": 8,
        "max_schema_retries": 1,
        "max_snapshot_reads": 1,
    }
    values.update(overrides)
    return RunBudget(**values)


@pytest.mark.asyncio
async def test_budget_ledger_atomically_records_every_investigation_resource() -> None:
    now = [100.0]
    ledger = BudgetLedger(budget(), monotonic=lambda: now[0])

    await ledger.claim_llm_request("investigate")
    await ledger.record_llm_usage(input_tokens=3, output_tokens=2, provider_usage=True)
    await ledger.claim_tool_call("submit_check_result")
    await ledger.claim_snapshot_read("read_snapshot_submodule")
    await ledger.claim_schema_retry("check-result-schema")
    await ledger.claim_repair_round("repair-registration")
    async with ledger.operation_slot("model-request"):
        assert ledger.snapshot().active_operations == 1

    usage = ledger.snapshot()
    assert usage.llm_requests == 1
    assert usage.successful_llm_requests == 1
    assert usage.provider_usage_requests == 1
    assert usage.input_tokens == 3
    assert usage.output_tokens == 2
    assert usage.total_tokens == 5
    assert usage.tool_calls == 2
    assert usage.snapshot_reads == 1
    assert usage.schema_retries == 1
    assert usage.repair_rounds == 1
    assert usage.peak_concurrency == 1
    assert usage.active_operations == 0
    assert ledger.to_execution_cost().total_tokens == 5


@pytest.mark.asyncio
async def test_budget_ledger_rejects_limits_and_keeps_actual_overshoot_accounted() -> None:
    now = [100.0]
    ledger = BudgetLedger(budget(), monotonic=lambda: now[0])
    await ledger.claim_llm_request("first")

    with pytest.raises(AgentExecutionError, match="token budget exhausted"):
        await ledger.record_llm_usage(
            input_tokens=6,
            output_tokens=0,
            provider_usage=True,
        )
    assert ledger.snapshot().input_tokens == 6

    concurrency_ledger = BudgetLedger(budget(), monotonic=lambda: now[0])
    async with concurrency_ledger.operation_slot("first-slot"):
        with pytest.raises(AgentExecutionError, match="concurrency"):
            async with concurrency_ledger.operation_slot("second-slot"):
                pytest.fail("a second slot must not be granted")

    deadline_ledger = BudgetLedger(budget(), monotonic=lambda: now[0])
    now[0] = 111.0
    with pytest.raises(AgentExecutionError, match="deadline"):
        await deadline_ledger.claim_tool_call("late-tool")


@pytest.mark.asyncio
async def test_budget_ledger_claims_are_atomic_under_concurrency() -> None:
    ledger = BudgetLedger(budget(max_tool_calls=3), monotonic=lambda: 100.0)

    async def claim(index: int) -> bool:
        try:
            await ledger.claim_tool_call(f"tool-{index}")
        except AgentExecutionError:
            return False
        return True

    outcomes = await asyncio.gather(*(claim(index) for index in range(20)))

    assert sum(outcomes) == 3
    assert ledger.snapshot().tool_calls == 3


@pytest.mark.asyncio
async def test_reservation_caps_output_and_blocks_combined_inflight_inputs() -> None:
    ledger = BudgetLedger(budget(), monotonic=lambda: 100.0)
    first = await ledger.reserve_llm_request("first", input_tokens=3, output_tokens=20)
    assert first.output_tokens == 5
    assert ledger.snapshot().reserved_input_tokens == 3
    assert ledger.snapshot().reserved_output_tokens == 5
    with pytest.raises(AgentExecutionError, match="token budget"):
        await ledger.reserve_llm_request("second", input_tokens=3, output_tokens=1)
    assert ledger.snapshot().llm_requests == 1
    await ledger.complete_llm_request(first, input_tokens=3, output_tokens=1, provider_usage=True)
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().input_tokens == 3


@pytest.mark.asyncio
async def test_late_usage_is_recorded_before_deadline_error_with_safe_actual_cost() -> None:
    now = [100.0]
    ledger = BudgetLedger(budget(), monotonic=lambda: now[0])
    await ledger.claim_llm_request("first")
    now[0] = 111.0
    with pytest.raises(AgentExecutionError, match="deadline") as caught:
        await ledger.record_llm_usage(input_tokens=3, output_tokens=1, provider_usage=True)
    assert ledger.snapshot().input_tokens == 3
    assert caught.value.details["execution_cost"]["total_tokens"] == 4
    assert caught.value.details["provider_usage_complete"] is True


@pytest.mark.asyncio
async def test_reservation_overshoot_is_recorded_and_release_is_idempotent() -> None:
    ledger = BudgetLedger(budget(), monotonic=lambda: 100.0)
    pending = await ledger.reserve_llm_request("first", input_tokens=2, output_tokens=2)
    with pytest.raises(AgentExecutionError, match="token budget") as caught:
        await ledger.complete_llm_request(
            pending, input_tokens=6, output_tokens=2, provider_usage=True
        )
    await ledger.release_llm_request(pending)
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().input_tokens == 6
    assert caught.value.details["execution_cost"]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_cancel_before_dispatch_releases_claim_but_unknown_usage_stays_unreported() -> None:
    ledger = BudgetLedger(budget(), monotonic=lambda: 100.0)
    pending = await ledger.reserve_llm_request("first", input_tokens=2, output_tokens=2)
    await ledger.release_llm_request(pending)
    await ledger.release_llm_request(pending)
    assert ledger.snapshot().llm_requests == 0
    pending = await ledger.reserve_llm_request("retry", input_tokens=2, output_tokens=2)
    await ledger.complete_llm_request(
        pending, input_tokens=None, output_tokens=None, provider_usage=False, succeeded=False
    )
    usage = ledger.snapshot()
    assert usage.unknown_usage_requests == 1
    assert usage.unreported_input_tokens == 2
    assert usage.unreported_output_tokens == 2
    assert usage.reserved_input_tokens == 0
    assert usage.provider_usage_requests == 0
    assert usage.successful_llm_requests == 0
    assert usage.input_tokens == 0
    with pytest.raises(AgentExecutionError, match="token budget") as caught:
        await ledger.reserve_llm_request("next", input_tokens=4, output_tokens=1)
    assert caught.value.details["provider_usage_complete"] is False


@pytest.mark.asyncio
async def test_token_reservations_are_atomic_under_concurrency() -> None:
    ledger = BudgetLedger(budget(max_llm_requests=20), monotonic=lambda: 100.0)

    async def reserve(index: int) -> bool:
        try:
            await ledger.reserve_llm_request(str(index), input_tokens=2, output_tokens=2)
        except AgentExecutionError:
            return False
        return True

    assert sum(await asyncio.gather(*(reserve(index) for index in range(20)))) == 2
    assert ledger.snapshot().reserved_input_tokens == 4
    assert ledger.snapshot().reserved_output_tokens == 4
