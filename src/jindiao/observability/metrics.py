"""Deterministic run metrics used by artifacts and benchmarks."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from pydantic import JsonValue

from jindiao.contracts.execution import ExecutionCost


class RunMetricsCollector:
    """Collect phase, latency, and bounded-resource counters for one run."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._phase_started: dict[str, float] = {}
        self._phase_duration_ms: dict[str, int] = {}
        self._first_valid_evidence_ms: int | None = None
        self._execution_cost: ExecutionCost | None = None
        self._observed_cost: ExecutionCost | None = None
        self._provider_usage_complete = False
        self._resources = {
            "tool_calls": 0,
            "token_count": 0,
            "conflicts_detected": 0,
            "repairs_requested": 0,
            "repairs_completed": 0,
        }

    def start_phase(self, phase: str) -> None:
        if not phase or phase in self._phase_started:
            raise ValueError("phase must be non-empty and not already running")
        self._phase_started[phase] = self._monotonic()

    def finish_phase(self, phase: str) -> None:
        try:
            started_at = self._phase_started.pop(phase)
        except KeyError as error:
            raise ValueError(f"phase was not started: {phase}") from error
        self._phase_duration_ms[phase] = max(0, round((self._monotonic() - started_at) * 1000))

    def mark_first_valid_evidence(self) -> None:
        if self._first_valid_evidence_ms is None:
            self._first_valid_evidence_ms = max(
                0, round((self._monotonic() - self._started_at) * 1000)
            )

    def record_resources(
        self,
        *,
        tool_calls: int,
        token_count: int,
        conflicts: int,
        repairs: int,
        repairs_completed: int = 0,
    ) -> None:
        values = (tool_calls, token_count, conflicts, repairs, repairs_completed)
        if any(value < 0 for value in values):
            raise ValueError("resource metrics cannot be negative")
        self._resources = {
            "tool_calls": tool_calls,
            "token_count": token_count,
            "conflicts_detected": conflicts,
            "repairs_requested": repairs,
            "repairs_completed": repairs_completed,
        }

    def observe_model_completion(self, payload: Mapping[str, object]) -> None:
        """Keep numeric provider usage only, as a fallback lower bound, not a ledger."""

        usage = payload.get("usage_metadata")
        if not isinstance(usage, Mapping):
            usage = {}
        input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_value = usage.get("output_tokens", usage.get("completion_tokens"))
        input_known = type(input_value) is int and input_value >= 0
        output_known = type(output_value) is int and output_value >= 0
        input_tokens = input_value if input_known else 0
        output_tokens = output_value if output_known else 0
        observed = ExecutionCost(
            llm_requests=1,
            successful_llm_requests=1,
            provider_usage_requests=int(input_known and output_known),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            tool_calls=0,
            mcp_calls=0,
            schema_retries=0,
            repair_rounds=0,
            wall_time_ms=0,
        )
        self._observed_cost = ExecutionCost.combine(
            self._observed_cost or ExecutionCost.zero(), observed
        )

    def record_execution_cost(self, cost: ExecutionCost, *, provider_usage_complete: bool) -> None:
        """Replace event lower bounds with authoritative, already aggregated costs."""

        self._execution_cost = cost
        self._provider_usage_complete = (
            provider_usage_complete and cost.provider_usage_requests == cost.llm_requests
        )

    def finish(self) -> dict[str, JsonValue]:
        now = self._monotonic()
        for phase, started_at in tuple(self._phase_started.items()):
            self._phase_duration_ms[phase] = max(0, round((now - started_at) * 1000))
            del self._phase_started[phase]
        cost = self._execution_cost or self._observed_cost
        resources = dict(self._resources)
        if cost is not None:
            resources["token_count"] = (
                cost.total_tokens
                if self._execution_cost is not None
                else max(resources["token_count"], cost.total_tokens)
            )
            resources["tool_calls"] = max(resources["tool_calls"], cost.tool_calls)
        return {
            "end_to_end_duration_ms": max(0, round((now - self._started_at) * 1000)),
            "first_valid_evidence_ms": self._first_valid_evidence_ms,
            "phase_duration_ms": dict(sorted(self._phase_duration_ms.items())),
            **resources,
            "execution_cost": cost.model_dump(mode="json") if cost is not None else None,
            "provider_usage_complete": self._provider_usage_complete,
        }


__all__ = ["RunMetricsCollector"]
