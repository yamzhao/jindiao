from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jindiao.contracts.events import EventSequencer, EventType
from jindiao.contracts.results import OrchestrationMode, RunStatus
from jindiao.contracts.runs import RunResource
from jindiao.observability.artifacts import AgentArtsSessionStore, RunArtifactStore
from jindiao.observability.run_store import JsonlEventStore, JsonRunRepository


def resource(*, status: RunStatus = RunStatus.ACCEPTED) -> RunResource:
    return RunResource(
        request_id="req-persistence",
        run_id="run-persistence",
        owner_id="owner-1",
        mode=OrchestrationMode.SINGLE,
        status=status,
        created_at=datetime.now(UTC),
        links={},
    )


@pytest.mark.asyncio
async def test_json_repository_recovers_running_runs_after_restart(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    first = JsonRunRepository(root)
    await first.create(resource())
    await first.save(resource(status=RunStatus.RUNNING))

    restarted = JsonRunRepository(root)
    recovered = await restarted.recover_running()

    assert len(recovered) == 1
    assert recovered[0].status is RunStatus.FAILED
    assert recovered[0].termination is not None
    assert recovered[0].termination.reason.value == "interrupted"
    current = await restarted.get("run-persistence")
    assert current is not None
    assert current.status is RunStatus.FAILED


@pytest.mark.asyncio
async def test_jsonl_event_store_is_readable_by_a_second_instance(tmp_path: Path) -> None:
    first = JsonlEventStore(tmp_path / "events")
    event = EventSequencer(request_id="req-1", run_id="run-1").next(
        EventType.RUN_ACCEPTED,
        {"status": "accepted"},
    )
    await first.append(event)

    second = JsonlEventStore(tmp_path / "events")
    loaded = await second.read_after("run-1")
    assert loaded == (event,)


def test_manifest_validation_rejects_interrupted_or_tampered_artifacts(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path)
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    (run_root / "result.json").write_text('{"partial": true}\n', encoding="utf-8")
    assert store.load_result("run-1") is None

    RunArtifactStore._write_manifest(run_root)
    assert store.verify_manifest("run-1") is True
    (run_root / "result.json").write_text('{"partial": false}\n', encoding="utf-8")
    assert store.verify_manifest("run-1") is False


def test_agentarts_store_requires_explicit_shared_storage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shared root"):
        AgentArtsSessionStore(tmp_path, shared=False)
