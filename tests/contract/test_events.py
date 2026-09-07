from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jindiao.contracts.events import EventSequencer, EventType, RunEvent

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_event_type_contains_the_stable_public_event_set() -> None:
    assert {event.value for event in EventType} == {
        "run.accepted",
        "acquisition.started",
        "acquisition.completed",
        "snapshot.frozen",
        "snapshot.read",
        "snapshot.read_denied",
        "model.request.started",
        "model.request.completed",
        "check.assigned",
        "check.started",
        "check.completed",
        "submission.accepted",
        "submission.rejected",
        "review.submitted",
        "review.issue",
        "review.repair_requested",
        "budget.updated",
        "budget.exhausted",
        "entity.resolved",
        "plan.created",
        "agent.started",
        "evidence.collected",
        "source.fallback",
        "conflict.detected",
        "repair.requested",
        "section.completed",
        "report.completed",
        "skill_evolution.proposed",
        "run.failed",
    }


def test_event_sequencer_emits_monotonic_correlated_events() -> None:
    sequencer = EventSequencer(request_id="req-1", run_id="run-1")

    first = sequencer.next(EventType.RUN_ACCEPTED, {"status": "accepted"}, occurred_at=NOW)
    second = sequencer.next(
        EventType.AGENT_STARTED,
        {"agent_id": "governance-agent"},
        occurred_at=NOW,
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert first.request_id == second.request_id == "req-1"
    assert first.run_id == second.run_id == "run-1"


def test_event_payload_requires_the_type_specific_root_key() -> None:
    with pytest.raises(ValidationError):
        RunEvent(
            event_type=EventType.REPORT_COMPLETED,
            request_id="req-1",
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            payload={"status": "completed"},
        )


def test_public_event_recursively_removes_prompts_reasoning_secrets_and_raw_responses() -> None:
    event = RunEvent(
        event_type=EventType.MODEL_REQUEST_COMPLETED,
        request_id="req-1",
        run_id="run-1",
        sequence=1,
        occurred_at=NOW,
        payload={
            "model": {
                "status": "completed",
                "decision_summary": "证据不足。",
                "system_prompt": "private system prompt",
                "reasoning": "private chain of thought",
                "raw_mcp_response": {"authorization": "Bearer secret-value"},
                "raw_web_response": "private webpage body",
            }
        },
    )

    encoded = event.model_dump_json()
    assert "decision_summary" in encoded
    assert "private system prompt" not in encoded
    assert "private chain of thought" not in encoded
    assert "secret-value" not in encoded
    assert "private webpage body" not in encoded
