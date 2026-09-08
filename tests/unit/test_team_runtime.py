from __future__ import annotations

import asyncio
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from jindiao.application.service import DueDiligenceService
from jindiao.application.settings import Settings
from jindiao.orchestration.team_runtime import OfflineTeamRuntime, OpenJiuwenTeamRuntime
from jindiao.orchestration.team_spec import build_due_diligence_team_spec
from jindiao.scenarios import ScenarioRepository


@pytest.mark.asyncio
async def test_openjiuwen_runtime_uses_runner_stream_and_redacts_private_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_stream(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        yield SimpleNamespace(
            payload={
                "event_type": "member.completed",
                "member_name": "governance-agent",
                "status": "completed",
                "reasoning_content": "private chain of thought",
            }
        )

    monkeypatch.setattr(
        "jindiao.orchestration.team_runtime.Runner.run_agent_team_streaming",
        fake_stream,
    )
    spec = build_due_diligence_team_spec(
        team_name="runtime-test",
        model_name="test-model",
        max_review_rounds=1,
    )

    events = [
        item
        async for item in OpenJiuwenTeamRuntime().stream(
            spec,
            {"request_id": "req-1"},
            session_id="run-1",
        )
    ]

    assert calls[0]["agent_team"] is spec
    assert calls[0]["session"] == "run-1"
    assert events[0].event_type == "member.completed"
    assert events[0].member_name == "governance-agent"
    assert "reasoning_content" not in events[0].payload


@pytest.mark.asyncio
async def test_native_stream_preserves_task_local_session_between_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jindiao.orchestration import team_runtime

    context: ContextVar[str] = ContextVar("native_session", default="unbound")
    observed = []

    async def stream(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        token = context.set("bound-session")
        try:
            yield SimpleNamespace(payload={"event_type": "member.started"})
            observed.append(context.get())
            yield SimpleNamespace(payload={"event_type": "member.completed"})
        finally:
            context.reset(token)

    monkeypatch.setattr(team_runtime.Runner, "run_agent_team_streaming", stream)
    spec = build_due_diligence_team_spec(
        team_name="context-test", model_name="test", max_review_rounds=1
    )
    events = [e async for e in OpenJiuwenTeamRuntime().stream(spec, {}, session_id="bound-session")]
    assert len(events) == 2
    assert observed == ["bound-session"]
    assert context.get() == "unbound"


@pytest.mark.asyncio
async def test_idle_terminal_team_releases_stream_for_business_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jindiao.orchestration import team_runtime

    monkeypatch.setattr(team_runtime, "_QUIESCENCE_CHECK_SECONDS", 0.01, raising=False)
    closed = asyncio.Event()
    reads = 0
    stopped = []

    async def stream(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        try:
            yield SimpleNamespace(payload={"event_type": "team.runtime_ready"})
            await asyncio.Event().wait()
        finally:
            closed.set()

    class Monitor:
        async def start(self):  # type: ignore[no-untyped-def]
            pass

        async def stop(self):  # type: ignore[no-untyped-def]
            pass

        async def get_tasks(self):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(status="completed")]

        async def get_members(self):  # type: ignore[no-untyped-def]
            nonlocal reads
            reads += 1
            return [
                SimpleNamespace(
                    status="ready", execution_status="running" if reads == 1 else "idle"
                )
            ]

    async def monitor(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return Monitor()

    async def stop(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        stopped.append(True)
        return True

    monkeypatch.setattr(team_runtime.Runner, "run_agent_team_streaming", stream)
    monkeypatch.setattr(team_runtime.Runner, "get_agent_team_monitor", monitor)
    monkeypatch.setattr(team_runtime.Runner, "stop_agent_team", stop)
    monkeypatch.setattr(team_runtime.Runner, "delete_agent_team", stop)
    spec = build_due_diligence_team_spec(
        team_name="idle-recovery", model_name="test", max_review_rounds=1
    )
    async with asyncio.timeout(0.5):
        events = [e async for e in OpenJiuwenTeamRuntime().stream(spec, {}, session_id="idle")]
    assert events[-1].event_type == "team.runtime.quiescent"
    assert reads >= 3  # A running model prevents the first terminal-task observation from exiting.
    assert closed.is_set() and len(stopped) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tool_result", (True, False))
async def test_openjiuwen_runtime_stops_stream_then_deletes_team(
    monkeypatch: pytest.MonkeyPatch,
    has_tool_result: bool,
) -> None:
    stop_calls: list[dict[str, object]] = []
    delete_calls: list[dict[str, object]] = []

    async def fake_stream(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        yield SimpleNamespace(
            type="message",
            source_member="leader",
            payload={"event_type": "team.runtime_ready"},
        )
        if has_tool_result:
            yield SimpleNamespace(
                type="tool_result",
                source_member="leader",
                payload={
                    "event_type": "team.created",
                    "tool_name": "build_team",
                    "tool_result": "success=true",
                },
            )
        yield SimpleNamespace(
            type="message",
            source_member="governance-agent",
            payload={"event_type": "check.completed", "check_id": "registration"},
        )
        yield SimpleNamespace(
            type="message",
            source_member="leader",
            payload={"event_type": "team.completed"},
        )

    async def fake_stop(**kwargs):  # type: ignore[no-untyped-def]
        stop_calls.append(kwargs)
        return True

    async def fake_delete(**kwargs):  # type: ignore[no-untyped-def]
        delete_calls.append(kwargs)
        return True

    monkeypatch.setattr(
        "jindiao.orchestration.team_runtime.Runner.run_agent_team_streaming",
        fake_stream,
    )
    monkeypatch.setattr(
        "jindiao.orchestration.team_runtime.Runner.stop_agent_team",
        fake_stop,
    )
    monkeypatch.setattr(
        "jindiao.orchestration.team_runtime.Runner.delete_agent_team",
        fake_delete,
    )
    spec = build_due_diligence_team_spec(
        team_name="runtime-cleanup-test",
        model_name="test-model",
        max_review_rounds=1,
    )

    events = [
        item
        async for item in OpenJiuwenTeamRuntime().stream(
            spec,
            {"query": "complete every fixed check"},
            session_id="run-cleanup-1",
        )
    ]

    assert [event.event_type for event in events] == [
        "team.runtime_ready",
        *(["team.created"] if has_tool_result else []),
        "check.completed",
        "team.completed",
    ]
    assert stop_calls == [
        {
            "team_name": "runtime-cleanup-test",
            "session_id": "run-cleanup-1",
        }
    ]
    assert delete_calls == [
        {
            "team_name": "runtime-cleanup-test",
            "session_ids": ["run-cleanup-1"],
            "force": False,
        }
    ]


@pytest.mark.asyncio
async def test_openjiuwen_runtime_waits_for_every_team_task_to_be_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle: list[str] = []

    class FakeMonitor:
        def __init__(self) -> None:
            self.task_reads = 0
            self.member_reads = 0

        async def start(self) -> None:
            lifecycle.append("start")

        async def stop(self) -> None:
            lifecycle.append("stop")

        async def get_tasks(self):  # type: ignore[no-untyped-def]
            self.task_reads += 1
            return [
                SimpleNamespace(
                    task_id="review:round2-signal",
                    status="completed",
                )
            ]

        async def get_members(self):  # type: ignore[no-untyped-def]
            self.member_reads += 1
            reviewer_status = "busy" if self.member_reads == 1 else "ready"
            return [
                SimpleNamespace(member_name="leader", status="busy"),
                SimpleNamespace(
                    member_name="reviewer-agent",
                    status=reviewer_status,
                ),
            ]

        async def events(self):  # type: ignore[no-untyped-def]
            yield SimpleNamespace(event_type="task_completed")

    monitor = FakeMonitor()

    async def fake_get_monitor(**kwargs):  # type: ignore[no-untyped-def]
        assert kwargs == {
            "team_name": "runtime-terminal-test",
            "session_id": "run-terminal-1",
        }
        return monitor

    monkeypatch.setattr(
        "jindiao.orchestration.team_runtime.Runner.get_agent_team_monitor",
        fake_get_monitor,
    )

    await OpenJiuwenTeamRuntime().wait_for_tasks_terminal(
        team_name="runtime-terminal-test",
        session_id="run-terminal-1",
        leader_member_name="leader",
    )

    assert monitor.task_reads == 2
    assert monitor.member_reads == 2
    assert lifecycle == ["start", "stop"]


def test_service_selects_runtime_from_explicit_agent_runtime_mode() -> None:
    scenarios = ScenarioRepository(Path("mock_data/scenarios"))

    live = DueDiligenceService(
        settings=Settings(
            agent_runtime_mode="formal",
            model_provider="openai_compatible",
            model_name="test-model",
            model_base_url="https://example.invalid/v1",
            model_api_key=SecretStr("test-only-key"),
        ),
        scenarios=scenarios,
    )
    harness = DueDiligenceService(
        settings=Settings(
            agent_runtime_mode="deterministic_harness",
            model_provider="openai_compatible",
            model_name="scripted-test-model",
        ),
        scenarios=scenarios,
    )

    assert isinstance(live.team_runtime, OpenJiuwenTeamRuntime)
    assert isinstance(harness.team_runtime, OfflineTeamRuntime)


def test_team_spec_routes_complete_runtime_model_configuration() -> None:
    spec = build_due_diligence_team_spec(
        team_name="jindiao-live-model",
        model_name="qwen-plus",
        model_provider="OpenAI",
        model_base_url="https://dashscope.example/v1",
        model_api_key="test-only-secret",
        model_temperature=0.3,
        model_timeout_seconds=30,
        max_review_rounds=2,
    )

    assert spec.model_router is not None
    assert spec.model_router.model_names == ["qwen-plus"]
    assert spec.model_router.api_provider == "OpenAI"
    assert spec.model_router.api_base_url == "https://dashscope.example/v1"
    assert spec.model_router.api_key == "test-only-secret"
    assert spec.model_router.metadata == {
        "client": {
            "timeout": 30,
            "stream_first_chunk_timeout": 30,
            "stream_idle_timeout": 30,
        },
        "request": {"temperature": 0.3},
    }
