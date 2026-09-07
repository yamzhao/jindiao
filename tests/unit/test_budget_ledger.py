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
