# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jindiao.application.run_coordinator import RunCoordinator
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.events import EventType, ExecutionEventType
from jindiao.contracts.execution_steps import ExecutionStepSnapshot
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import RunCreateRequest
from jindiao.investigation.catalog import CHECK_CATALOG
from jindiao.observability.run_store import JsonlEventStore, JsonRunRepository
from jindiao.orchestration.base import TeamRuntimeEvent
from jindiao.scenarios import ScenarioRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_execution_starts_live_then_fails_or_cancels_without_private_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    instance = coordinator(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def run(*args: Any, **kwargs: Any) -> Any:
        sink = kwargs["event_sink"]
        await sink(
            TeamRuntimeEvent(
                event_type="agent.started",
                member_name="real-agent",
                payload={"task_ids": [f"check:{CHECK_CATALOG.check_ids[0]}"]},
            )
        )
        await sink(
            TeamRuntimeEvent(
                event_type="agent.output",
                member_name="real-agent",
                payload={"content": "private scratchpad marker"},
            )
        )
        started.set()
        await release.wait()
        raise RuntimeError("private exception marker")

    monkeypatch.setattr(instance.service, "_run_impl", run)
    resource = await instance.create(create_request())
    task = instance.start(resource.run_id)
    await asyncio.wait_for(started.wait(), 2)
    before = await instance.event_store.read_after(resource.run_id)
    assert any(e.event_type == ExecutionEventType.STEP_STARTED for e in before)
    assert not any(e.event_type == ExecutionEventType.STEP_COMPLETED for e in before)
    if cancel:
        await instance.cancel(resource.run_id)
    else:
        release.set()
        await task
    events = await instance.event_store.read_after(resource.run_id)
    assert events[-1].event_type == ("run.cancelled" if cancel else "run.failed")
    assert "private scratchpad marker" not in "".join(e.model_dump_json() for e in events)
    failed = [e for e in events if e.event_type == ExecutionEventType.STEP_FAILED]
    if cancel:
        assert not failed  # UI derives cancelled from the authoritative Run event.
    else:
        active_ids = {
            ExecutionStepSnapshot.model_validate(e.payload["step"]).step_id
            for e in before
            if e.event_type == ExecutionEventType.STEP_STARTED
        }
        assert {
            ExecutionStepSnapshot.model_validate(e.payload["step"]).step_id for e in failed
        } == active_ids
        assert "private exception marker" not in "".join(e.model_dump_json() for e in failed)


@pytest.mark.asyncio
async def test_v1_stream_filters_execution_events_and_keeps_contiguous_legacy_sequence(
    tmp_path: Path,
) -> None:
    instance = coordinator(tmp_path)
    events = [
        e
        async for e in instance.stream_compat(
            create_request().to_execution_request(), mode=OrchestrationMode.SINGLE
        )
    ]
    assert events
    assert all(isinstance(e.event_type, EventType) for e in events)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert events[-1].event_type == EventType.REPORT_COMPLETED


def coordinator(tmp_path: Path) -> RunCoordinator:
    service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
    )
    return RunCoordinator(service)


def create_request() -> RunCreateRequest:
    return RunCreateRequest(
        customerName="乐视网信息技术（北京）股份有限公司",
        scenario_id="normal-enterprise",
    )


@pytest.mark.asyncio
async def test_coordinator_create_is_idempotent() -> None:
    coordinator_instance = coordinator(Path("/tmp/jindiao-coordinator-test"))
    first = await coordinator_instance.create(
        create_request(), owner_id="user-1", idempotency_key="k"
    )
    second = await coordinator_instance.create(
        create_request(), owner_id="user-1", idempotency_key="k"
    )
    assert first.run_id == second.run_id
    assert first.status is RunStatus.ACCEPTED


@pytest.mark.asyncio
async def test_coordinator_execute_persists_result_and_projection() -> None:
    coordinator_instance = coordinator(Path("/tmp/jindiao-coordinator-test-2"))
    resource = await coordinator_instance.create(create_request(), owner_id="user-1")
    await coordinator_instance.execute(resource.run_id)
    current = await coordinator_instance.get(resource.run_id, owner_id="user-1")
    assert current.status is RunStatus.PARTIAL
    assert current.result_available
    assert current.progress.completed == current.progress.total == 7
    assert current.progress.percentage == 100
    result = await coordinator_instance.get_result(resource.run_id, owner_id="user-1")
    assert result is not None
    assert result.meta.run_id == resource.run_id


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_terminal_run() -> None:
    coordinator_instance = coordinator(Path("/tmp/jindiao-coordinator-test-3"))
    resource = await coordinator_instance.create(create_request(), owner_id="user-1")
    await coordinator_instance.execute(resource.run_id)
    cancelled = await coordinator_instance.cancel(resource.run_id, owner_id="user-1")
    assert cancelled.status is RunStatus.PARTIAL


@pytest.mark.asyncio
async def test_cancel_before_task_is_scheduled_reaches_terminal_state() -> None:
    coordinator_instance = coordinator(Path("/tmp/jindiao-coordinator-cancel-race"))
    resource = await coordinator_instance.create(create_request(), owner_id="user-1")
    coordinator_instance.start(resource.run_id)
    cancelled = await coordinator_instance.cancel(resource.run_id, owner_id="user-1")
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.termination is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_creation", [False, True])
async def test_completed_run_survives_restart_without_replaying_snapshot_events(
    tmp_path: Path, retry_creation: bool
) -> None:
    service = coordinator(tmp_path).service

    def new_coordinator() -> RunCoordinator:
        return RunCoordinator(
            service,
            repository=JsonRunRepository(tmp_path / "runs"),
            event_store=JsonlEventStore(tmp_path / "events"),
        )

    request = RunCreateRequest(
        customerName="金调双源制造有限公司",
        scenario_id="evidence-conflict",
    )
    original = new_coordinator()
    resource = await original.create(request, owner_id="owner", idempotency_key="restart")
    await original.execute(resource.run_id)
    before = await original.get(resource.run_id, owner_id="owner")
    assert before.review.repair_count > 0
    restored = new_coordinator()
    if retry_creation:
        repeated = await restored.create(request, owner_id="owner", idempotency_key="restart")
        assert repeated.run_id == resource.run_id
    after = await restored.get(resource.run_id, owner_id="owner")
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    assert await restored.event_store.last_sequence(resource.run_id) == before.latest_sequence


@pytest.mark.asyncio
async def test_old_zero_progress_metadata_is_rebuilt_from_steps_without_rewriting_history(
    tmp_path: Path,
) -> None:
    service = coordinator(tmp_path).service

    def instance() -> RunCoordinator:
        return RunCoordinator(
            service,
            repository=JsonRunRepository(tmp_path / "runs"),
            event_store=JsonlEventStore(tmp_path / "events"),
        )

    original = instance()
    item = await original.create(create_request(), owner_id="owner")
    await original.execute(item.run_id)
    metadata = tmp_path / "runs" / item.run_id / "metadata.json"
    legacy = json.loads(metadata.read_text())
    legacy["progress"] = {"completed": 0, "total": 0, "percentage": 0.0}
    metadata.write_text(json.dumps(legacy))

    def saved_files() -> dict[str, bytes]:
        return {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    saved = await asyncio.to_thread(saved_files)
    restored = instance()
    states = await asyncio.gather(*(restored.get(item.run_id, owner_id="owner") for _ in range(8)))
    for state in states:
        assert state.progress.completed == state.progress.total == 7
        assert state.progress.percentage == 100
        assert state.reporting.model_dump(mode="json") == legacy["reporting"]
        assert state.latest_sequence == legacy["latest_sequence"]
    assert await asyncio.to_thread(saved_files) == saved
