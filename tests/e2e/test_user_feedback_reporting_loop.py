# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jindiao.api.app import create_app
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.evidence import SourceStatus
from jindiao.contracts.product import ProductResult
from jindiao.contracts.runs import RunCreateRequest
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.scenarios import ScenarioRepository

HEADERS = {"x-hw-agentgateway-user-id": "alice", "x-hw-agentarts-session-id": "demo-session"}
FEEDBACK = {
    "kind": "gap_disclosure_placement",
    "text": "司法缺口请就近披露",
    "target_section_ids": ["external_verification"],
}


def demo_service(root: Path, *, enabled: bool = True) -> DueDiligenceService:
    return DueDiligenceService(
        settings=Settings(  # type: ignore[call-arg]
            _env_file=None,
            model_provider="offline_mock",
            model_name="deterministic-mock",
            data_source_mode="mock",
            artifact_root=root,
            reporting_demo_enabled=enabled,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        toolset_factory=lambda: ScenarioToolset(
            clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
            source_status_overrides={"judicial": SourceStatus.SOURCE_ERROR},
        ),
    )


async def completed_run(app: FastAPI) -> tuple[str, ProductResult]:
    coordinator = app.state.run_coordinator
    run = await coordinator.create(
        RunCreateRequest(
            customerName="乐视网信息技术（北京）股份有限公司",
            scenario_id="normal-enterprise",
        ),
        owner_id="alice",
        session_id="demo-session",
    )
    await coordinator.execute(run.run_id)
    result = await coordinator.get_result(run.run_id, owner_id="alice", session_id="demo-session")
    assert isinstance(result, ProductResult)
    return run.run_id, result


@pytest.mark.asyncio
async def test_demo_feedback_apply_new_run_and_reset(tmp_path: Path) -> None:
    app = create_app(service=demo_service(tmp_path))
    run_id, old = await completed_run(app)
    assert old.meta.status.value == "partial"
    events_before = await app.state.run_coordinator.event_store.read_after(run_id, 0)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=HEADERS
    ) as client:
        url = f"/api/v2/due-diligence/runs/{run_id}/feedback"
        response = await client.post(url, json=FEEDBACK, headers={"Idempotency-Key": "first"})
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["status"] == "awaiting_approval"
        assert data["evaluation"]["passed"]
        assert len(data["evaluation"]["cases"]) == 9
        assert "before" not in data["evaluation"]["cases"][0]
        repeated = await client.post(url, json=FEEDBACK, headers={"Idempotency-Key": "first"})
        assert repeated.status_code == 200
        assert repeated.json()["evolution_id"] == data["evolution_id"]
        detail = await client.get(data["detail_url"] + "?include=reports")
        assert detail.status_code == 200
        source = detail.json()["evaluation"]["cases"][0]
        assert source["before"] == old.report_markdown
        assert source["after_numerator"] > source["before_numerator"]
        _, unchanged = await completed_run(app)
        assert unchanged.report_markdown == old.report_markdown
        command = [
            sys.executable,
            "-m",
            "jindiao.reporting.demo_cli",
            "--demo",
            "--artifact-root",
            str(tmp_path),
        ]
        applied = await asyncio.to_thread(
            subprocess.run,
            [*command, "apply", data["evolution_id"], "--reason", "确认对比"],
            capture_output=True,
            text=True,
        )
        assert applied.returncode == 0, applied.stderr
        _, new = await completed_run(app)
        assert new.report_markdown == source["after"]
        assert new.subject == old.subject
        assert new.summary == old.summary
        assert new.report == old.report
        assert new.risk_findings == old.risk_findings
        assert new.evidence == old.evidence
        reset = await asyncio.to_thread(
            subprocess.run,
            [*command, "reset", "--reason", "恢复基线"],
            capture_output=True,
            text=True,
        )
        assert reset.returncode == 0, reset.stderr
        _, restored = await completed_run(app)
        assert restored.report_markdown == old.report_markdown
        assert (await client.post(data["detail_url"] + "/activate", json={})).status_code == 404
    assert await app.state.run_coordinator.event_store.read_after(run_id, 0) == events_before


@pytest.mark.asyncio
async def test_demo_permissions_references_and_readiness(tmp_path: Path) -> None:
    app = create_app(service=demo_service(tmp_path))
    run_id, _ = await completed_run(app)
    url = f"/api/v2/due-diligence/runs/{run_id}/feedback"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=HEADERS
    ) as client:
        assert (await client.post(url, json=FEEDBACK)).status_code == 422
        headers = {"Idempotency-Key": "one"}
        assert (
            await client.post(
                url, json=FEEDBACK, headers={**headers, "x-hw-agentgateway-user-id": "bob"}
            )
        ).status_code == 404
        assert (
            await client.post(
                url, json=FEEDBACK, headers={**headers, "x-hw-agentarts-session-id": "wrong"}
            )
        ).status_code == 404
        assert (
            await client.post(
                url, json={**FEEDBACK, "target_section_ids": ["unknown"]}, headers=headers
            )
        ).status_code == 422
        assert (
            await client.post(
                url, json=FEEDBACK, headers={**headers, "Origin": "https://evil.test"}
            )
        ).status_code == 403
        response = await client.post(url, json=FEEDBACK, headers=headers)
        assert response.status_code == 201
        detail = response.json()["detail_url"]
        assert (
            await client.get(detail, headers={"x-hw-agentgateway-user-id": "bob"})
        ).status_code == 404
        assert (await client.get(detail + "?case_id=../../secret")).status_code == 422
        assert (
            await client.post(url, json={**FEEDBACK, "text": "different"}, headers=headers)
        ).status_code == 409
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1234)),
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as remote:
        assert (await remote.get(detail)).status_code == 403


@pytest.mark.asyncio
async def test_demo_disabled_by_default(tmp_path: Path) -> None:
    app = create_app(service=demo_service(tmp_path, enabled=False))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.get("/api/v2/skill-evolutions/evo-unknown")).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["input", "output", "runtime", "deadline"])
async def test_evaluation_failure_isolated_and_has_correct_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from jindiao.reporting import replay as replay_module

    app = create_app(service=demo_service(tmp_path))
    run_id, original = await completed_run(app)
    events = await app.state.run_coordinator.event_store.read_after(run_id, 0)
    if boundary == "input":
        monkeypatch.setattr(replay_module, "MAX_TOTAL_BYTES", 1)
    elif boundary == "output":
        # Actual output includes before/after/diff and exceeds the frozen input.
        monkeypatch.setattr(replay_module, "MAX_TOTAL_BYTES", 70_000)
    elif boundary == "runtime":

        def fail() -> tuple[replay_module.ReplayCase, ...]:
            raise RuntimeError("simulated evaluator failure")

        monkeypatch.setattr(replay_module, "load_suite", fail)
    else:
        from types import SimpleNamespace

        times = iter((0.0, 11.0))
        monkeypatch.setattr(replay_module, "time", SimpleNamespace(monotonic=lambda: next(times)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as client:
        response = await client.post(
            f"/api/v2/due-diligence/runs/{run_id}/feedback",
            json=FEEDBACK,
            headers={"Idempotency-Key": "failed"},
        )
        assert response.status_code == (413 if boundary in {"input", "output"} else 500), (
            response.text
        )
        assert response.json()["status"] == "failed"
        detail = await client.get(response.json()["detail_url"])
        assert detail.status_code == 200
        assert detail.json()["status"] == "failed"
    assert (
        await app.state.run_coordinator.get_result(
            run_id, owner_id="alice", session_id="demo-session"
        )
        == original
    )
    assert await app.state.run_coordinator.event_store.read_after(run_id, 0) == events
    assert app.state.due_diligence_service.freeze_reporting_policy().version == "1.1.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["accepted", "cancelled", "missing", "tampered"])
async def test_feedback_refuses_unready_or_invalid_snapshot(tmp_path: Path, condition: str) -> None:
    app = create_app(service=demo_service(tmp_path))
    coordinator = app.state.run_coordinator
    if condition in {"accepted", "cancelled"}:
        run = await coordinator.create(
            RunCreateRequest(
                customerName="乐视网信息技术（北京）股份有限公司",
                scenario_id="normal-enterprise",
            ),
            owner_id="alice",
            session_id="demo-session",
        )
        run_id = run.run_id
        if condition == "cancelled":
            await coordinator.cancel(run_id, owner_id="alice", session_id="demo-session")
    else:
        run_id, _ = await completed_run(app)
        path = tmp_path / run_id / "report-replay.json"
        if condition == "missing":
            path.unlink()
        else:
            path.write_text(path.read_text().replace("1.1.0", "1.1.9"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as client:
        response = await client.post(
            f"/api/v2/due-diligence/runs/{run_id}/feedback",
            json=FEEDBACK,
            headers={"Idempotency-Key": "invalid"},
        )
        assert response.status_code == 409
    assert not list((tmp_path / "reporting-demo" / "candidates").glob("*.json"))


@pytest.mark.asyncio
async def test_local_boundary_busy_and_evidence_reference(tmp_path: Path) -> None:
    app = create_app(service=demo_service(tmp_path))
    run_id, _ = await completed_run(app)
    url = f"/api/v2/due-diligence/runs/{run_id}/feedback"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=HEADERS,
    ) as client:
        headers = {"Idempotency-Key": "one"}
        for extra in ({"X-Forwarded-For": "127.0.0.1"}, {"Host": "evil.test"}):
            assert (
                await client.post(url, json=FEEDBACK, headers=headers | extra)
            ).status_code == 403
        response = await client.post(
            url, json=FEEDBACK | {"evidence_ids": ["foreign-evidence"]}, headers=headers
        )
        assert response.status_code == 422
        with app.state.due_diligence_service.reporting_demo_store._lock():
            assert (await client.post(url, json=FEEDBACK, headers=headers)).status_code == 429
        response = await client.post(url, json=FEEDBACK, headers=headers)
        assert response.status_code == 201
        data = response.json()
        detail = await client.get(data["detail_url"] + "?case_id=single-gap")
        assert [case["case_id"] for case in detail.json()["evaluation"]["cases"]] == ["single-gap"]
        assert "before" in detail.json()["evaluation"]["cases"][0]
        app.state.due_diligence_service.reporting_demo_store.reset(reason="使旧基线过期")
        assert (await client.post(url, json=FEEDBACK, headers=headers)).status_code == 200
        assert (
            await client.post(url, json=FEEDBACK, headers={"Idempotency-Key": "new"})
        ).status_code == 409


@pytest.mark.parametrize("backend", ["session", "sfs"])
def test_demo_rejects_shared_storage(backend: str) -> None:
    with pytest.raises(ValueError, match="reporting demo requires"):
        Settings.model_validate(
            {
                "reporting_demo_enabled": True,
                "shared_storage_backend": backend,
            }
        )


def test_demo_rejects_production() -> None:
    with pytest.raises(ValueError, match="reporting demo requires"):
        Settings(reporting_demo_enabled=True, environment="production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", ["/api/v1/due-diligence/result", "/api/v2/due-diligence/runs", "/invocations"]
)
async def test_legacy_feedback_http_is_deprecated_and_rejected(
    tmp_path: Path, endpoint: str
) -> None:
    app = create_app(service=demo_service(tmp_path))
    schemas = app.openapi()["components"]["schemas"]
    assert schemas["DueDiligenceRequest"]["properties"]["skill_feedback"]["deprecated"] is True
    assert "skill_feedback" not in schemas["RunCreateRequest"]["properties"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        response = await client.post(
            endpoint,
            json={
                "enterprise": {"company_name": "乐视网信息技术（北京）股份有限公司"},
                "scenario_id": "normal-enterprise",
                "skill_feedback": {
                    "source": "legacy",
                    "reference": "artifact://old/report.md",
                    "text": "请就近披露",
                    "evidence_refs": ["old"],
                },
            },
        )
        if endpoint == "/api/v1/due-diligence/result":
            assert response.status_code == 200, response.text
            assert response.json()["schema_version"] == "prototype-v1"
            assert "skill_evolution" not in response.json()
        else:
            assert response.status_code == 422
            assert app.state.run_coordinator.active_runs() == 0
    assert not list((tmp_path / "skill-evolution").glob("candidates/*"))


def test_standalone_demo_writes_reviewable_evidence_in_new_directory(tmp_path: Path) -> None:
    import json

    from jindiao.paths import project_root

    # Caller configuration must not affect the isolated reproduction.
    (tmp_path / ".env").write_text("JINDIAO_REPORTING_DEMO_ENABLED=true\nJINDIAO_ENV=production\n")
    output = tmp_path / "isolated-demo"
    command = [
        sys.executable,
        str(project_root() / "scripts/run_reporting_feedback_demo.py"),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["verified"]
    assert summary["case_count"] == 9
    assert summary["source_unchanged"]
    assert (output / "before.md").read_text() != (output / "after.md").read_text()
    assert (output / "diff.patch").read_text()
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert repeated.returncode != 0
