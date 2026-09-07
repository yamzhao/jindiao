from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from jindiao.api.app import create_app
from jindiao.api.sse import stream_sse_events
from jindiao.application.context import RunContext
from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.contracts.entities import EnterpriseInput, ResolvedSubject
from jindiao.contracts.events import EventSequencer, EventType, RunEvent
from jindiao.contracts.results import (
    DueDiligenceRequest,
    DueDiligenceResult,
    SkillEvolutionFeedback,
    SkillEvolutionStatus,
)
from jindiao.orchestration.base import DomainInvestigation
from jindiao.orchestration.scenario_toolset import ScenarioToolset
from jindiao.scenarios import ScenarioRepository

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class IdFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return f"id-{self.calls}"


def service(id_factory: IdFactory | None = None) -> DueDiligenceService:
    return DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            data_source_mode="mock",
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=id_factory or IdFactory(),
        clock=lambda: NOW,
    )


def request_body() -> dict[str, object]:
    return {
        "enterprise": {"company_name": "金调绿洲科技有限公司"},
        "scenario_id": "normal-enterprise",
        "language": "zh-CN",
    }


@pytest.mark.asyncio
async def test_request_report_as_of_controls_review_and_decision_date() -> None:
    result = await service().run(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
            scenario_id="normal-enterprise",
            report_as_of=date(2026, 8, 1),
        )
    )

    assert result.decision.as_of_date == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_legacy_feedback_is_rejected_without_fake_candidate(tmp_path: Path) -> None:
    runtime_service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        clock=lambda: NOW,
    )
    request = DueDiligenceRequest(
        enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
        scenario_id="normal-enterprise",
        skill_feedback=SkillEvolutionFeedback(
            source="review_form",
            reference="artifact://previous-run/feedback.json#1",
            text="请把数据缺口放到对应结论旁边。",
            evidence_refs=("artifact://previous-run/report.md#risk-summary",),
        ),
    )

    result = await runtime_service.run(request)
    streamed = [event async for event in runtime_service.stream(request)]

    assert result.skill_evolution.status is SkillEvolutionStatus.REJECTED
    assert result.skill_evolution.reason_codes == ("use_feedback_api",)
    assert result.skill_evolution.candidate_version is None
    assert not list((tmp_path / "skill-evolution" / "candidates").glob("*/metadata.json"))
    assert EventType.SKILL_EVOLUTION_PROPOSED in {event.event_type for event in streamed}


class BlockingToolset:
    def __init__(self) -> None:
        self._delegate = ScenarioToolset(clock=lambda: NOW)
        self.investigation_started = asyncio.Event()
        self.release = asyncio.Event()

    async def resolve_subject(self, context: RunContext) -> ResolvedSubject:
        return await self._delegate.resolve_subject(context)

    async def investigate(
        self,
        context: RunContext,
        subject: ResolvedSubject,
        domain: str,
    ) -> DomainInvestigation:
        self.investigation_started.set()
        await self.release.wait()
        return await self._delegate.investigate(context, subject, domain)


@pytest.mark.asyncio
async def test_service_stream_emits_progress_before_investigations_finish(tmp_path: Path) -> None:
    toolset = BlockingToolset()
    runtime_service = DueDiligenceService(
        settings=Settings(
            model_provider="offline_mock",
            model_name="deterministic-mock",
            artifact_root=tmp_path,
        ),
        scenarios=ScenarioRepository(Path("mock_data/scenarios")),
        id_factory=IdFactory(),
        clock=lambda: NOW,
        toolset_factory=lambda: toolset,
    )
    events = runtime_service.stream(
        DueDiligenceRequest(
            enterprise=EnterpriseInput(company_name="金调绿洲科技有限公司"),
            scenario_id="normal-enterprise",
        )
    )

    accepted = await anext(events)
    progress_task: asyncio.Future[RunEvent] = asyncio.ensure_future(anext(events))
    await asyncio.wait_for(toolset.investigation_started.wait(), timeout=1)
    progress = await asyncio.wait_for(progress_task, timeout=1)
    toolset.release.set()
    remaining = [event async for event in events]

    assert accepted.event_type is EventType.RUN_ACCEPTED
    assert progress.event_type is EventType.ENTITY_RESOLVED
    assert remaining[-1].event_type is EventType.REPORT_COMPLETED


@pytest.mark.asyncio
async def test_app_registers_one_business_endpoint_and_returns_complete_json() -> None:
    app = create_app(service=service())
    business_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    ]
    assert [(route.path, route.methods) for route in business_routes] == [
        ("/api/v1/due-diligence/result", {"POST"})
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/due-diligence/result",
            json=request_body(),
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 200
    result = DueDiligenceResult.model_validate(response.json())
    assert result.meta.mode.value == "multi"
    assert result.meta.status.value == "completed"
    assert len(result.sections) == 8
    assert result.report_markdown.startswith("# 企业信用与风控尽调报告")
    assert set(response.json()) == {
        "meta",
        "subject",
        "decision",
        "risk_summary",
        "coverage",
        "sections",
        "findings",
        "evidence",
        "agent_results",
        "report_structure",
        "context_snapshot",
        "execution_cost",
        "comparison_metadata",
        "agent_trace",
        "collaboration",
        "evaluation",
        "skill_evolution",
        "report_markdown",
        "errors",
    }


@pytest.mark.asyncio
async def test_result_endpoint_selects_single_mode_for_json() -> None:
    app = create_app(service=service())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/due-diligence/result?mode=single",
            json=request_body(),
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["meta"]["mode"] == "single"


@pytest.mark.asyncio
async def test_result_endpoint_selects_single_mode_for_sse() -> None:
    app = create_app(service=service())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/due-diligence/result?mode=single",
            json=request_body(),
            headers={"Accept": "text/event-stream"},
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert events[-1]["payload"]["result"]["meta"]["mode"] == "single"


@pytest.mark.asyncio
async def test_result_endpoint_rejects_invalid_mode_before_creating_run() -> None:
    ids = IdFactory()
    app = create_app(service=service(ids))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/due-diligence/result?mode=parallel",
            json=request_body(),
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 422
    assert ids.calls == 0


@pytest.mark.asyncio
async def test_sse_sequences_events_and_finishes_with_json_equivalent_result() -> None:
    app = create_app(service=service())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        json_response = await client.post(
            "/api/v1/due-diligence/result",
            json=request_body(),
            headers={"Accept": "application/json"},
        )
        sse_response = await client.post(
            "/api/v1/due-diligence/result",
            json=request_body(),
            headers={"Accept": "text/event-stream"},
        )

    assert sse_response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert events[0]["event_type"] == "run.accepted"
    assert events[-1]["event_type"] == "report.completed"
    json_result = json_response.json()
    sse_result = events[-1]["payload"]["result"]
    DueDiligenceResult.model_validate(sse_result)
    assert set(sse_result) == set(json_result)
    for result in (json_result, sse_result):
        result["meta"] = {
            key: value
            for key, value in result["meta"].items()
            if key not in {"request_id", "run_id"}
        }
    assert sse_result == json_result


@pytest.mark.asyncio
async def test_unsupported_accept_is_rejected_before_a_run_is_created() -> None:
    ids = IdFactory()
    app = create_app(service=service(ids))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/due-diligence/result",
            json=request_body(),
            headers={"Accept": "application/xml"},
        )

    assert response.status_code == 406
    assert ids.calls == 0


@pytest.mark.asyncio
async def test_entity_failure_has_explicit_json_and_sse_semantics() -> None:
    app = create_app(service=service())
    body = {"enterprise": {"company_name": "不存在的企业有限公司"}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        json_response = await client.post(
            "/api/v1/due-diligence/result",
            json=body,
            headers={"Accept": "application/json"},
        )
        sse_response = await client.post(
            "/api/v1/due-diligence/result",
            json=body,
            headers={"Accept": "text/event-stream"},
        )

    assert json_response.status_code == 404
    assert json_response.json()["error"]["code"] == "entity_not_found"
    events = [
        json.loads(line.removeprefix("data: "))
        for line in sse_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [item["event_type"] for item in events] == ["run.accepted", "run.failed"]
    assert events[-1]["payload"]["error"]["code"] == "entity_not_found"


@pytest.mark.asyncio
async def test_sse_disconnect_cancels_the_downstream_event_producer() -> None:
    cancelled = False
    checks = 0
    sequencer = EventSequencer(request_id="req-1", run_id="run-1")

    async def source() -> AsyncIterator[RunEvent]:
        nonlocal cancelled
        try:
            yield sequencer.next(EventType.RUN_ACCEPTED, {"status": "accepted"})
            await __import__("asyncio").Event().wait()
        finally:
            cancelled = True

    async def disconnected() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    output = [
        item
        async for item in stream_sse_events(
            source(),
            disconnect_check=disconnected,
            poll_interval=0.001,
        )
    ]

    assert output[0]["event"] == "run.accepted"
    assert cancelled is True
