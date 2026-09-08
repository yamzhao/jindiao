# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_reporting_demo_store import feedback
from test_reporting_replay import source_snapshot

from jindiao.application.run_coordinator import RunCoordinator
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.product import ProductResult
from jindiao.contracts.results import (
    DueDiligenceRequest,
    SkillEvolutionFeedback,
)
from jindiao.contracts.runs import RunCreateRequest
from jindiao.observability.artifacts import RunArtifactStore
from jindiao.observability.run_store import JsonRunRepository
from jindiao.scenarios import ScenarioRepository


def make_service(root: Path, store: RunArtifactStore | None = None) -> DueDiligenceService:
    return DueDiligenceService(
        settings=Settings(  # type: ignore[call-arg]
            _env_file=None,
            model_provider="offline_mock",
            model_name="deterministic-mock",
            data_source_mode="mock",
            artifact_root=root,
            reporting_demo_enabled=True,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        artifact_store=store,
    )


def request() -> RunCreateRequest:
    return RunCreateRequest(
        customerName="乐视网信息技术（北京）股份有限公司",
        scenario_id="normal-enterprise",
    )


@pytest.mark.asyncio
async def test_accepted_run_and_idempotent_retry_keep_old_binding(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    coordinator = RunCoordinator(service)
    first = await coordinator.create(request(), owner_id="alice", idempotency_key="old")
    store = service.reporting_demo_store
    assert store is not None
    candidate, _ = store.propose(source_snapshot(), feedback(), key="one", owner="a", session=None)
    store.apply(candidate.evolution_id, reason="确认")
    repeated = await coordinator.create(request(), owner_id="alice", idempotency_key="old")
    second = await coordinator.create(request(), owner_id="alice", idempotency_key="new")
    assert repeated.run_id == first.run_id
    assert repeated.reporting_policy == first.reporting_policy
    assert first.reporting_policy is not None and second.reporting_policy is not None
    assert first.reporting_policy.version == "1.1.0"
    assert second.reporting_policy.version == candidate.candidate.version
    await coordinator.execute(first.run_id)
    result = await coordinator.get_result(first.run_id, owner_id="alice")
    assert result is not None
    artifacts = RunArtifactStore(tmp_path)
    assert artifacts.verify_manifest(first.run_id)
    replay = artifacts.load_replay(first.run_id)
    assert replay is not None and replay.binding == first.reporting_policy
    assert replay.binding.version == "1.1.0"
    stored = artifacts.load_result(first.run_id)
    assert stored is not None and stored.meta == result.meta
    events = await coordinator.event_store.read_after(first.run_id, 0)
    completed = next(event for event in events if str(event.event_type) == "report.completed")
    assert ProductResult.model_validate(completed.payload["result"]).meta == result.meta


@pytest.mark.asyncio
async def test_direct_service_saves_replay_and_rejects_old_feedback(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    result = await service.run(
        DueDiligenceRequest(
            enterprise=request().to_execution_request().enterprise,
            scenario_id="normal-enterprise",
            skill_feedback=SkillEvolutionFeedback(
                source="user", reference="old", text="请就近披露", evidence_refs=("old",)
            ),
        )
    )
    assert not hasattr(result, "skill_evolution")
    data = json.loads((tmp_path / result.meta.run_id / "manifest.json").read_text())
    assert "report-replay.json" in data["files"]


class BrokenReplayStore(RunArtifactStore):
    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        if path.name == "report-replay.json":
            raise OSError("simulated disk error")
        RunArtifactStore._write_json(path, value)


@pytest.mark.asyncio
async def test_replay_write_failure_does_not_fail_main_result(tmp_path: Path) -> None:
    store = BrokenReplayStore(tmp_path)
    result = await make_service(tmp_path, store).run(request().to_execution_request())
    assert not hasattr(result.meta, "report_replay_available")
    assert store.load_replay(result.meta.run_id) is None
    assert store.verify_manifest(result.meta.run_id)
    saved = store.load_result(result.meta.run_id)
    assert saved is not None and saved.meta == result.meta


@pytest.mark.asyncio
async def test_inflight_direct_stream_keeps_entry_binding(tmp_path: Path) -> None:
    from jindiao.contracts.events import EventType

    service = make_service(tmp_path)
    stream = service.stream(request().to_execution_request())
    accepted = await anext(stream)
    assert accepted.event_type is EventType.RUN_ACCEPTED
    store = service.reporting_demo_store
    assert store is not None
    candidate, _ = store.propose(source_snapshot(), feedback(), key="one", owner="a", session=None)
    store.apply(candidate.evolution_id, reason="在途应用")
    events = [event async for event in stream]
    completed = next(event for event in events if event.event_type is EventType.REPORT_COMPLETED)
    result = ProductResult.model_validate(completed.payload["result"])
    first_snapshot = service.load_report_replay(result.meta.run_id)
    assert first_snapshot is not None and first_snapshot.binding.version == "1.1.0"
    next_result = await service.run(request().to_execution_request())
    next_snapshot = service.load_report_replay(next_result.meta.run_id)
    assert next_snapshot is not None
    assert next_snapshot.binding.version == candidate.candidate.version


@pytest.mark.asyncio
async def test_primary_artifact_failure_never_publishes_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunArtifactStore(tmp_path)
    write = store._write_json

    def fail_result(path: Path, value: object) -> None:
        if path.name == "result.json":
            raise OSError("simulated primary artifact failure")
        write(path, value)

    monkeypatch.setattr(store, "_write_json", fail_result)
    coordinator = RunCoordinator(make_service(tmp_path, store))
    run = await coordinator.create(request())
    await coordinator.execute(run.run_id)
    assert (await coordinator.get(run.run_id)).status.value == "failed"
    assert await coordinator.get_result(run.run_id) is None
    events = await coordinator.event_store.read_after(run.run_id, 0)
    assert all(str(event.event_type) != "report.completed" for event in events)


@pytest.mark.asyncio
async def test_repository_write_failure_never_returns_cached_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = JsonRunRepository(tmp_path / "runs")
    coordinator = RunCoordinator(make_service(tmp_path / "artifacts"), repository=repository)
    run = await coordinator.create(request(), owner_id="alice")
    write = repository._write

    def fail_result(path: Path, value: object) -> None:
        if path.name == "result.json":
            raise OSError("simulated result persistence failure")
        write(path, value)

    monkeypatch.setattr(repository, "_write", fail_result)
    await coordinator.execute(run.run_id)
    current = await coordinator.get(run.run_id, owner_id="alice")
    assert current.status.value == "failed"
    assert not current.result_available
    assert await coordinator.get_result(run.run_id, owner_id="alice") is None
    assert await repository.get_result(run.run_id) is None
    events = await coordinator.event_store.read_after(run.run_id, 0)
    assert all(str(event.event_type) != "report.completed" for event in events)


@pytest.mark.asyncio
async def test_snapshot_disk_size_limit_controls_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jindiao.observability import artifacts
    from jindiao.reporting import replay

    # The limit applies to serialized disk bytes, not the smaller compact model JSON.
    service = make_service(tmp_path / "source")
    result = await service.run(request().to_execution_request())
    snapshot = service.load_report_replay(result.meta.run_id)
    assert snapshot is not None
    compact_size = len(snapshot.model_dump_json().encode())
    monkeypatch.setattr(replay, "MAX_SNAPSHOT_BYTES", compact_size + 1)
    monkeypatch.setattr(artifacts, "MAX_SNAPSHOT_BYTES", compact_size + 1)
    store = RunArtifactStore(tmp_path / "limited")
    saved = store.complete(result, metrics={}, replay_view=snapshot.view, binding=snapshot.binding)
    assert saved == result
    assert store.load_replay(result.meta.run_id) is None
    assert store.verify_manifest(result.meta.run_id)


@pytest.mark.asyncio
async def test_terminal_run_save_precedes_completion_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jindiao.contracts.runs import RunResource

    repository = JsonRunRepository(tmp_path / "runs")
    coordinator = RunCoordinator(make_service(tmp_path / "artifacts"), repository=repository)
    run = await coordinator.create(request(), owner_id="alice")
    save = repository.save

    async def fail_completed(resource: RunResource) -> RunResource:
        if resource.status.value in {"completed", "partial"}:
            raise OSError("simulated terminal state failure")
        return await save(resource)

    monkeypatch.setattr(repository, "save", fail_completed)
    await coordinator.execute(run.run_id)
    assert (await coordinator.get(run.run_id, owner_id="alice")).status.value == "failed"
    assert await coordinator.get_result(run.run_id, owner_id="alice") is None
    events = await coordinator.event_store.read_after(run.run_id, 0)
    assert not {"report.completed", "run.completed", "run.partial"} & {
        str(event.event_type) for event in events
    }
