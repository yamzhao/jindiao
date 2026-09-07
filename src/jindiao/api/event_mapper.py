"""Map internal team/DeepSearch events to the stable public event contract."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from jindiao.contracts.events import EventSequencer, EventType, LifecycleEventType, RunEvent
from jindiao.contracts.runs import ActorView, RunStage
from jindiao.orchestration.base import TeamRuntimeEvent

_AUDIT_EVENT_ROOTS = {
    EventType.ACQUISITION_STARTED: "acquisition",
    EventType.ACQUISITION_COMPLETED: "acquisition",
    EventType.SNAPSHOT_FROZEN: "snapshot",
    EventType.SNAPSHOT_READ: "snapshot",
    EventType.SNAPSHOT_READ_DENIED: "snapshot",
    EventType.MODEL_REQUEST_STARTED: "model",
    EventType.MODEL_REQUEST_COMPLETED: "model",
    EventType.CHECK_ASSIGNED: "check",
    EventType.CHECK_STARTED: "check",
    EventType.CHECK_COMPLETED: "check",
    EventType.SUBMISSION_ACCEPTED: "submission",
    EventType.SUBMISSION_REJECTED: "submission",
    EventType.REVIEW_SUBMITTED: "review",
    EventType.REVIEW_ISSUE: "review",
    EventType.REVIEW_REPAIR_REQUESTED: "review",
    EventType.BUDGET_UPDATED: "budget",
    EventType.BUDGET_EXHAUSTED: "budget",
}
_AUDIT_EVENT_BY_VALUE = {item.value: item for item in _AUDIT_EVENT_ROOTS}


class EventMapper:
    def map(
        self,
        event: TeamRuntimeEvent,
        sequencer: EventSequencer,
    ) -> RunEvent | None:
        audit_type = _AUDIT_EVENT_BY_VALUE.get(event.event_type)
        stage = self._stage_for(event)
        actor = ActorView(
            kind="agent" if event.member_name else "system",
            id=event.member_name or "jindiao",
            role=cast(str | None, event.payload.get("role"))
            if isinstance(event.payload.get("role"), str)
            else None,
        )
        task_id = (
            event.payload.get("task_id") if isinstance(event.payload.get("task_id"), str) else None
        )
        check_id = (
            event.payload.get("check_id")
            if isinstance(event.payload.get("check_id"), str)
            else None
        )
        if audit_type is not None:
            audit_payload = dict(event.payload)
            audit_payload.setdefault(
                _AUDIT_EVENT_ROOTS[audit_type],
                cast(JsonValue, {}),
            )
            return sequencer.next(
                audit_type,
                audit_payload,
                stage=stage,
                actor=actor,
                task_id=cast(str | None, task_id),
                check_id=cast(str | None, check_id),
            )
        lifecycle = {item.value: item for item in LifecycleEventType}.get(event.event_type)
        if lifecycle is not None:
            return sequencer.next(
                lifecycle,
                dict(event.payload),
                stage=stage,
                actor=actor,
                task_id=cast(str | None, task_id),
                check_id=cast(str | None, check_id),
            )
        if event.event_type == "entity.resolved":
            return sequencer.next(
                EventType.ENTITY_RESOLVED, dict(event.payload), stage=stage, actor=actor
            )
        if event.event_type == "plan.created":
            return sequencer.next(
                EventType.PLAN_CREATED, dict(event.payload), stage=stage, actor=actor
            )
        if event.event_type in {"member.started", "agent.started"}:
            payload: dict[str, JsonValue] = {
                "agent_id": event.member_name or "unknown-agent",
                **event.payload,
            }
            return sequencer.next(
                EventType.AGENT_STARTED,
                payload,
                stage=stage,
                actor=actor,
                task_id=cast(str | None, task_id),
                check_id=cast(str | None, check_id),
            )
        if event.event_type in {"deepsearch.fallback", "source.fallback"}:
            payload = dict(event.payload)
            payload.setdefault("source_status", "capability_absent")
            return sequencer.next(EventType.SOURCE_FALLBACK, payload, stage=stage, actor=actor)
        if event.event_type == "evidence.collected":
            payload = dict(event.payload)
            payload.setdefault("evidence", cast(JsonValue, {}))
            return sequencer.next(EventType.EVIDENCE_COLLECTED, payload, stage=stage, actor=actor)
        if event.event_type == "conflict.detected":
            payload = dict(event.payload)
            payload.setdefault("issue", cast(JsonValue, {}))
            return sequencer.next(EventType.CONFLICT_DETECTED, payload, stage=stage, actor=actor)
        if event.event_type == "repair.requested":
            payload = dict(event.payload)
            payload.setdefault("repair_task", cast(JsonValue, {}))
            return sequencer.next(EventType.REPAIR_REQUESTED, payload, stage=stage, actor=actor)
        if event.event_type == "section.completed":
            payload = dict(event.payload)
            payload.setdefault("section", cast(JsonValue, {}))
            return sequencer.next(EventType.SECTION_COMPLETED, payload, stage=stage, actor=actor)
        if event.event_type == "skill_evolution.proposed":
            payload = dict(event.payload)
            payload.setdefault("candidate", cast(JsonValue, {}))
            return sequencer.next(
                EventType.SKILL_EVOLUTION_PROPOSED, payload, stage=stage, actor=actor
            )
        return None

    @staticmethod
    def _stage_for(event: TeamRuntimeEvent) -> RunStage:
        raw = event.payload.get("stage")
        if isinstance(raw, str):
            try:
                return RunStage(raw)
            except ValueError:
                pass
        value = event.event_type
        if value.startswith("acquisition") or value.startswith("entity"):
            return RunStage.ACQUISITION
        if value.startswith("snapshot"):
            return RunStage.SNAPSHOT
        if value.startswith(("review", "repair", "conflict")):
            return RunStage.ADJUDICATION
        if value.startswith(("section", "report")):
            return RunStage.REPORTING
        if value.startswith(("check", "submission", "agent", "model", "plan")):
            return RunStage.INVESTIGATION
        return RunStage.ACCEPTED


__all__ = ["EventMapper"]
