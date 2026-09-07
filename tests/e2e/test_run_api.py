from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jindiao.api.app import create_app
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.scenarios import ScenarioRepository


def app(tmp_path: Path) -> FastAPI:
    service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
    )
    return create_app(service=service)


def body() -> dict[str, object]:
    return {
        "enterprise": {"company_name": "金调绿洲科技有限公司"},
        "scenario_id": "normal-enterprise",
        "mode": "single",
    }


@pytest.mark.asyncio
async def test_v2_run_lifecycle_supports_create_status_events_result_and_idempotency(
    tmp_path: Path,
) -> None:
    application = app(tmp_path)
    headers = {
        "Accept": "application/json",
        "Idempotency-Key": "demo-1",
        "X-Hw-Agentgateway-User-Id": "user-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post("/api/v2/due-diligence/runs", json=body(), headers=headers)
        repeated = await client.post("/api/v2/due-diligence/runs", json=body(), headers=headers)
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        status = await client.get(f"/api/v2/due-diligence/runs/{run_id}", headers=headers)
        events = await client.get(f"/api/v2/due-diligence/runs/{run_id}/events", headers=headers)
        result = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result", headers=headers)

    assert created.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["run_id"] == run_id
    assert status.json()["status"] == "completed"
    lines = [
        json.loads(line.removeprefix("data: "))
        for line in events.text.splitlines()
        if line.startswith("data: ")
    ]
    assert lines[-1]["event_type"] == "run.completed"
    assert result.status_code == 200
    assert result.json()["meta"]["run_id"] == run_id


@pytest.mark.asyncio
async def test_v2_run_access_is_not_disclosed_to_another_principal(tmp_path: Path) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v2/due-diligence/runs",
            json=body(),
            headers={"X-Hw-Agentgateway-User-Id": "owner"},
        )
        run_id = created.json()["run_id"]
        hidden = await client.get(
            f"/api/v2/due-diligence/runs/{run_id}",
            headers={"X-Hw-Agentgateway-User-Id": "other"},
        )
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_agentarts_invocations_and_ping_are_available(tmp_path: Path) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/invocations", json=body())
        ping = await client.get("/ping")
    assert response.status_code == 202
    assert ping.status_code == 200
    assert ping.json()["status"] in {"Healthy", "HealthyBusy"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["{}", "{", '{"input":{"mode":"invalid"}}'])
async def test_invocations_validation_returns_422_without_starting_run(
    tmp_path: Path,
    payload: str,
) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/invocations", content=payload, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert application.state.run_coordinator.active_runs() == 0


@pytest.mark.asyncio
async def test_invocations_rejects_unsupported_accept_before_execution(tmp_path: Path) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/invocations", json=body(), headers={"Accept": "text/plain"})
    assert response.status_code == 406
    assert application.state.run_coordinator.active_runs() == 0


@pytest.mark.asyncio
async def test_events_authorization_is_checked_before_sse_headers(tmp_path: Path) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/v2/due-diligence/runs",
            json=body(),
            headers={"X-Hw-Agentgateway-User-Id": "owner"},
        )
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        for target in (run_id, "unknown-run"):
            response = await client.get(
                f"/api/v2/due-diligence/runs/{target}/events",
                headers={"X-Hw-Agentgateway-User-Id": "other"},
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "run_not_found"


@pytest.mark.asyncio
async def test_terminal_events_cursor_closes_without_waiting_for_more_events(
    tmp_path: Path,
) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post("/api/v2/due-diligence/runs", json=body())
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        resource = await application.state.run_coordinator.get(run_id)
        response = await asyncio.wait_for(
            client.get(
                f"/api/v2/due-diligence/runs/{run_id}/events",
                headers={"Last-Event-ID": str(resource.latest_sequence)},
            ),
            timeout=1,
        )
        assert response.status_code == 200
        assert "data:" not in response.text
