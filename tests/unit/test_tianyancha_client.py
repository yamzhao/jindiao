from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from jindiao.tianyancha import (
    McpCallResult,
    McpErrorKind,
    McpToolDefinition,
    TianyanchaMcpClient,
    TianyanchaMcpError,
)


class ScriptedTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        return cast(tuple[McpToolDefinition, ...], await self._next())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        assert name
        assert arguments is not None
        return cast(McpCallResult, await self._next())

    async def aclose(self) -> None:
        return None

    async def _next(self) -> object:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def no_wait(_: float) -> None:
    return None


def make_client(
    transport: ScriptedTransport,
    *,
    timeout_seconds: float = 1,
    max_retries: int = 1,
    sleep: Callable[[float], Awaitable[None]] = no_wait,
) -> TianyanchaMcpClient:
    return TianyanchaMcpClient(
        transport,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_base_seconds=0.01,
        sleep=sleep,
    )


@pytest.mark.asyncio
async def test_client_returns_transport_independent_tool_contracts() -> None:
    tool = McpToolDefinition(name="search_company", description="搜索企业", input_schema={})
    transport = ScriptedTransport([(tool,)])

    tools = await make_client(transport).list_tools()

    assert tools == (tool,)
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_client_retries_transient_failure_with_a_hard_limit() -> None:
    transient = TianyanchaMcpError(McpErrorKind.RATE_LIMIT, "MCP rate limited")
    result = McpCallResult(text=("ok",), structured_content={"items": []})
    transport = ScriptedTransport([transient, result])

    response = await make_client(transport).call_tool("search_company", {"keyword": "示例"})

    assert response == result
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_client_does_not_retry_authentication_or_protocol_errors() -> None:
    auth_error = TianyanchaMcpError(McpErrorKind.AUTHENTICATION, "MCP authentication failed")
    transport = ScriptedTransport([auth_error])

    with pytest.raises(TianyanchaMcpError) as captured:
        await make_client(transport, max_retries=3).list_tools()

    assert captured.value.kind is McpErrorKind.AUTHENTICATION
    assert captured.value.details["attempts"] == 1
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_client_classifies_timeout_and_stops_after_retry_budget() -> None:
    class SlowTransport(ScriptedTransport):
        async def list_tools(self) -> tuple[McpToolDefinition, ...]:
            self.calls += 1
            await asyncio.sleep(0.05)
            return ()

    transport = SlowTransport([])

    with pytest.raises(TianyanchaMcpError) as captured:
        await make_client(transport, timeout_seconds=0.001).list_tools()

    assert captured.value.kind is McpErrorKind.TIMEOUT
    assert captured.value.details["attempts"] == 2
    assert transport.calls == 2
