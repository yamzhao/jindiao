from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolCard, tool
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig


class ScriptedModel:
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self.responses = responses
        self.calls = 0

    async def invoke(self, **kwargs: object) -> AssistantMessage:
        del kwargs
        response = self.responses[self.calls]
        self.calls += 1
        return response

    async def stream(self, **kwargs: object) -> AsyncIterator[AssistantMessageChunk]:
        del kwargs
        response = self.responses[self.calls]
        self.calls += 1
        yield AssistantMessageChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
            finish_reason=response.finish_reason,
        )


def _message(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        tool_calls=tool_calls,
        finish_reason="stop",
        usage_metadata=UsageMetadata(
            model_name="probe-model",
            input_tokens=5,
            output_tokens=2,
            total_tokens=7,
        ),
    )


def _agent(agent_id: str, model: ScriptedModel) -> ReActAgent:
    agent = ReActAgent(AgentCard(id=agent_id, name=agent_id)).configure(
        ReActAgentConfig(
            model_name="probe-model",
            model_provider="probe-provider",
            prompt_template=[{"role": "system", "content": "Use registered tools."}],
            max_iterations=3,
        )
    )
    assert isinstance(agent, ReActAgent)
    agent.set_llm(cast(Any, model))
    return agent


def test_openjiuwen_public_runtime_surface_is_available() -> None:
    assert inspect.iscoroutinefunction(ReActAgent.invoke)
    assert inspect.isasyncgenfunction(ReActAgent.stream)
    assert inspect.iscoroutinefunction(Runner.run_agent)
    assert inspect.isasyncgenfunction(Runner.run_agent_streaming)
    assert inspect.iscoroutinefunction(Runner.release)
    assert inspect.iscoroutinefunction(ReActAgent.clear_session)


@pytest.mark.asyncio
async def test_react_agent_invokes_registered_tool_and_reports_provider_usage() -> None:
    tool_calls: list[str] = []

    @tool(  # type: ignore[untyped-decorator]
        card=ToolCard(
            id="runtime-probe.echo",
            name="runtime_probe_echo",
            description="Echo one bounded probe value.",
            input_params={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            stateless=False,
            idempotent=True,
        )
    )
    async def echo(value: str) -> dict[str, str]:
        tool_calls.append(value)
        return {"value": value}

    model = ScriptedModel(
        [
            _message(
                tool_calls=[
                    ToolCall(
                        id="call-probe",
                        type="function",
                        name="runtime_probe_echo",
                        arguments='{"value":"ok"}',
                    )
                ]
            ),
            _message("probe complete"),
        ]
    )
    agent = _agent("runtime-probe-agent", model)
    registration = agent.ability_manager.add_ability(echo.card, echo)
    session_id = "runtime-probe-invoke"
    try:
        await Runner.start()
        result = await Runner.run_agent(
            agent,
            {"query": "Run the probe.", "conversation_id": session_id},
            session=session_id,
        )

        assert registration.added is True
        assert tool_calls == ["ok"]
        assert result == {"output": "probe complete", "result_type": "answer"}
        assert model.calls == 2
        assert all(response.usage_metadata is not None for response in model.responses)
    finally:
        await agent.clear_session(session_id)
        agent.ability_manager.teardown_tools()
        await Runner.stop()

    assert Runner.resource_mgr.get_tool(echo.card.id) is None


@pytest.mark.asyncio
async def test_react_agent_streaming_exposes_usage_and_final_answer_chunks() -> None:
    model = ScriptedModel([_message("streamed answer")])
    agent = _agent("runtime-probe-stream-agent", model)
    session_id = "runtime-probe-stream"
    try:
        await Runner.start()
        chunks = [
            chunk
            async for chunk in Runner.run_agent_streaming(
                agent,
                {"query": "Stream the probe.", "conversation_id": session_id},
                session=session_id,
            )
        ]
    finally:
        await agent.clear_session(session_id)
        await Runner.stop()

    chunk_types = [getattr(chunk, "type", None) for chunk in chunks]
    assert "llm_output" in chunk_types
    assert "llm_usage" in chunk_types
    assert "answer" in chunk_types
