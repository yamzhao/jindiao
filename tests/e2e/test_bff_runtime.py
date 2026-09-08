"""Actual due-diligence contracts behind a local gateway adapter, not AgentArts."""

# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from starlette.types import Receive, Scope, Send

from jindiao.api.app import create_app as runtime_app
from jindiao.application.settings import Settings
from jindiao.bff.app import create_app
from jindiao.bff.config import BffSettings
from jindiao.bff.security import hash_password

ORIGIN = "https://bff.example"
RUNS = "/api/v2/due-diligence/runs"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_bff_with_actual_runtime_contracts(tmp_path: Path, mode: str) -> None:
    runtime = runtime_app(
        settings=Settings(  # type: ignore[call-arg]
            _env_file=None,
            model_provider="offline_mock",
            model_name="deterministic-mock",
            agent_runtime_mode="deterministic_harness",
            data_source_mode="mock",
            artifact_root=tmp_path,
            execution_profile="attached",
            shared_storage_backend="memory",
        )
    )
    key = "local-adapter-test-key-not-real"
    prefix = "/runtimes/jindiao-test/invocations"

    async def gateway(scope: Scope, receive: Receive, send: Send) -> None:
        assert dict(scope["headers"])[b"authorization"] == f"Bearer {key}".encode()
        assert scope["path"].startswith(prefix + RUNS)
        scope = dict(scope)
        scope["path"] = scope["path"][len(prefix) :]
        scope["raw_path"] = scope["path"].encode()
        await runtime(scope, receive, send)

    hashed = hash_password("local-integration-test-password")
    bff = create_app(
        BffSettings(
            gateway_origin="https://gateway.example",
            runtime_name="jindiao-test",
            api_key=SecretStr(key),
            identity_key=SecretStr("local-test-identity-key-0123456789"),
            public_origin=ORIGIN,
            users={"alice": hashed, "bob": hashed},
        ),
        transport=httpx.ASGITransport(app=gateway),
    )
    async with (
        bff.router.lifespan_context(bff),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bff),
            base_url=ORIGIN,
        ) as client,
    ):

        async def login(user: str) -> dict[str, str]:
            response = await client.post(
                "/auth/login",
                headers={"Origin": ORIGIN},
                json={
                    "username": user,
                    "password": "local-integration-test-password",
                },
            )
            assert response.status_code == 200
            return {
                "Origin": ORIGIN,
                "X-CSRF-Token": response.json()["csrf_token"],
                "Idempotency-Key": "contract-test",
            }

        headers = await login("alice")
        payload: dict[str, Any] = {
            "customerName": "乐视网信息技术（北京）股份有限公司",
            "scenario_id": "normal-enterprise",
            "mode": mode,
        }
        created = await client.post(RUNS, headers=headers, json=payload)
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        repeated = await client.post(RUNS, headers=headers, json=payload)
        assert repeated.json()["run_id"] == run_id
        await runtime.state.run_coordinator.execute(run_id)
        status = await client.get(f"{RUNS}/{run_id}")
        assert status.json()["status"] == "partial"
        assert "owner_id" not in status.json() and "session_id" not in status.json()
        events = await client.get(f"{RUNS}/{run_id}/events")
        assert events.status_code == 200 and "proxy.error" not in events.text
        parsed = [
            json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")
        ]
        assert parsed[-1]["event_type"] == "run.partial"
        result = await client.get(f"{RUNS}/{run_id}/result")
        assert result.status_code == 200 and result.json()["meta"]["run_id"] == run_id
        assert key not in result.text + events.text + status.text
        await login("bob")
        assert (await client.get(f"{RUNS}/{run_id}/result")).status_code == 404
        await login("alice")
        replay = await client.get(
            f"{RUNS}/{run_id}/events",
            headers={
                "Last-Event-ID": str(status.json()["latest_sequence"]),
            },
        )
        assert replay.status_code == 200 and "data:" not in replay.text
