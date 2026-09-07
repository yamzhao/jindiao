"""In-process Run metadata, event storage, projection and publication.

The interfaces are intentionally small so AgentArts session/SFS adapters can be
introduced without coupling the coordinator to a particular persistence engine.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from pydantic import JsonValue

from jindiao.contracts.events import EventSequencer, EventType, LifecycleEventType, RunEvent
from jindiao.contracts.execution import RunTermination, RunTerminationReason
from jindiao.contracts.results import AgentStatus, DueDiligenceResult, RunStatus
from jindiao.contracts.runs import (
    ActorView,
    AgentView,
    BudgetView,
    CheckView,
    ReviewView,
    RunProgress,
    RunResource,
    RunStage,
)
from jindiao.security import redact_json


def _safe_segment(value: str) -> str:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise ValueError("run_id must be one safe path segment")
    return value


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


class RunRepository(Protocol):
    async def create(
        self,
        resource: RunResource,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[RunResource, bool]: ...

    async def get(self, run_id: str) -> RunResource | None: ...

    async def save(self, resource: RunResource) -> RunResource: ...

    async def save_result(self, run_id: str, result: DueDiligenceResult) -> None: ...

    async def get_result(self, run_id: str) -> DueDiligenceResult | None: ...

    async def save_error(self, run_id: str, error: object) -> None: ...


class EventStore(Protocol):
    async def append(self, event: RunEvent) -> RunEvent: ...

    async def read_after(self, run_id: str, after: int = 0) -> tuple[RunEvent, ...]: ...

    def subscribe(self, run_id: str, *, after: int = 0) -> AsyncIterator[RunEvent]: ...

    async def last_sequence(self, run_id: str) -> int: ...


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._runs: dict[str, RunResource] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        self._results: dict[str, DueDiligenceResult] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        resource: RunResource,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[RunResource, bool]:
        async with self._lock:
            if idempotency_key is not None:
                if not request_hash:
                    raise ValueError("idempotency request hash is required")
                key = (resource.owner_id, idempotency_key)
                existing = self._idempotency.get(key)
                if existing is not None:
                    existing_run_id, existing_hash = existing
                    if existing_hash != request_hash:
                        raise ValueError("idempotency key conflicts with an existing request")
                    return self._runs[existing_run_id], False
                self._idempotency[key] = (resource.run_id, request_hash)
            self._runs[resource.run_id] = resource
            return resource, True

    async def get(self, run_id: str) -> RunResource | None:
        async with self._lock:
            resource = self._runs.get(run_id)
            return resource.model_copy(deep=True) if resource is not None else None

    async def save(self, resource: RunResource) -> RunResource:
        async with self._lock:
            if resource.run_id not in self._runs:
                raise KeyError(resource.run_id)
            self._runs[resource.run_id] = resource
            return resource

    async def save_result(self, run_id: str, result: DueDiligenceResult) -> None:
        async with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            self._results[run_id] = result

    async def get_result(self, run_id: str) -> DueDiligenceResult | None:
        async with self._lock:
            return self._results.get(run_id)

    async def save_error(self, run_id: str, error: object) -> None:
        # Error details belong to the public Run projection; the repository keeps
        # this hook to allow durable adapters to persist a separate error record.
        if await self.get(run_id) is None:
            raise KeyError(run_id)

    async def recover_running(self) -> tuple[RunResource, ...]:
        """Mark in-process runs as interrupted after a coordinator restart."""

        recovered: list[RunResource] = []
        async with self._lock:
            for run_id, resource in tuple(self._runs.items()):
                if resource.status is not RunStatus.RUNNING:
                    continue
                updated = resource.model_copy(
                    update={
                        "status": RunStatus.FAILED,
                        "stage": RunStage.TERMINAL,
                        "completed_at": datetime.now(UTC),
                        "termination": RunTermination(reason=RunTerminationReason.INTERRUPTED),
                    },
                    deep=True,
                )
                self._runs[run_id] = updated
                recovered.append(updated)
        return tuple(recovered)


class JsonRunRepository(InMemoryRunRepository):
    """Safe JSON adapter for local harnesses and mounted session/SFS storage."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_idempotency()

    def _path(self, run_id: str) -> Path:
        return self.root / _safe_segment(run_id) / "metadata.json"

    def _load_idempotency(self) -> None:
        path = self.root / "idempotency.json"
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if (
                isinstance(key, str)
                and isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], str)
            ):
                owner, idempotency_key = key.split("\u0000", 1) if "\u0000" in key else ("", key)
                self._idempotency[(owner, idempotency_key)] = (value[0], value[1])

    def _persist_idempotency(self) -> None:
        payload = {
            f"{owner}\u0000{key}": [run_id, request_hash]
            for (owner, key), (run_id, request_hash) in self._idempotency.items()
        }
        self._write(self.root / "idempotency.json", payload)

    async def create(
        self,
        resource: RunResource,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[RunResource, bool]:
        if idempotency_key is not None:
            existing = self._idempotency.get((resource.owner_id, idempotency_key))
            if existing is not None:
                loaded = await self.get(existing[0])
                if loaded is None:
                    raise ValueError("idempotency record references a missing Run")
        existing_resource = await self.get(resource.run_id)
        if existing_resource is not None:
            return existing_resource, False
        result = await super().create(
            resource, idempotency_key=idempotency_key, request_hash=request_hash
        )
        if result[1]:
            self._write(self._path(resource.run_id), resource.model_dump(mode="json"))
            self._persist_idempotency()
        return result

    async def get(self, run_id: str) -> RunResource | None:
        current = await super().get(run_id)
        if current is not None:
            return current
        path = self._path(run_id)
        if not path.is_file():
            return None
        resource = RunResource.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self._runs[run_id] = resource
        return resource.model_copy(deep=True)

    async def save(self, resource: RunResource) -> RunResource:
        if await self.get(resource.run_id) is None:
            raise KeyError(resource.run_id)
        self._write(self._path(resource.run_id), resource.model_dump(mode="json"))
        return await super().save(resource)

    async def save_result(self, run_id: str, result: DueDiligenceResult) -> None:
        if await self.get(run_id) is None:
            raise KeyError(run_id)
        self._write(
            self.root / _safe_segment(run_id) / "result.json",
            result.model_dump(mode="json"),
        )
        await super().save_result(run_id, result)

    async def get_result(self, run_id: str) -> DueDiligenceResult | None:
        result = await super().get_result(run_id)
        if result is not None:
            return result
        path = self.root / _safe_segment(run_id) / "result.json"
        if not path.is_file():
            return None
        loaded = DueDiligenceResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self._results[run_id] = loaded
        return loaded

    async def save_error(self, run_id: str, error: object) -> None:
        await super().save_error(run_id, error)
        self._write(self.root / _safe_segment(run_id) / "error.json", error)

    async def recover_running(self) -> tuple[RunResource, ...]:
        for path in self.root.glob("*/metadata.json"):
            run_id = path.parent.name
            try:
                loaded = RunResource.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if loaded.run_id == run_id:
                self._runs[run_id] = loaded
        recovered = await super().recover_running()
        for resource in recovered:
            self._write(self._path(resource.run_id), resource.model_dump(mode="json"))
        return recovered

    @staticmethod
    def _write(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(redact_json(value), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class InMemoryEventStore:
    def __init__(self, *, max_queue: int = 256) -> None:
        self._events: dict[str, list[RunEvent]] = {}
        self._queues: dict[str, set[asyncio.Queue[RunEvent]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._max_queue = max_queue

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def append(self, event: RunEvent) -> RunEvent:
        async with self._lock_for(event.run_id):
            events = self._events.setdefault(event.run_id, [])
            terminal_types = {"run.completed", "run.partial", "run.failed", "run.cancelled"}
            event_name = (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            )
            if event_name in terminal_types and any(
                (
                    item.event_type.value
                    if hasattr(item.event_type, "value")
                    else str(item.event_type)
                )
                in terminal_types
                for item in events
            ):
                raise ValueError("a Run may contain only one terminal event")
            if events and event.sequence <= events[-1].sequence:
                for previous in events:
                    if previous.event_id == event.event_id:
                        return previous
                raise ValueError("event sequence must be monotonic and unique")
            expected = events[-1].sequence + 1 if events else 1
            if event.sequence != expected:
                raise ValueError(f"event sequence must be {expected}")
            events.append(event)
            for queue in tuple(self._queues.get(event.run_id, ())):
                if queue.full():
                    # Keep terminal/business events; telemetry may be replayed.
                    if event.event_type not in {
                        "run.completed",
                        "run.partial",
                        "run.failed",
                        "run.cancelled",
                        "submission.accepted",
                        "review.submitted",
                    }:
                        continue
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(event)
            return event

    async def read_after(self, run_id: str, after: int = 0) -> tuple[RunEvent, ...]:
        async with self._lock_for(run_id):
            return tuple(item for item in self._events.get(run_id, ()) if item.sequence > after)

    async def last_sequence(self, run_id: str) -> int:
        async with self._lock_for(run_id):
            events = self._events.get(run_id, ())
            return events[-1].sequence if events else 0

    async def _register(self, run_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock_for(run_id):
            self._queues.setdefault(run_id, set()).add(queue)
        return queue

    async def _unregister(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        async with self._lock_for(run_id):
            self._queues.get(run_id, set()).discard(queue)

    async def _subscribe(self, run_id: str, after: int) -> AsyncIterator[RunEvent]:
        queue = await self._register(run_id)
        try:
            # Register first, then replay, and dedupe by sequence to avoid a gap.
            history = await self.read_after(run_id, after)
            seen = {event.sequence for event in history}
            for event in history:
                yield event
            while True:
                event = await queue.get()
                if event.sequence > after and event.sequence not in seen:
                    seen.add(event.sequence)
                    yield event
        finally:
            await self._unregister(run_id, queue)

    def subscribe(self, run_id: str, *, after: int = 0) -> AsyncIterator[RunEvent]:
        return self._subscribe(run_id, after)


class JsonlEventStore(InMemoryEventStore):
    """Replayable local EventStore used by deterministic harnesses.

    Each append is first persisted as a complete JSON line and then fanned out by
    the in-memory implementation.  Production AgentArts deployments should use
    the session/SFS adapter after its storage probe has passed.
    """

    def __init__(self, root: Path, *, max_queue: int = 256) -> None:
        super().__init__(max_queue=max_queue)
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def append(self, event: RunEvent) -> RunEvent:
        path = self.root / f"{_safe_segment(event.run_id)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = await self.read_after(event.run_id, after=0)
        for previous in existing:
            if previous.event_id == event.event_id:
                return previous
        if existing:
            expected = existing[-1].sequence + 1
            if event.sequence != expected:
                raise ValueError(f"event sequence must be {expected}")
            terminal_types = {"run.completed", "run.partial", "run.failed", "run.cancelled"}
            event_name = (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            )
            if event_name in terminal_types and any(
                (
                    item.event_type.value
                    if hasattr(item.event_type, "value")
                    else str(item.event_type)
                )
                in terminal_types
                for item in existing
            ):
                raise ValueError("a Run may contain only one terminal event")
        elif event.sequence != 1:
            raise ValueError("event sequence must be 1")
        line = (
            json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
        return await super().append(event)

    async def _load_from_disk(self, run_id: str) -> None:
        if getattr(self, "_events", {}).get(run_id):
            return
        path = self.root / f"{_safe_segment(run_id)}.jsonl"
        if not path.is_file():
            return
        events = [
            RunEvent.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self._events[run_id] = events

    async def read_after(self, run_id: str, after: int = 0) -> tuple[RunEvent, ...]:
        await self._load_from_disk(run_id)
        return await super().read_after(run_id, after)

    async def last_sequence(self, run_id: str) -> int:
        await self._load_from_disk(run_id)
        return await super().last_sequence(run_id)


class RunProjection:
    """Fold public events into a browser-oriented RunResource."""

    def __init__(self, resource: RunResource) -> None:
        self.resource = resource

    def apply(self, event: RunEvent) -> RunResource:
        current = self.resource
        status = current.status
        stage = current.stage
        values: dict[str, object] = {
            "latest_sequence": max(current.latest_sequence, event.sequence),
        }
        event_type = (
            event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        )
        payload = event.payload
        if event_type in {"run.started", "run.accepted"}:
            status = RunStatus.RUNNING if event_type == "run.started" else RunStatus.ACCEPTED
            values["started_at"] = current.started_at or event.occurred_at
        elif event_type in {"run.phase.started", "run.phase.completed"}:
            raw_stage = payload.get("stage", event.stage.value)
            try:
                stage = RunStage(str(raw_stage))
            except ValueError:
                stage = event.stage
            if event_type == "run.phase.started":
                status = RunStatus.RUNNING
        elif event_type in {"run.completed", "report.completed"}:
            status, stage, values["completed_at"] = (
                RunStatus.COMPLETED,
                RunStage.TERMINAL,
                event.occurred_at,
            )
            if event_type == "run.completed" or "result" in payload:
                values["result_available"] = True
            if event_type == "run.completed":
                values["termination"] = RunTermination(reason=RunTerminationReason.COMPLETED)
        elif event_type == "run.partial":
            status, stage = RunStatus.PARTIAL, RunStage.TERMINAL
            values["result_available"] = True
            values["completed_at"] = event.occurred_at
            values["termination"] = RunTermination(reason=RunTerminationReason.PARTIAL)
        elif event_type == "run.failed":
            status, stage = RunStatus.FAILED, RunStage.TERMINAL
            values["completed_at"] = event.occurred_at
            values["termination"] = RunTermination(reason=RunTerminationReason.FAILED)
        elif event_type == "run.cancelled":
            status, stage = RunStatus.CANCELLED, RunStage.TERMINAL
            values["completed_at"] = event.occurred_at
            values["termination"] = RunTermination(reason=RunTerminationReason.CANCELLED)
        elif event_type in {"acquisition.started", "acquisition.completed"}:
            stage = RunStage.ACQUISITION
            if event_type == "acquisition.started":
                values["acquisition"] = RunProgress(completed=0, total=48)
            elif event_type == "acquisition.completed":
                values["acquisition"] = RunProgress(completed=48, total=48)
            else:
                values["acquisition"] = self._progress_from_payload(
                    current.acquisition, payload.get("acquisition")
                )
        elif event_type == "snapshot.frozen":
            stage = RunStage.SNAPSHOT
        elif event_type in {"agent.started", "agent.completed", "agent.failed", "agent.cancelled"}:
            stage = event.stage if event.stage is not RunStage.ACCEPTED else RunStage.INVESTIGATION
            values["agents"] = self._upsert_agent(current.agents, event, event_type)
        elif event_type.startswith("check.") or event_type == "submission.accepted":
            stage = RunStage.INVESTIGATION
            values["checks"] = self._upsert_check(current.checks, event, event_type)
            values["investigation"] = self._progress_from_checks(values["checks"])
        elif event_type.startswith("review.") or event_type == "repair.requested":
            stage = RunStage.ADJUDICATION
            values["review"] = self._update_review(current.review, event, event_type)
        elif event_type.startswith("budget."):
            raw_budget = payload.get("budget")
            if isinstance(raw_budget, dict):
                used = raw_budget.get("used")
                remaining = raw_budget.get("remaining")
                used_value = (
                    cast(dict[str, int], used)
                    if isinstance(used, dict)
                    and all(
                        isinstance(key, str) and isinstance(value, int)
                        for key, value in used.items()
                    )
                    else current.budget.used
                )
                remaining_value = (
                    cast(dict[str, int], remaining)
                    if isinstance(remaining, dict)
                    and all(
                        isinstance(key, str) and isinstance(value, int)
                        for key, value in remaining.items()
                    )
                    else current.budget.remaining
                )
                peak = raw_budget.get("peak_concurrency", current.budget.peak_concurrency)
                values["budget"] = BudgetView(
                    used=used_value,
                    remaining=remaining_value,
                    peak_concurrency=(
                        peak if isinstance(peak, int) else current.budget.peak_concurrency
                    ),
                )
        elif event_type.startswith("section.") or event_type == "report.completed":
            stage = RunStage.REPORTING
            if event_type == "section.completed":
                current_reporting = current.reporting
                values["reporting"] = RunProgress(
                    completed=min(current_reporting.total or 8, current_reporting.completed + 1),
                    total=current_reporting.total or 8,
                )

        values["status"] = status
        values["stage"] = stage
        self.resource = current.model_copy(update=values, deep=True)
        return self.resource

    @staticmethod
    def _progress_from_payload(current: RunProgress, value: object) -> RunProgress:
        if isinstance(value, dict):
            completed = value.get("completed", value.get("coverage_count", current.completed))
            total = value.get("total", current.total)
            if isinstance(completed, int) and isinstance(total, int):
                return RunProgress(completed=completed, total=total)
        return current

    @staticmethod
    def _progress_from_checks(checks: object) -> RunProgress:
        if not isinstance(checks, tuple):
            return RunProgress()
        completed = sum(item.status in {"completed", "accepted"} for item in checks)
        return RunProgress(completed=completed, total=len(checks))

    @staticmethod
    def _upsert_agent(
        current: tuple[AgentView, ...], event: RunEvent, event_type: str
    ) -> tuple[AgentView, ...]:
        payload = event.payload
        raw = payload.get("agent_id", event.actor.id)
        agent_id = str(raw)
        role = event.actor.role or "agent"
        if isinstance(payload.get("agent"), dict):
            agent = cast(dict[str, JsonValue], payload["agent"])
            agent_id = str(agent.get("agent_id", agent_id))
            role = str(agent.get("role", role))
        status_map = {
            "agent.started": AgentStatus.RUNNING,
            "agent.completed": AgentStatus.COMPLETED,
            "agent.failed": AgentStatus.FAILED,
            "agent.cancelled": AgentStatus.CANCELLED,
        }
        status = status_map.get(event_type, AgentStatus.RUNNING)
        updated = AgentView(agent_id=agent_id, role=role, status=status, phase=event.stage)
        return tuple(updated if item.agent_id == agent_id else item for item in current) or (
            updated,
        )

    @staticmethod
    def _upsert_check(
        current: tuple[CheckView, ...], event: RunEvent, event_type: str
    ) -> tuple[CheckView, ...]:
        raw = event.check_id or event.payload.get("check_id")
        if raw is None and isinstance(event.payload.get("check"), dict):
            check = cast(dict[str, JsonValue], event.payload["check"])
            raw = check.get("check_id")
        if raw is None:
            return current
        check_id = str(raw)
        status = {
            "check.assigned": "assigned",
            "check.started": "running",
            "check.completed": "completed",
            "submission.accepted": "accepted",
        }.get(event_type, "pending")
        updated = CheckView(check_id=check_id, status=status, agent_id=event.actor.id)
        return tuple(updated if item.check_id == check_id else item for item in current) or (
            updated,
        )

    @staticmethod
    def _update_review(current: ReviewView, event: RunEvent, event_type: str) -> ReviewView:
        issue_count = current.issue_count + int(event_type in {"review.issue", "conflict.detected"})
        repair_count = current.repair_count + int(
            event_type in {"review.repair_requested", "repair.requested"}
        )
        round_value = current.round
        raw_round = event.payload.get("round")
        if isinstance(raw_round, int):
            round_value = max(round_value, raw_round)
        return ReviewView(
            round=round_value,
            status="completed" if event_type == "review.submitted" else "running",
            issue_count=issue_count,
            repair_count=repair_count,
        )


class RunEventPublisher:
    """Single sequence source for EventStore, projection and subscribers."""

    def __init__(
        self,
        event_store: EventStore,
        *,
        projection: RunProjection,
    ) -> None:
        self.event_store = event_store
        self.projection = projection
        self._lock = asyncio.Lock()
        self._sequencer = EventSequencer(
            request_id=projection.resource.request_id,
            run_id=projection.resource.run_id,
        )

    async def complete(
        self,
        result: DueDiligenceResult,
        *,
        persist: Callable[[RunResource], Awaitable[RunResource]],
    ) -> None:
        """Persist the final projection before any completion event is visible.

        This is ordered local persistence, not a distributed event-store transaction.
        Prepare with a separate sequencer so a failed save leaves no sequence gap.
        """
        async with self._lock:
            current = self.projection.resource
            sequence = await self.event_store.last_sequence(current.run_id)
            prepared = EventSequencer(request_id=current.request_id, run_id=current.run_id)
            prepared.restore(sequence)
            terminal = (
                LifecycleEventType.RUN_PARTIAL
                if result.meta.status is RunStatus.PARTIAL
                else LifecycleEventType.RUN_COMPLETED
            )
            events = (
                prepared.next(
                    LifecycleEventType.RUN_PHASE_COMPLETED,
                    {"stage": RunStage.REPORTING.value, "status": "completed"},
                    stage=RunStage.REPORTING,
                ),
                prepared.next(
                    EventType.REPORT_COMPLETED,
                    {"result": redact_json(result.model_dump(mode="json"))},
                    stage=RunStage.REPORTING,
                ),
                prepared.next(
                    terminal,
                    {"status": result.meta.status.value},
                    stage=RunStage.TERMINAL,
                ),
            )
            projected = RunProjection(current.model_copy(deep=True))
            for event in events:
                projected.apply(event)
            await persist(projected.resource)
            for event in events:
                await self.event_store.append(event)
                self.projection.apply(event)
                self._sequencer.restore(event.sequence)

    async def publish(
        self,
        event_type: EventType | LifecycleEventType | str,
        payload: dict[str, JsonValue],
        *,
        stage: RunStage | None = None,
        actor: ActorView | None = None,
        task_id: str | None = None,
        check_id: str | None = None,
    ) -> RunEvent:
        async with self._lock:
            persisted_sequence = await self.event_store.last_sequence(
                self.projection.resource.run_id
            )
            if persisted_sequence > self._sequencer.sequence:
                self._sequencer.restore(persisted_sequence)
            event = self._sequencer.next(
                event_type,
                cast(dict[str, JsonValue], redact_json(payload)),
                stage=stage or self.projection.resource.stage,
                actor=actor,
                task_id=task_id,
                check_id=check_id,
            )
            await self.event_store.append(event)
            self.projection.apply(event)
            return event


__all__ = [
    "CancellationToken",
    "EventStore",
    "InMemoryEventStore",
    "InMemoryRunRepository",
    "JsonRunRepository",
    "JsonlEventStore",
    "RunEventPublisher",
    "RunProjection",
    "RunRepository",
]
