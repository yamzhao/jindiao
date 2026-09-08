from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jindiao.application.execution_steps import ExecutionStepProjector
from jindiao.contracts.events import ExecutionEventType, LifecycleEventType
from jindiao.contracts.product import ProductResult
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import ExecutionProfile, RunResource, RunStage
from jindiao.observability.run_store import (
    InMemoryEventStore,
    InMemoryRunRepository,
    JsonlEventStore,
    RunEventPublisher,
    RunProjection,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
async def test_execution_snapshots_survive_pressure_and_persistent_replay(
    tmp_path: Path,
    durable: bool,
    execution_product: ProductResult,
) -> None:
    store = JsonlEventStore(tmp_path, max_queue=1) if durable else InMemoryEventStore(max_queue=1)
    publisher = RunEventPublisher(store, projection=RunProjection(resource()))
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI)
    plan = projector.start()
    await publisher.publish(plan.event_type, plan.payload)
    subscription = store.subscribe("run-1")
    assert (await anext(subscription)).event_type == ExecutionEventType.PLAN_CREATED
    for event in projector.complete(execution_product):
        await publisher.publish(event.event_type, event.payload)
    # Even a terminal snapshot may evict the only queued completion.
    await publisher.publish(LifecycleEventType.RUN_PARTIAL, {"status": "partial"})
    received = [await asyncio.wait_for(anext(subscription), 1) for _ in range(8)]
    assert [event.sequence for event in received] == list(range(2, 10))
    assert all(event.event_type == ExecutionEventType.STEP_COMPLETED for event in received[:-1])
    assert received[-1].event_type == LifecycleEventType.RUN_PARTIAL
    await subscription.aclose()  # type: ignore[attr-defined]
    restored = JsonlEventStore(tmp_path) if durable else store
    assert await restored.read_after("run-1", 1) == tuple(received)


@pytest.fixture
def execution_product() -> ProductResult:
    return ProductResult.model_validate_json(
        Path("docs/api/samples/product-result-full-input.json").read_text()
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
async def test_overall_progress_counts_unique_completed_product_steps(
    execution_product: ProductResult,
) -> None:
    projection = RunProjection(resource())
    publisher = RunEventPublisher(InMemoryEventStore(), projection=projection)
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI)
    plan = projector.start()
    await publisher.publish(plan.event_type, plan.payload)
    assert projection.resource.progress.total == 7
    assert projection.resource.progress.completed == 0
    completions = projector.complete(execution_product)
    first = completions[0]
    saved = await publisher.publish(first.event_type, first.payload)
    assert projection.resource.progress.completed == 1
    # A duplicated full snapshot, even at a new sequence, is an upsert, not +1.
    await publisher.publish(first.event_type, first.payload)
    projection.apply(saved)
    assert projection.resource.progress.completed == 1
    for event in completions[1:]:
        await publisher.publish(event.event_type, event.payload)
    await publisher.publish(LifecycleEventType.RUN_PARTIAL, {"status": "partial"})
    assert projection.resource.progress.completed == 7
    assert projection.resource.progress.percentage == 100
    assert projection.resource.status is RunStatus.PARTIAL


@pytest.mark.asyncio
async def test_failed_step_is_not_counted_as_successful_progress() -> None:
    projection = RunProjection(resource())
    publisher = RunEventPublisher(InMemoryEventStore(), projection=projection)
    projector = ExecutionStepProjector(mode=OrchestrationMode.MULTI)
    plan = projector.start()
    await publisher.publish(plan.event_type, plan.payload)
    from jindiao.orchestration.base import TeamRuntimeEvent

    for event in projector.observe(TeamRuntimeEvent(event_type="report.started")):
        await publisher.publish(event.event_type, event.payload)
    for event in projector.fail_active():
        await publisher.publish(event.event_type, event.payload)
    await publisher.publish("run.failed", {"error": {"code": "internal_error"}})
    assert projection.resource.progress.total == 7
    assert projection.resource.progress.completed == 0
    assert projection.resource.status is RunStatus.FAILED


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
