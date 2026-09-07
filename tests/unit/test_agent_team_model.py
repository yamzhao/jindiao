from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from jindiao.orchestration.agent_team_model import BudgetedAgentTeamModelClient
from jindiao.orchestration.base import BudgetLedger, RunBudget


class RecordingInnerModelClient:
    def __init__(self) -> None:
        self.requested_models: list[str | None] = []

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
        return self._response()

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[AssistantMessage]:
        del args
        self.requested_models.append(kwargs.get("model"))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["invoke", "stream"])
async def test_budgeted_team_model_replaces_member_alias_with_upstream_model(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    inner = RecordingInnerModelClient()
    ledger = _budget()
    monkeypatch.setattr(
        "jindiao.orchestration.agent_team_model.create_model_client",
        lambda **kwargs: inner,
    )
    monkeypatch.setattr(
        BudgetedAgentTeamModelClient,
        "_ledger",
        property(lambda self: ledger),
    )
    client = BudgetedAgentTeamModelClient(
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

    if method == "invoke":
        await client.invoke([], model="qwen-plus::leader")
    else:
        assert [item async for item in client.stream([], model="qwen-plus::leader")]

    assert inner.requested_models == ["qwen-plus"]
    assert ledger.snapshot().successful_llm_requests == 1
