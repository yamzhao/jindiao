"""Unified Run lifecycle facade for JSON, SSE and AgentArts adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.events import EventSequencer, EventType, LifecycleEventType, RunEvent
from jindiao.contracts.results import DueDiligenceResult, RunStatus
from jindiao.contracts.runs import (
    ExecutionProfile,
    ExecutionProfileConfig,
    RunCreateRequest,
    RunLink,
    RunResource,
    RunStage,
)
from jindiao.observability.run_store import (
    EventStore,
    InMemoryEventStore,
    InMemoryRunRepository,
    RunEventPublisher,
    RunProjection,
    RunRepository,
)
from jindiao.orchestration.base import TeamRuntimeEvent

from .errors import (
    DetachedUnavailableError,
    IdempotencyConflictError,
    RunNotFoundApplicationError,
    error_to_record,
)
from .service import DueDiligenceService


class RunNotFoundError(RunNotFoundApplicationError):
    pass


class RunConflictError(IdempotencyConflictError):
    pass


class RunCancellationToken:
    def __init__(self) -> None:
        self.cancelled = False

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()


class RunCoordinator:
    """Own Run identity, idempotency, execution and public event publication."""

    def __init__(
        self,
        service: DueDiligenceService,
        *,
        repository: RunRepository | None = None,
        event_store: EventStore | None = None,
        profile_config: ExecutionProfileConfig | None = None,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.service = service
        self.repository = repository or InMemoryRunRepository()
        self.event_store = event_store or InMemoryEventStore()
        self.profile_config = profile_config or ExecutionProfileConfig()
        self.id_factory = id_factory
        self.clock = clock
        self._projections: dict[str, RunProjection] = {}
        self._publishers: dict[str, RunEventPublisher] = {}
        self._results: dict[str, DueDiligenceResult] = {}
        self._requests: dict[str, RunCreateRequest] = {}
        self._tokens: dict[str, RunCancellationToken] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._exceptions: dict[str, Exception] = {}
        self._mapper: Any | None = None
        self._recovery_lock = asyncio.Lock()
        self._recovered = False

    async def create(
        self,
        request: RunCreateRequest,
        *,
        owner_id: str = "anonymous",
        idempotency_key: str | None = None,
        session_id: str | None = None,
    ) -> RunResource:
        await self.recover()
        profile = request.execution_profile
        if profile is ExecutionProfile.DETACHED and self.profile_config.profile is not profile:
            raise DetachedUnavailableError("detached execution profile is not enabled")
        request_hash = hashlib.sha256(
            json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        request_id = self.id_factory()
        run_id = self.id_factory()
        now = self.clock()
        effective_session = session_id or request.session_id
        resource = RunResource(
            request_id=request_id,
            run_id=run_id,
            owner_id=owner_id,
            mode=request.mode,
            status=RunStatus.ACCEPTED,
            stage=RunStage.ACCEPTED,
            profile=profile,
            session_id=effective_session,
            created_at=now,
            links=self._links(run_id),
            metadata={"request_hash": request_hash},
            reporting_policy=self.service.freeze_reporting_policy(),
        )
        try:
            stored, created = await self.repository.create(
                resource,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        except ValueError as error:
            raise RunConflictError(str(error)) from error
        if not created:
            # A persisted idempotency receipt must not emit a new accepted event
            # or reset a completed projection when the process has restarted.
            existing = await self.get(
                stored.run_id, owner_id=owner_id, session_id=effective_session
            )
            self._requests.setdefault(stored.run_id, request)
            return existing
        if stored.run_id not in self._projections:
            self._requests[stored.run_id] = request
            self._projections[stored.run_id] = RunProjection(stored)
            self._publishers[stored.run_id] = RunEventPublisher(
                self.event_store,
                projection=self._projections[stored.run_id],
            )
            self._tokens[stored.run_id] = RunCancellationToken()
            await self._publishers[stored.run_id].publish(
                EventType.RUN_ACCEPTED,
                {"status": "accepted"},
                stage=RunStage.ACCEPTED,
            )
        return stored

    def start(self, run_id: str) -> asyncio.Task[None]:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self.execute(run_id), name=f"jindiao-run-{run_id}")
        self._tasks[run_id] = task
        return task

    async def execute(self, run_id: str) -> None:
        await self.recover()
        await self._require_resource(run_id)
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            latest = await self.repository.get(run_id)
            if latest is None:
                raise RunNotFoundError("Run not found")
            if latest.status in {
                RunStatus.COMPLETED,
                RunStatus.PARTIAL,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return
            publisher = self._publisher_for(latest)
            token = self._tokens.setdefault(run_id, RunCancellationToken())
            await publisher.publish(
                LifecycleEventType.RUN_STARTED,
                {"status": "running"},
                stage=RunStage.ACCEPTED,
            )
            await publisher.publish(
                LifecycleEventType.RUN_PHASE_STARTED,
                {"stage": RunStage.ACQUISITION.value, "status": "running"},
                stage=RunStage.ACQUISITION,
            )
            try:
                result = await self.service._run_impl(
                    self._request_for_resource(latest),
                    mode=latest.mode,
                    request_id=latest.request_id,
                    run_id=latest.run_id,
                    event_sink=self._runtime_sink(run_id),
                    cancellation_token=token,
                    reporting_policy=latest.reporting_policy,
                )
                token.raise_if_cancelled()
                save_result = getattr(self.repository, "save_result", None)
                if save_result is not None:
                    await save_result(run_id, result)
                await publisher.complete(result, persist=self.repository.save)
                self._results[run_id] = result
            except asyncio.CancelledError:
                token.cancelled = True
                await publisher.publish(
                    LifecycleEventType.RUN_CANCELLED,
                    {"status": "cancelled", "reason": "cancel_requested"},
                    stage=RunStage.TERMINAL,
                )
                await self.repository.save(self._projection(run_id).resource)
            except Exception as error:
                self._exceptions[run_id] = error
                record = error_to_record(error)
                await publisher.publish(
                    "run.failed",
                    {"error": record.model_dump(mode="json")},
                    stage=RunStage.TERMINAL,
                )
                self._projection(run_id).resource = self._projection(run_id).resource.model_copy(
                    update={"error": record}
                )
                save_error = getattr(self.repository, "save_error", None)
                if save_error is not None:
                    await save_error(run_id, record)
                await self.repository.save(self._projection(run_id).resource)

    async def execute_compat(
        self,
        request: Any,
        *,
        mode: Any,
        request_id: str | None = None,
        run_id: str | None = None,
        event_sink: Callable[[Any], Any] | None = None,
        cancellation_token: object | None = None,
    ) -> DueDiligenceResult:
        """Execute a legacy request through the canonical Run lifecycle.

        ``request_id`` and ``run_id`` are accepted for compatibility with the
        service implementation; identity is allocated by ``create`` so the
        repository remains the source of truth.
        """

        del request_id, run_id, event_sink
        if cancellation_token is not None and getattr(cancellation_token, "cancelled", False):
            raise asyncio.CancelledError()
        from jindiao.contracts.runs import RunCreateRequest

        payload = request.model_dump(mode="json")
        payload["mode"] = mode.value if hasattr(mode, "value") else mode
        resource = await self.create(RunCreateRequest.model_validate(payload))
        task = self.start(resource.run_id)
        await task
        error = self._exceptions.get(resource.run_id)
        if error is not None:
            raise error
        result = await self.get_result(resource.run_id)
        if result is None:
            raise RuntimeError("Run completed without a result")
        return result

    async def stream_compat(
        self,
        request: Any,
        *,
        mode: Any,
    ) -> AsyncIterator[RunEvent]:
        """Yield the historical v1 event vocabulary from canonical Run events."""

        from jindiao.contracts.runs import RunCreateRequest

        payload = request.model_dump(mode="json")
        payload["mode"] = mode.value if hasattr(mode, "value") else mode
        resource = await self.create(RunCreateRequest.model_validate(payload))
        self.start(resource.run_id)
        sequencer = EventSequencer(request_id=resource.request_id, run_id=resource.run_id)
        legacy_types = set(EventType)
        async for event in self.subscribe(resource.run_id):
            if not isinstance(event.event_type, EventType) or event.event_type not in legacy_types:
                continue
            yield sequencer.next(
                event.event_type,
                event.payload,
                occurred_at=event.occurred_at,
                stage=event.stage,
                actor=event.actor,
                task_id=event.task_id,
                check_id=event.check_id,
            )

    async def get(
        self,
        run_id: str,
        *,
        owner_id: str = "anonymous",
        admin: bool = False,
        session_id: str | None = None,
    ) -> RunResource:
        await self.recover()
        resource = await self.repository.get(run_id)
        if resource is None or (resource.owner_id != owner_id and not admin):
            raise RunNotFoundError("Run not found")
        if resource.session_id is not None and resource.session_id != session_id and not admin:
            raise RunNotFoundError("Run not found")
        await self._hydrate_projection(run_id, resource)
        projection = self._projections.get(run_id)
        return projection.resource if projection is not None else resource

    async def get_result(
        self,
        run_id: str,
        *,
        owner_id: str = "anonymous",
        admin: bool = False,
        session_id: str | None = None,
    ) -> DueDiligenceResult | None:
        resource = await self.get(run_id, owner_id=owner_id, admin=admin, session_id=session_id)
        if not resource.result_available or resource.status not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
        }:
            return None
        result = self._results.get(run_id)
        if result is not None:
            return result
        load_result = getattr(self.repository, "get_result", None)
        if callable(load_result):
            loaded = await load_result(run_id)
            if loaded is None or isinstance(loaded, DueDiligenceResult):
                return loaded
        return None

    async def cancel(
        self,
        run_id: str,
        *,
        owner_id: str = "anonymous",
        admin: bool = False,
        session_id: str | None = None,
    ) -> RunResource:
        resource = await self.get(run_id, owner_id=owner_id, admin=admin, session_id=session_id)
        if resource.status in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return resource
        publisher = self._publisher_for(resource)
        self._tokens.setdefault(run_id, RunCancellationToken()).cancelled = True
        await publisher.publish(
            LifecycleEventType.RUN_CANCEL_REQUESTED,
            {"status": "cancellation_requested"},
            stage=resource.stage,
        )
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        current = await self.repository.get(run_id)
        if current is not None and current.status not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            # A task cancelled before its coroutine gets scheduled has no chance
            # to execute the ``CancelledError`` handler in ``execute``.
            await publisher.publish(
                LifecycleEventType.RUN_CANCELLED,
                {"status": "cancelled", "reason": "cancel_requested"},
                stage=RunStage.TERMINAL,
            )
            await self.repository.save(self._projection(run_id).resource)
            current = await self.repository.get(run_id)
        return current or self._projection(run_id).resource

    async def subscribe(
        self,
        run_id: str,
        *,
        owner_id: str = "anonymous",
        admin: bool = False,
        session_id: str | None = None,
        after: int = 0,
    ) -> AsyncIterator[Any]:
        await self.get(run_id, owner_id=owner_id, admin=admin, session_id=session_id)
        terminal_events = {
            LifecycleEventType.RUN_COMPLETED,
            LifecycleEventType.RUN_PARTIAL,
            "run.failed",
            LifecycleEventType.RUN_CANCELLED,
        }
        last_sequence = await self.event_store.last_sequence(run_id)
        if after >= last_sequence:
            tail = await self.event_store.read_after(run_id, max(0, last_sequence - 1))
            if tail and tail[-1].event_type in terminal_events:
                return
        async for event in self.event_store.subscribe(run_id, after=after):
            yield event
            if event.event_type in terminal_events:
                break

    def active_runs(self, *, profile: ExecutionProfile | None = None) -> int:
        return sum(
            not task.done()
            for run_id, task in self._tasks.items()
            if task is not None
            and (
                profile is None
                or (
                    run_id in self._projections
                    and self._projections[run_id].resource.profile is profile
                )
            )
        )

    async def recover(self) -> None:
        """Apply explicit interrupted semantics to runs left running on restart."""

        if self._recovered:
            return
        async with self._recovery_lock:
            if self._recovered:
                return
            recover_running = getattr(self.repository, "recover_running", None)
            if callable(recover_running):
                recovered = await recover_running()
                for resource in recovered:
                    publisher = self._publisher_for(resource)
                    await self._hydrate_projection(resource.run_id, resource)
                    if await self.event_store.last_sequence(resource.run_id) == 0:
                        await publisher.publish(EventType.RUN_ACCEPTED, {"status": "accepted"})
                    await publisher.publish(
                        EventType.RUN_FAILED,
                        {
                            "error": {
                                "code": "interrupted",
                                "message": "Run interrupted by service restart",
                            }
                        },
                        stage=RunStage.TERMINAL,
                    )
                    await self.repository.save(self._projection(resource.run_id).resource)
            self._recovered = True

    async def _hydrate_projection(self, run_id: str, resource: RunResource) -> None:
        if run_id in self._projections and self._projections[run_id].resource.latest_sequence:
            return
        self._publisher_for(resource)
        projection = self._projections[run_id]
        # The durable resource is already folded through latest_sequence.
        # Reapplying older events would double-count repair/report progress.
        events = await self.event_store.read_after(run_id, projection.resource.latest_sequence)
        for event in events:
            projection.apply(event)

    def _runtime_sink(self, run_id: str) -> Callable[[TeamRuntimeEvent], Any]:
        async def sink(event: TeamRuntimeEvent) -> None:
            resource = self._projection(run_id).resource
            if self._mapper is None:
                from jindiao.api.event_mapper import EventMapper

                self._mapper = EventMapper()
            public = self._mapper.map(
                event,
                EventSequencer(request_id=resource.request_id, run_id=resource.run_id),
            )
            if public is None:
                return
            # EventMapper historically accepted a sequencer. Re-publish through the
            # canonical publisher so the store owns the only externally visible sequence.
            publisher = self._publisher_for(self._projection(run_id).resource)
            await publisher.publish(
                public.event_type,
                public.payload,
                stage=public.stage,
                actor=public.actor,
                task_id=public.task_id,
                check_id=public.check_id,
            )

        return sink

    def _projection(self, run_id: str) -> RunProjection:
        return self._projections[run_id]

    def _publisher_for(self, resource: RunResource) -> RunEventPublisher:
        if resource.run_id not in self._projections:
            self._projections[resource.run_id] = RunProjection(resource)
            self._publishers[resource.run_id] = RunEventPublisher(
                self.event_store,
                projection=self._projections[resource.run_id],
            )
        return self._publishers[resource.run_id]

    async def _require_resource(self, run_id: str) -> RunResource:
        resource = await self.repository.get(run_id)
        if resource is None:
            raise RunNotFoundError("Run not found")
        self._publisher_for(resource)
        return resource

    def _request_for_resource(self, resource: RunResource) -> RunCreateRequest:
        existing = self._requests.get(resource.run_id)
        if existing is not None:
            return existing
        raw = resource.metadata.get("request")
        if isinstance(raw, dict):
            return RunCreateRequest.model_validate(raw)
        # Metadata stores the canonical request at creation time in newer callers;
        # this fallback keeps repository records created by older code executable.
        return RunCreateRequest(
            enterprise=EnterpriseInput(company_name=str(resource.metadata.get("company_name", ""))),
            mode=resource.mode,
            execution_profile=resource.profile,
        )

    @staticmethod
    def _links(run_id: str) -> dict[str, RunLink]:
        base = f"/api/v2/due-diligence/runs/{run_id}"
        return {
            "self": RunLink(href=base),
            "events": RunLink(href=f"{base}/events"),
            "result": RunLink(href=f"{base}/result"),
            "cancel": RunLink(href=f"{base}/cancel", method="POST"),
        }


__all__ = ["RunCancellationToken", "RunConflictError", "RunCoordinator", "RunNotFoundError"]
