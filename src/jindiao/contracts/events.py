"""Stable public events emitted by JSON/SSE runs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from jindiao.security import redact_json

from .base import ContractModel
from .execution_steps import ExecutionPlan, ExecutionStepSnapshot, ExecutionStepState
from .runs import ActorView, RunStage


class EventType(StrEnum):
    RUN_ACCEPTED = "run.accepted"
    ACQUISITION_STARTED = "acquisition.started"
    ACQUISITION_COMPLETED = "acquisition.completed"
    SNAPSHOT_FROZEN = "snapshot.frozen"
    SNAPSHOT_READ = "snapshot.read"
    SNAPSHOT_READ_DENIED = "snapshot.read_denied"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_REQUEST_COMPLETED = "model.request.completed"
    CHECK_ASSIGNED = "check.assigned"
    CHECK_STARTED = "check.started"
    CHECK_COMPLETED = "check.completed"
    SUBMISSION_ACCEPTED = "submission.accepted"
    SUBMISSION_REJECTED = "submission.rejected"
    REVIEW_SUBMITTED = "review.submitted"
    REVIEW_ISSUE = "review.issue"
    REVIEW_REPAIR_REQUESTED = "review.repair_requested"
    BUDGET_UPDATED = "budget.updated"
    BUDGET_EXHAUSTED = "budget.exhausted"
    ENTITY_RESOLVED = "entity.resolved"
    PLAN_CREATED = "plan.created"
    AGENT_STARTED = "agent.started"
    EVIDENCE_COLLECTED = "evidence.collected"
    SOURCE_FALLBACK = "source.fallback"
    CONFLICT_DETECTED = "conflict.detected"
    REPAIR_REQUESTED = "repair.requested"
    SECTION_COMPLETED = "section.completed"
    REPORT_COMPLETED = "report.completed"
    SKILL_EVOLUTION_PROPOSED = "skill_evolution.proposed"
    RUN_FAILED = "run.failed"


class LifecycleEventType(StrEnum):
    """Lifecycle events added by the versioned Run API.

    Kept separate from :class:`EventType` so existing clients that enumerate the
    original event set remain source compatible.
    """

    RUN_STARTED = "run.started"
    RUN_PHASE_STARTED = "run.phase.started"
    RUN_PHASE_COMPLETED = "run.phase.completed"
    RUN_COMPLETED = "run.completed"
    RUN_PARTIAL = "run.partial"
    RUN_CANCEL_REQUESTED = "run.cancel_requested"
    RUN_CANCELLED = "run.cancelled"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    AGENT_CANCELLED = "agent.cancelled"


class ExecutionEventType(StrEnum):
    """Stable semantic events intended for product execution timelines."""

    PLAN_CREATED = "execution.plan.created"
    STEP_STARTED = "execution.step.started"
    STEP_PROGRESS = "execution.step.progress"
    STEP_COMPLETED = "execution.step.completed"
    STEP_FAILED = "execution.step.failed"


REQUIRED_PAYLOAD_KEY: dict[EventType, str] = {
    EventType.RUN_ACCEPTED: "status",
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
    EventType.ENTITY_RESOLVED: "subject",
    EventType.PLAN_CREATED: "plan",
    EventType.AGENT_STARTED: "agent_id",
    EventType.EVIDENCE_COLLECTED: "evidence",
    EventType.SOURCE_FALLBACK: "source_status",
    EventType.CONFLICT_DETECTED: "issue",
    EventType.REPAIR_REQUESTED: "repair_task",
    EventType.SECTION_COMPLETED: "section",
    EventType.REPORT_COMPLETED: "result",
    EventType.SKILL_EVOLUTION_PROPOSED: "candidate",
    EventType.RUN_FAILED: "error",
}


class RunEvent(ContractModel):
    event_type: EventType | LifecycleEventType | ExecutionEventType
    schema_version: int = Field(default=1, ge=1)
    event_id: str | None = None
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime
    stage: RunStage = RunStage.ACCEPTED
    actor: ActorView = Field(
        default_factory=lambda: ActorView(kind="system", id="jindiao", role="coordinator")
    )
    task_id: str | None = Field(default=None, min_length=1)
    check_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, JsonValue]

    @model_validator(mode="before")
    @classmethod
    def populate_event_id(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("event_id") is None:
            value = dict(value)
            if value.get("run_id") and value.get("sequence"):
                value["event_id"] = f"{value['run_id']}:{value['sequence']}"
        return value

    @field_validator("payload", mode="before")
    @classmethod
    def redact_private_payload(cls, value: object) -> dict[str, JsonValue]:
        safe = redact_json(value)
        if not isinstance(safe, dict):
            raise ValueError("public event payload must be an object")
        return safe

    @model_validator(mode="after")
    def require_type_specific_payload(self) -> RunEvent:
        if isinstance(self.event_type, EventType):
            required_key = REQUIRED_PAYLOAD_KEY[self.event_type]
            if required_key not in self.payload:
                raise ValueError(f"{self.event_type.value} payload requires {required_key}")
        elif isinstance(self.event_type, ExecutionEventType):
            key = "plan" if self.event_type is ExecutionEventType.PLAN_CREATED else "step"
            if key not in self.payload:
                raise ValueError(f"{self.event_type.value} payload requires {key}")
            model = ExecutionPlan if key == "plan" else ExecutionStepSnapshot
            snapshot = model.model_validate(self.payload[key])
            if set(self.payload) != {key}:
                raise ValueError("execution payload must contain only the typed snapshot")
            if isinstance(snapshot, ExecutionStepSnapshot):
                expected = {
                    ExecutionEventType.STEP_STARTED: ExecutionStepState.RUNNING,
                    ExecutionEventType.STEP_PROGRESS: ExecutionStepState.RUNNING,
                    ExecutionEventType.STEP_COMPLETED: ExecutionStepState.COMPLETED,
                    ExecutionEventType.STEP_FAILED: ExecutionStepState.FAILED,
                }[self.event_type]
                if snapshot.state is not expected:
                    raise ValueError("execution event type does not match step state")
        return self


class EventSequencer:
    """Allocate monotonically increasing sequence numbers for one run."""

    def __init__(self, *, request_id: str, run_id: str) -> None:
        self._request_id = request_id
        self._run_id = run_id
        self._sequence = 0

    def next(
        self,
        event_type: EventType | LifecycleEventType | ExecutionEventType | str,
        payload: dict[str, JsonValue],
        *,
        occurred_at: datetime | None = None,
        stage: RunStage = RunStage.ACCEPTED,
        actor: ActorView | None = None,
        task_id: str | None = None,
        check_id: str | None = None,
    ) -> RunEvent:
        if isinstance(event_type, str):
            try:
                event_type = EventType(event_type)
            except ValueError:
                try:
                    event_type = LifecycleEventType(event_type)
                except ValueError:
                    event_type = ExecutionEventType(event_type)
        self._sequence += 1
        return RunEvent(
            event_type=event_type,
            request_id=self._request_id,
            run_id=self._run_id,
            sequence=self._sequence,
            occurred_at=occurred_at or datetime.now(UTC),
            stage=stage,
            actor=actor or ActorView(kind="system", id="jindiao", role="coordinator"),
            task_id=task_id,
            check_id=check_id,
            payload=payload,
        )

    @property
    def sequence(self) -> int:
        return self._sequence

    def restore(self, sequence: int) -> None:
        if sequence < self._sequence:
            raise ValueError("cannot restore sequencer backwards")
        self._sequence = sequence


__all__ = [
    "EventSequencer",
    "EventType",
    "ExecutionEventType",
    "LifecycleEventType",
    "RunEvent",
]
