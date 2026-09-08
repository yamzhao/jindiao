from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from openjiuwen.core.runner import Runner

from jindiao.contracts.results import AgentResultPhase
from jindiao.orchestration import agent_runtime as runtime


def test_public_agent_event_recursively_removes_private_runtime_fields() -> None:
    event = runtime.AgentExecutionEvent(
        run_id="run-1",
        agent_id="corporate-agent",
        role="corporate",
        phase=AgentResultPhase.INVESTIGATION,
        sequence=1,
        event_type=runtime.AgentExecutionEventType.MODEL_REQUEST_COMPLETED,
        occurred_at=datetime(2026, 9, 5, tzinfo=UTC),
        prompt_version="investigation-core-v1",
        prompt_sha256="a" * 64,
        payload={
            "usage_metadata": {"input_tokens": 20, "output_tokens": 5},
            "prompt": "private prompt",
            "nested": {"reasoning_content": "private reasoning", "status": "ok"},
        },
    )

    assert "prompt" not in event.payload
    assert event.payload["nested"] == {"status": "ok"}


@pytest.mark.parametrize(
    "role",
    (
        "enterprise-context",
        "supplemental-evidence",
        "single-investigator",
        "leader",
        "corporate",
        "judicial-compliance",
        "financial-operations",
        "related-peer",
        "reviewer",
    ),
)
def test_every_architecture_role_uses_the_same_execution_request(role: str) -> None:
    request = runtime.AgentExecutionRequest(
        run_id="run-roles",
        agent_id=f"{role}-agent",
        role=role,
        phase=(
            AgentResultPhase.ACQUISITION
            if role in {"enterprise-context", "supplemental-evidence"}
            else AgentResultPhase.INVESTIGATION
        ),
        session_id=f"session:{role}",
        query="Execute the assigned structured task.",
        task_ids=("task-1",),
        prompt_version="prompt-v1",
        prompt_sha256="b" * 64,
        timeout_seconds=30,
    )

    assert request.role == role


@pytest.mark.asyncio
async def test_openjiuwen_agent_runtime_streams_safe_events_and_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_stream(**kwargs: object) -> AsyncIterator[SimpleNamespace]:
        calls.append(kwargs)
        yield SimpleNamespace(
            type="llm_reasoning",
            payload={"reasoning_content": "never publish"},
        )
        yield SimpleNamespace(
            type="llm_usage",
            payload={
                "usage_metadata": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "total_tokens": 13,
                }
            },
        )
        yield SimpleNamespace(
            type="answer",
            payload={"output": "structured submission accepted", "result_type": "answer"},
        )

    monkeypatch.setattr(Runner, "run_agent_streaming", fake_stream)

    class FakeAbilityManager:
        def __init__(self) -> None:
            self.torn_down = False

        def teardown_tools(self) -> None:
            self.torn_down = True

    class FakeAgent:
        def __init__(self) -> None:
            self.cleared: list[str] = []
            self.ability_manager = FakeAbilityManager()

        async def clear_session(self, session_id: str) -> None:
            self.cleared.append(session_id)

    agent = FakeAgent()
    request = runtime.AgentExecutionRequest(
        run_id="run-runtime",
        agent_id="single-agent",
        role="single-investigator",
        phase=AgentResultPhase.INVESTIGATION,
        session_id="session-runtime",
        query="Execute all checks.",
        task_ids=("check:all",),
        prompt_version="single-v1",
        prompt_sha256="c" * 64,
        timeout_seconds=5,
    )

    events = [
        event async for event in runtime.OpenJiuwenAgentExecutionRuntime().stream(agent, request)
    ]

    assert calls == [
        {
            "agent": agent,
            "inputs": {
                "query": request.query,
                "conversation_id": request.session_id,
            },
            "session": request.session_id,
        }
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == [
        runtime.AgentExecutionEventType.STARTED,
        runtime.AgentExecutionEventType.MODEL_REQUEST_COMPLETED,
        runtime.AgentExecutionEventType.OUTPUT,
        runtime.AgentExecutionEventType.COMPLETED,
    ]
    assert all("reasoning_content" not in str(event.payload) for event in events)
    assert agent.cleared == [request.session_id]
    assert agent.ability_manager.torn_down is True


@pytest.mark.asyncio
async def test_openjiuwen_agent_runtime_cancellation_still_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancelled_stream(**kwargs: object) -> AsyncIterator[SimpleNamespace]:
        del kwargs
        yield SimpleNamespace(type="llm_output", payload={"content": "partial"})
        raise asyncio.CancelledError

    monkeypatch.setattr(Runner, "run_agent_streaming", cancelled_stream)

    class FakeAbilityManager:
        def teardown_tools(self) -> None:
            return None

    class FakeAgent:
        ability_manager = FakeAbilityManager()

        def __init__(self) -> None:
            self.cleared = False

        async def clear_session(self, session_id: str) -> None:
            del session_id
            self.cleared = True

    agent = FakeAgent()
    request = runtime.AgentExecutionRequest(
        run_id="run-cancel",
        agent_id="reviewer",
        role="reviewer",
        phase=AgentResultPhase.INVESTIGATION,
        session_id="session-cancel",
        query="Review submissions.",
        task_ids=("review:1",),
        prompt_version="reviewer-v1",
        prompt_sha256="d" * 64,
        timeout_seconds=5,
    )

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in runtime.OpenJiuwenAgentExecutionRuntime().stream(agent, request)
        ]

    assert agent.cleared is True


@pytest.mark.asyncio
async def test_agent_deadline_has_public_timeout_code_and_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jindiao.application.errors import AgentExecutionError, error_to_record

    closed = []

    async def blocked(**kwargs: object) -> AsyncIterator[SimpleNamespace]:
        del kwargs
        try:
            yield SimpleNamespace(type="llm_output", payload={})
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    monkeypatch.setattr(Runner, "run_agent_streaming", blocked)
    agent = SimpleNamespace(
        ability_manager=SimpleNamespace(teardown_tools=lambda: closed.append(True))
    )
    request = runtime.AgentExecutionRequest(
        run_id="run-timeout",
        agent_id="single-investigator",
        role="single-investigator",
        phase=AgentResultPhase.INVESTIGATION,
        session_id="timeout-session",
        query="test",
        task_ids=("check:all",),
        prompt_version="v1",
        prompt_sha256="e" * 64,
        timeout_seconds=0.01,
    )
    events = []
    with pytest.raises(AgentExecutionError) as caught:
        async for event in runtime.OpenJiuwenAgentExecutionRuntime().stream(agent, request):
            events.append(event)
    record = error_to_record(caught.value)
    assert record.code == "agent_execution_timeout"
    assert record.recoverable
    assert record.details == {
        "agent_id": request.agent_id,
        "phase": "investigation",
        "timeout_seconds": 0.01,
    }
    assert events[-1].event_type == runtime.AgentExecutionEventType.FAILED
    assert len(closed) == 2
