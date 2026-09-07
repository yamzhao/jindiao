from __future__ import annotations

from jindiao.api.event_mapper import EventMapper
from jindiao.contracts.events import EventSequencer, EventType
from jindiao.orchestration.base import TeamRuntimeEvent


def test_event_mapper_converts_internal_member_and_deepsearch_events() -> None:
    mapper = EventMapper()
    sequencer = EventSequencer(request_id="req-1", run_id="run-1")

    started = mapper.map(
        TeamRuntimeEvent(
            event_type="member.started",
            member_name="judicial-compliance-agent",
            payload={"task_id": "investigate-judicial"},
        ),
        sequencer,
    )
    fallback = mapper.map(
        TeamRuntimeEvent(
            event_type="deepsearch.fallback",
            member_name="deepsearch-agent",
            payload={"source_status": "capability_absent"},
        ),
        sequencer,
    )

    assert started is not None
    assert started.event_type is EventType.AGENT_STARTED
    assert started.payload["agent_id"] == "judicial-compliance-agent"
    assert fallback is not None
    assert fallback.event_type is EventType.SOURCE_FALLBACK
    assert fallback.sequence == 2


def test_event_mapper_drops_unknown_and_private_runtime_chunks() -> None:
    event = EventMapper().map(
        TeamRuntimeEvent(
            event_type="llm.reasoning.delta",
            member_name="leader",
            payload={"value": "private"},
        ),
        EventSequencer(request_id="req-1", run_id="run-1"),
    )

    assert event is None


def test_event_mapper_maps_acquisition_investigation_and_budget_audit_events() -> None:
    mapper = EventMapper()
    sequencer = EventSequencer(request_id="req-1", run_id="run-1")
    events = [
        mapper.map(
            TeamRuntimeEvent(event_type=event_type, payload={}),
            sequencer,
        )
        for event_type in (
            "acquisition.started",
            "snapshot.frozen",
            "snapshot.read",
            "model.request.completed",
            "check.completed",
            "submission.accepted",
            "review.submitted",
            "budget.updated",
        )
    ]

    assert [item.event_type.value for item in events if item is not None] == [
        "acquisition.started",
        "snapshot.frozen",
        "snapshot.read",
        "model.request.completed",
        "check.completed",
        "submission.accepted",
        "review.submitted",
        "budget.updated",
    ]
    assert [item.sequence for item in events if item is not None] == list(range(1, 9))
