from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from jindiao.contracts.events import LifecycleEventType
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import ExecutionProfile, RunResource, RunStage
from jindiao.observability.run_store import (
    InMemoryEventStore,
    InMemoryRunRepository,
    RunEventPublisher,
    RunProjection,
)


def resource() -> RunResource:
    return RunResource(
        request_id="req-1",
        run_id="run-1",
        owner_id="u-1",
        mode=OrchestrationMode.MULTI,
        profile=ExecutionProfile.ATTACHED,
        created_at=datetime.now(UTC),
        links={},
    )


@pytest.mark.asyncio
async def test_in_memory_event_store_allocates_sequences_and_replays_after_cursor() -> None:
    store = InMemoryEventStore()
    publisher = RunEventPublisher(store, projection=RunProjection(resource()))
    first = await publisher.publish(LifecycleEventType.RUN_STARTED, {"status": "running"})
    second = await publisher.publish(
        LifecycleEventType.RUN_PHASE_STARTED,
        {"stage": "investigation"},
        stage=RunStage.INVESTIGATION,
    )
    assert first.sequence == 1
    assert second.event_id == "run-1:2"
    assert [item.sequence for item in await store.read_after("run-1", 1)] == [2]


@pytest.mark.asyncio
async def test_publisher_updates_projection_and_subscriber_receives_event() -> None:
    store = InMemoryEventStore()
    projection = RunProjection(resource())
    publisher = RunEventPublisher(store, projection=projection)
    subscription = store.subscribe("run-1", after=0)
    await publisher.publish(LifecycleEventType.RUN_STARTED, {"status": "running"})
    received = await asyncio.wait_for(anext(subscription), timeout=1)
    assert received.sequence == 1
    assert projection.resource.status is RunStatus.RUNNING
    close = getattr(subscription, "aclose", None)
    assert callable(close)
    await close()


@pytest.mark.asyncio
async def test_repository_idempotency_returns_same_resource_and_rejects_conflict() -> None:
    repository = InMemoryRunRepository()
    item, created = await repository.create(resource(), idempotency_key="key", request_hash="a")
    same, created_again = await repository.create(
        resource(), idempotency_key="key", request_hash="a"
    )
    assert created and not created_again
    assert same.run_id == item.run_id
    with pytest.raises(ValueError, match="idempotency"):
        await repository.create(
            resource().model_copy(update={"run_id": "run-2"}),
            idempotency_key="key",
            request_hash="b",
        )


@pytest.mark.asyncio
async def test_event_store_rejects_second_terminal_event() -> None:
    store = InMemoryEventStore()
    publisher = RunEventPublisher(store, projection=RunProjection(resource()))
    await publisher.publish(LifecycleEventType.RUN_STARTED, {"status": "running"})
    await publisher.publish(LifecycleEventType.RUN_COMPLETED, {"status": "completed"})
    with pytest.raises(ValueError, match="terminal"):
        await publisher.publish("run.failed", {"error": {"code": "x"}})
