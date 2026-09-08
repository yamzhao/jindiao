from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import JsonValue, ValidationError

from jindiao.application.execution_steps import build_execution_plan
from jindiao.contracts.events import EventSequencer, EventType, ExecutionEventType, RunEvent
from jindiao.contracts.execution_steps import ExecutionPlan, ExecutionStepSnapshot
from jindiao.contracts.results import OrchestrationMode


def snapshot(**overrides: Any) -> dict[str, Any]:
    definition = build_execution_plan(OrchestrationMode.SINGLE).steps[0]
    return {**definition.model_dump(), "state": "running", **overrides}


def test_plan_has_same_seven_ordered_steps_for_each_topology() -> None:
    single = build_execution_plan(OrchestrationMode.SINGLE)
    multi = build_execution_plan(OrchestrationMode.MULTI)
    assert single.steps == multi.steps
    assert len(single.steps) == 7
    raw = single.model_dump(mode="json")
    raw["steps"].reverse()
    with pytest.raises(ValidationError, match="fixed seven steps"):
        ExecutionPlan.model_validate(raw)


@pytest.mark.parametrize(
    "overrides",
    [
        {"step_id": "invented"},
        {"order": 2},
        {"conclusion": "x" * 241},
        {"executor_ids": ["agent", "agent"]},
        {"executor_ids": ["x" * 161]},
        {"gaps": ["duplicate", "duplicate"]},
        {"gaps": ["x" * 241]},
        {"outcome": "normal"},
        {"state": "completed", "conclusion": "done"},
        {"state": "failed"},
        {"progress_message": "x" * 161},
        {"key_facts": [{"text": "fact", "evidence_ids": ["missing"]}]},
        {"key_facts": [{"text": "same"}, {"text": "same"}]},
        {"key_facts": [{"text": "fact", "evidence_ids": ["a", "a"]}]},
        {"source_tags": [{"label": "source", "evidence_id": "a", "source_type": "tianyancha"}] * 2},
    ],
)
def test_invalid_snapshot_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExecutionStepSnapshot.model_validate(snapshot(**overrides))


def test_event_validates_full_snapshot_and_matching_lifecycle() -> None:
    sequencer = EventSequencer(request_id="req", run_id="run")
    step = snapshot(
        state="completed",
        outcome="attention",
        conclusion="核查发现异常",
        progress_percent=100,
        key_facts=[{"text": "存在执行事项", "evidence_ids": ["judicial"]}],
        source_tags=[
            {"label": "天眼查·司法", "evidence_id": "judicial", "source_type": "tianyancha"}
        ],
    )
    event = sequencer.next("execution.step.completed", {"step": step})
    assert isinstance(event.event_type, ExecutionEventType)
    assert not isinstance(event.event_type, EventType)
    assert RunEvent.model_validate_json(event.model_dump_json()) == event
    for payload in ({"step": step, "raw_output": "private"}, {}, {"step": snapshot()}):
        with pytest.raises(ValidationError):
            sequencer.next(ExecutionEventType.STEP_COMPLETED, cast(dict[str, JsonValue], payload))
    with pytest.raises(ValidationError):
        sequencer.next(ExecutionEventType.STEP_STARTED, {"step": step})


def test_maximal_execution_frame_is_below_bff_limit() -> None:
    ids = [str(i) + "证" * 159 for i in range(8)]
    step = ExecutionStepSnapshot.model_validate(
        snapshot(
            state="completed",
            outcome="inconclusive",
            conclusion="结" * 240,
            progress_message="进" * 160,
            progress_percent=100,
            gaps=[str(i) + "缺" * 239 for i in range(3)],
            executor_ids=ids,
            key_facts=[{"text": str(i) + "实" * 239, "evidence_ids": ids} for i in range(3)],
            source_tags=[
                {"label": "源" * 80, "evidence_id": item, "source_type": "tianyancha"}
                for item in ids
            ],
        )
    )
    event = EventSequencer(request_id="req", run_id="run").next(
        ExecutionEventType.STEP_COMPLETED, {"step": step.model_dump(mode="json")}
    )
    assert len(event.model_dump_json().encode()) + 100 < 262144
