# ruff: noqa: RUF001 -- official company name uses fullwidth parentheses
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
        "customerName": "乐视网信息技术（北京）股份有限公司",
        "scenario_id": "normal-enterprise",
        "mode": "single",
    }


@pytest.mark.asyncio
async def test_timeout_result_uses_explicit_error_not_generic_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jindiao.application.errors import AgentTimeoutError

    application = app(tmp_path)

    async def fail(*args: object, **kwargs: object) -> None:
        raise AgentTimeoutError("Agent execution timed out", details={"timeout_seconds": 300})

    monkeypatch.setattr(application.state.due_diligence_service, "_run_impl", fail)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post("/api/v2/due-diligence/runs", json=body())
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        state = await client.get(f"/api/v2/due-diligence/runs/{run_id}")
        result = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result")
        assert state.json()["status"] == "failed"
        assert state.json()["result_available"] is False
        assert result.status_code == 504
        assert result.json()["error"]["code"] == "agent_execution_timeout"
        assert result.json()["error"] == state.json()["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "multi"])
async def test_v2_run_lifecycle_supports_create_status_events_result_and_idempotency(
    tmp_path: Path,
    mode: str,
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
        request = {**body(), "mode": mode}
        created = await client.post("/api/v2/due-diligence/runs", json=request, headers=headers)
        repeated = await client.post(
            "/api/v2/due-diligence/runs",
            json={**request, "amount": None},
            headers=headers,
        )
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        status = await client.get(f"/api/v2/due-diligence/runs/{run_id}", headers=headers)
        events = await client.get(f"/api/v2/due-diligence/runs/{run_id}/events", headers=headers)
        result = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result", headers=headers)
        replay = await client.get(
            f"/api/v2/due-diligence/runs/{run_id}/events", headers={**headers, "Last-Event-ID": "4"}
        )

    assert created.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["run_id"] == run_id
    assert status.json()["status"] == "partial"
    lines = [
        json.loads(line.removeprefix("data: "))
        for line in events.text.splitlines()
        if line.startswith("data: ")
    ]
    assert lines[-1]["event_type"] == "run.partial"
    semantic = [line for line in lines if line["event_type"].startswith("execution.")]
    assert semantic[0]["event_type"] == "execution.plan.created"
    assert sum(line["event_type"] == "execution.plan.created" for line in semantic) == 1
    completed = [line for line in semantic if line["event_type"] == "execution.step.completed"]
    assert len(completed) == 7
    assert len({line["payload"]["step"]["step_id"] for line in completed}) == 7
    assert all(line["payload"]["step"]["conclusion"] for line in completed)
    assert [
        json.loads(line.removeprefix("data: "))
        for line in replay.text.splitlines()
        if line.startswith("data: ")
    ] == [line for line in lines if line["sequence"] > 4]
    assert result.status_code == 200
    assert result.json()["schema_version"] == "prototype-v1"
    assert result.json()["meta"]["run_id"] == run_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"amount": 4000},
        {"term": 24},
        {"product": "固定资产贷款"},
        {"manager": "另一经理"},
        {"branch": "另一支行"},
        {"customerName": "另一企业"},
    ],
)
async def test_v2_idempotency_hash_includes_flat_business_fields(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    application = app(tmp_path)
    headers = {
        "Idempotency-Key": "same-request-key",
        "X-Hw-Agentgateway-User-Id": "user-1",
    }
    first_body = {
        **body(),
        "amount": 5000,
    }
    changed_body = {
        **first_body,
        **changed,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.post("/api/v2/due-diligence/runs", json=first_body, headers=headers)
        conflict = await client.post(
            "/api/v2/due-diligence/runs", json=changed_body, headers=headers
        )

    assert first.status_code == 202
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_v2_idempotency_ignores_submitted_uscc(tmp_path: Path) -> None:
    application = app(tmp_path)
    headers = {"Idempotency-Key": "name-only"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.post("/api/v2/due-diligence/runs", json=body(), headers=headers)
        assert first.status_code == 202
        for uscc in (None, "", "91110000EXAMPLE001", "91110108MA01JD001A"):
            repeated = await client.post(
                "/api/v2/due-diligence/runs",
                json={**body(), "uscc": uscc},
                headers=headers,
            )
            assert repeated.status_code == 202
            assert repeated.json()["run_id"] == first.json()["run_id"]


@pytest.mark.asyncio
async def test_form_normalization_is_idempotent_and_openapi_is_flat(tmp_path: Path) -> None:
    application = app(tmp_path)
    schema = application.openapi()["components"]["schemas"]["RunCreateRequest"]
    assert "customerName" in schema["properties"]
    assert (
        not {"enterprise", "business_context", "region", "company_name"}
        & schema["properties"].keys()
    )
    headers = {"Idempotency-Key": "normalized-form"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        first = await client.post(
            "/api/v2/due-diligence/runs",
            headers=headers,
            json={**body(), "amount": 5000, "term": 12, "manager": "王某某"},
        )
        normalized = await client.post(
            "/api/v2/due-diligence/runs",
            headers=headers,
            json={
                **body(),
                "amount": "5000.0",
                "term": "12",
                "manager": " 王某某 ",
                "branch": "  ",
                "product": None,
            },
        )
        assert first.status_code == normalized.status_code == 202
        assert first.json()["run_id"] == normalized.json()["run_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("numeric_text", [False, True])
@pytest.mark.parametrize(
    "endpoint,wrapped",
    [("/api/v2/due-diligence/runs", False), ("/invocations", False), ("/invocations", True)],
)
async def test_flat_form_reaches_report_and_persisted_result(
    tmp_path: Path,
    endpoint: str,
    wrapped: bool,
    numeric_text: bool,
) -> None:
    application = app(tmp_path)
    payload = {
        **body(),
        "uscc": "91110000EXAMPLE001",
        "product": "流动资金贷款",
        "amount": "1.0001" if numeric_text else 1.0001,
        "term": "12" if numeric_text else 12,
        "manager": " 王某某 ",
        "branch": "城东支行",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(endpoint, json={"input": payload} if wrapped else payload)
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        await application.state.run_coordinator.execute(run_id)
        response = await client.get(f"/api/v2/due-diligence/runs/{run_id}/result")
        assert response.status_code == 200, response.text
        product = response.json()
        profile = product["report"]["company_profile"]
        assert profile["company_name"] == payload["customerName"]
        assert profile["unified_social_credit_code"] == "91110108MA01JD001A"
        plan = product["report"]["business_plan"]
        assert plan["business_product"] == "流动资金贷款"
        assert plan["customer_manager"] == "王某某"
        assert plan["reporting_org"] == "城东支行"
        assert plan["application_amount"] == 10001 and plan["application_term_months"] == 12
        assert plan["application_type"] is None
        assert plan["suggested_amount"] == 10000
        assert 1 <= plan["suggested_loan_term_months"] <= plan["suggested_credit_term_months"] <= 3
        assert plan["suggested_interest_rate"]
        assert plan["guarantee_methods"] and plan["repayment_methods"]
        assert plan["suggestion_source"] == "rules"
        assert len(plan["generated_fields"]) == 6
        assert not set(plan["generated_fields"]) & {gap["field"] for gap in plan["missing_fields"]}
        for field in (
            "fund_use",
            "reporting_date",
            "repayment_source",
            "unified_credit",
            "investigation_location",
        ):
            assert plan[field] is None
        assert "城东支行" in product["report_markdown"]
        assert "规则保守建议" in product["report_markdown"]
        assert "建议担保方式" in product["report_markdown"]
        for name, value in (("business_product", "流动资金贷款"), ("customer_manager", "王某某")):
            assert value in product["report_markdown"]
            assert any(
                e["source_type"] == "user_input"
                and f"report.business_plan.{name}" in e["supports_fields"]
                for e in product["evidence"]
            )
        assert (
            "业务品种" in product["report_markdown"]
            and "主办客户经理" in product["report_markdown"]
        )
        stored = await application.state.run_coordinator.repository.get_result(run_id)
        assert stored.model_dump(mode="json") == product


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,wrapped",
    [("/api/v2/due-diligence/runs", False), ("/invocations", False), ("/invocations", True)],
)
@pytest.mark.parametrize(
    "name_fields", [{}, {"customerName": None}, {"customerName": ""}, {"customerName": "  "}]
)
async def test_flat_form_requires_customer_name_even_with_uscc(
    tmp_path: Path, endpoint: str, wrapped: bool, name_fields: dict[str, object]
) -> None:
    application = app(tmp_path)
    payload = {"uscc": "91110108MA01JD001A", **name_fields}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(endpoint, json={"input": payload} if wrapped else payload)
        assert response.status_code == 422
        assert application.state.run_coordinator.active_runs() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/api/v2/due-diligence/runs", "/invocations"])
@pytest.mark.parametrize(
    "extra",
    [
        {"enterprise": {"company_name": "旧名称"}},
        {"region": "北京"},
        {"business_context": {}},
        {"company_name": "旧名称"},
        {"amount": 0},
        {"term": 1.5},
        {"amount": "NaN"},
        {"amount": "1e309"},
        {"amount": "0"},
        {"amount": ""},
        {"amount": True},
        {"term": "1.5"},
        {"term": "12.0"},
        {"term": "-12"},
        {"term": ""},
        {"term": True},
    ],
)
async def test_flat_form_invalid_request_is_rejected_before_execution(
    tmp_path: Path,
    endpoint: str,
    extra: dict[str, object],
) -> None:
    application = app(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(endpoint, json={**body(), **extra})
        assert response.status_code == 422
        assert application.state.run_coordinator.active_runs() == 0


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
