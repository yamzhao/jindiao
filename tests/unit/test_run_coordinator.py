from __future__ import annotations

from pathlib import Path

import pytest

from jindiao.application.run_coordinator import RunCoordinator
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput
from jindiao.contracts.results import RunStatus
from jindiao.contracts.runs import RunCreateRequest
from jindiao.observability.run_store import JsonlEventStore, JsonRunRepository
from jindiao.scenarios import ScenarioRepository


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
        enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
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
    assert current.status is RunStatus.COMPLETED
    assert current.result_available
    result = await coordinator_instance.get_result(resource.run_id, owner_id="user-1")
    assert result is not None
    assert result.meta.run_id == resource.run_id


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_terminal_run() -> None:
    coordinator_instance = coordinator(Path("/tmp/jindiao-coordinator-test-3"))
    resource = await coordinator_instance.create(create_request(), owner_id="user-1")
    await coordinator_instance.execute(resource.run_id)
    cancelled = await coordinator_instance.cancel(resource.run_id, owner_id="user-1")
    assert cancelled.status is RunStatus.COMPLETED


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
        enterprise=EnterpriseInput(company_name="金调双源制造有限公司"),
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
