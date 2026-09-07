from __future__ import annotations

from datetime import date

import pytest

from jindiao.contracts.execution import (
    ComparisonFingerprint,
    ExecutionCost,
    LayeredExecutionCost,
    RunTermination,
    RunTerminationReason,
)


def test_layered_cost_keeps_shared_acquisition_separate_from_investigation() -> None:
    acquisition = ExecutionCost(
        llm_requests=1,
        successful_llm_requests=1,
        provider_usage_requests=1,
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        tool_calls=2,
        mcp_calls=1,
        schema_retries=0,
        repair_rounds=0,
        wall_time_ms=50,
    )
    investigation = ExecutionCost(
        llm_requests=3,
        successful_llm_requests=3,
        provider_usage_requests=3,
        input_tokens=500,
        output_tokens=100,
        total_tokens=600,
        tool_calls=4,
        mcp_calls=0,
        schema_retries=1,
        repair_rounds=1,
        wall_time_ms=250,
    )

    costs = LayeredExecutionCost(
        shared_acquisition_cost=acquisition,
        investigation_cost=investigation,
    )

    assert costs.shared_acquisition_cost.total_tokens == 120
    assert costs.investigation_cost.total_tokens == 600
    assert costs.combined.total_tokens == 720


def test_execution_cost_rejects_unreconciled_provider_usage() -> None:
    with pytest.raises(ValueError, match="total_tokens"):
        ExecutionCost(
            llm_requests=1,
            successful_llm_requests=1,
            provider_usage_requests=1,
            input_tokens=5,
            output_tokens=7,
            total_tokens=99,
            tool_calls=0,
            mcp_calls=0,
            schema_retries=0,
            repair_rounds=0,
            wall_time_ms=1,
        )


def test_termination_contract_preserves_completed_and_incomplete_work() -> None:
    termination = RunTermination(
        reason=RunTerminationReason.BUDGET_EXHAUSTED,
        completed_task_ids=("check:registration",),
        incomplete_task_ids=("check:profitability",),
        detail="investigation total-token budget exhausted",
    )

    assert termination.reason is RunTerminationReason.BUDGET_EXHAUSTED

    with pytest.raises(ValueError, match="both completed and incomplete"):
        RunTermination(
            reason=RunTerminationReason.PARTIAL,
            completed_task_ids=("check:same",),
            incomplete_task_ids=("check:same",),
        )


def test_comparison_fingerprint_has_stable_canonical_hash() -> None:
    common = {
        "schema_version": 1,
        "code_version": "git:abc123",
        "contract_schema_version": "contracts-v2",
        "snapshot_id": "snapshot-1",
        "snapshot_sha256": "a" * 64,
        "report_as_of": date(2026, 9, 5),
        "model_provider": "openai-compatible",
        "model_name": "model-a",
        "model_parameters": {"temperature": 0, "top_p": 1},
        "random_seed": 7,
        "common_prompt_sha256": "b" * 64,
        "check_catalog_sha256": "c" * 64,
        "report_catalog_sha256": "d" * 64,
        "investigation_budget": {
            "max_llm_requests": 20,
            "max_input_tokens": 100_000,
            "max_output_tokens": 20_000,
            "max_total_tokens": 120_000,
            "max_wall_time_ms": 300_000,
            "max_concurrency": 4,
            "max_schema_retries": 2,
            "max_repair_rounds": 2,
        },
        "rule_version": "risk-rules-v1",
        "evaluator_version": "evaluator-v1",
    }
    first = ComparisonFingerprint.model_validate(common)
    reordered = dict(reversed(tuple(common.items())))
    reordered["model_parameters"] = {"top_p": 1, "temperature": 0}
    second = ComparisonFingerprint.model_validate(reordered)

    assert first.canonical_json == second.canonical_json
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64

    changed = first.model_copy(update={"snapshot_id": "snapshot-2"})
    assert changed.sha256 != first.sha256
