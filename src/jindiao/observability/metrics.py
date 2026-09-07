"""Deterministic run metrics used by artifacts and benchmarks."""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import JsonValue


class RunMetricsCollector:
    """Collect phase, latency, and bounded-resource counters for one run."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._phase_started: dict[str, float] = {}
        self._phase_duration_ms: dict[str, int] = {}
        self._first_valid_evidence_ms: int | None = None
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

    def finish(self) -> dict[str, JsonValue]:
        now = self._monotonic()
        for phase, started_at in tuple(self._phase_started.items()):
            self._phase_duration_ms[phase] = max(0, round((now - started_at) * 1000))
            del self._phase_started[phase]
        return {
            "end_to_end_duration_ms": max(0, round((now - self._started_at) * 1000)),
            "first_valid_evidence_ms": self._first_valid_evidence_ms,
            "phase_duration_ms": dict(sorted(self._phase_duration_ms.items())),
            **self._resources,
        }


__all__ = ["RunMetricsCollector"]
