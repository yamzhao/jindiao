from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from jindiao.application.errors import AgentExecutionError
from jindiao.orchestration.agent_team_model import BudgetedAgentTeamModelClient
from jindiao.orchestration.base import BudgetLedger, RunBudget


class RecordingInnerModelClient:
    def __init__(self) -> None:
        self.requested_models: list[str | None] = []
        self.requested_max_tokens: list[int | None] = []

    @staticmethod
    def _response() -> AssistantMessage:
        return AssistantMessage(
            content="ok",
            finish_reason="stop",
            usage_metadata=UsageMetadata(
                model_name="upstream-model",
                input_tokens=3,
                output_tokens=1,
                total_tokens=4,
            ),
        )

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        del args
        self.requested_models.append(kwargs.get("model"))
        self.requested_max_tokens.append(kwargs.get("max_tokens"))
        return self._response()

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AssistantMessage]:
        del args
        self.requested_models.append(kwargs.get("model"))
        self.requested_max_tokens.append(kwargs.get("max_tokens"))
        yield self._response()


def _budget() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(
            max_tool_calls=10,
            max_concurrency=2,
            timeout_seconds=30,
            max_repair_rounds=1,
        )
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    inner: RecordingInnerModelClient,
    ledger: BudgetLedger,
) -> BudgetedAgentTeamModelClient:
    monkeypatch.setattr(
        "jindiao.orchestration.agent_team_model.create_model_client",
        lambda **kwargs: inner,
    )
    monkeypatch.setattr(
        BudgetedAgentTeamModelClient,
        "_ledger",
        property(lambda self: ledger),
    )
    return BudgetedAgentTeamModelClient(
        ModelRequestConfig(model="qwen-plus::leader"),
        ModelClientConfig(
            client_provider="jindiao_budgeted_agent_team",
            api_key="test-key",
            api_base="https://model.invalid/v1",
            runtime_key="runtime:test",
            member_name="leader",
            inner_provider="jindiao_openai_compatible",
            inner_model_name="qwen-plus",
        ),
    )


@pytest.mark.parametrize("configured_retries", [None, 5])
def test_inner_http_provider_disables_unmetered_retries_without_mutating_outer_config(
    monkeypatch: pytest.MonkeyPatch, configured_retries: int | None
) -> None:
    received_configs: list[ModelClientConfig] = []

    def create_inner(
        *, client_config: ModelClientConfig, model_config: object
    ) -> RecordingInnerModelClient:
        received_configs.append(client_config)
        return RecordingInnerModelClient()

    monkeypatch.setattr(
        "jindiao.orchestration.agent_team_model.create_model_client", create_inner
    )
    outer = ModelClientConfig(
        client_provider="jindiao_budgeted_agent_team",
        api_key="test-key",
        api_base="https://model.invalid/v1",
        runtime_key="runtime:test",
        inner_provider="jindiao_openai_compatible",
        inner_model_name="qwen-plus",
        **({"max_retries": configured_retries} if configured_retries is not None else {}),
    )
    original = outer.model_dump()
    BudgetedAgentTeamModelClient(ModelRequestConfig(model="qwen-plus::leader"), outer)

    assert len(received_configs) == 1
    assert received_configs[0].max_retries == 0
    assert received_configs[0].client_provider == "jindiao_openai_compatible"
    assert outer.model_dump() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["invoke", "stream"])
async def test_budgeted_team_model_replaces_member_alias_with_upstream_model(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    inner = RecordingInnerModelClient()
    ledger = _budget()
    client = _client(monkeypatch, inner, ledger)
    arguments = {"model": "qwen-plus::leader"}
    if method == "invoke":
        await client.invoke([], **arguments)
    else:
        assert [item async for item in client.stream([], **arguments)]

    assert inner.requested_models == ["qwen-plus"]
    assert inner.requested_max_tokens == [10_000]
    assert arguments == {"model": "qwen-plus::leader"}
    assert ledger.snapshot().llm_requests == 1
    assert ledger.snapshot().successful_llm_requests == 1
    assert ledger.snapshot().provider_usage_requests == 1
    assert ledger.snapshot().input_tokens == 3
    assert ledger.snapshot().output_tokens == 1
    assert ledger.snapshot().reserved_input_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["invoke", "stream"])
@pytest.mark.parametrize("field", ["messages", "tools"])
async def test_team_model_rejects_oversized_request_before_provider_dispatch(
    monkeypatch: pytest.MonkeyPatch, method: str, field: str
) -> None:
    inner = RecordingInnerModelClient()
    ledger = _budget()
    client = _client(monkeypatch, inner, ledger)
    arguments: dict[str, object] = {"messages": "hello", field: "x" * 300_001}
    with pytest.raises(AgentExecutionError, match="token budget"):
        if method == "invoke":
            await client.invoke(**arguments)
        else:
            _ = [chunk async for chunk in client.stream(**arguments)]
    assert inner.requested_models == []
    assert ledger.snapshot().llm_requests == 0


@pytest.mark.asyncio
async def test_team_stream_cancellation_keeps_usage_and_unknown_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancellingClient(RecordingInnerModelClient):
        async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AssistantMessage]:
            async for chunk in super().stream(*args, **kwargs):
                yield chunk
            raise asyncio.CancelledError()

    inner = CancellingClient()
    ledger = _budget()
    client = _client(monkeypatch, inner, ledger)
    with pytest.raises(asyncio.CancelledError) as caught:
        _ = [chunk async for chunk in client.stream("hello")]
    assert inner.requested_models == ["qwen-plus"]
    assert ledger.snapshot().llm_requests == 1
    assert ledger.snapshot().input_tokens == 3
    assert ledger.snapshot().output_tokens == 1
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().unknown_usage_requests == 1
    assert ledger.snapshot().unreported_output_tokens == 9999
    assert cast(Any, caught.value).details["provider_usage_complete"] is False


@pytest.mark.asyncio
async def test_closing_team_stream_closes_budgeted_source_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StreamingClient(RecordingInnerModelClient):
        closed = False

        async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AssistantMessage]:
            try:
                yield self._response()
                yield self._response()
            finally:
                self.closed = True

    inner = StreamingClient()
    ledger = _budget()
    stream = _client(monkeypatch, inner, ledger).stream("hello")
    await anext(stream)
    await cast(Any, stream).aclose()
    assert inner.closed is True
    assert ledger.snapshot().input_tokens == 3
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_non_chat_generation_methods_remain_direct_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MediaClient(RecordingInnerModelClient):
        async def generate_image(self, value: str) -> str:
            return f"image:{value}"

        async def generate_speech(self, value: str) -> str:
            return f"speech:{value}"

        async def generate_video(self, value: str) -> str:
            return f"video:{value}"

    ledger = _budget()
    client = _client(monkeypatch, MediaClient(), ledger)
    assert await client.generate_image("sample") == "image:sample"
    assert await client.generate_speech("sample") == "speech:sample"
    assert await client.generate_video("sample") == "video:sample"
    assert ledger.snapshot().llm_requests == 0
