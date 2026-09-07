from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from jindiao.tianyancha import StreamableHttpMcpTransport
from jindiao.tianyancha import transport as transport_module


@pytest.mark.asyncio
async def test_streamable_session_is_closed_by_the_task_that_opened_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered_by: list[asyncio.Task[Any] | None] = []
    exited_by: list[asyncio.Task[Any] | None] = []

    @asynccontextmanager
    async def fake_streamable_client(
        *args: object, **kwargs: object
    ) -> AsyncIterator[tuple[object, object, Callable[[], None]]]:
        del args, kwargs
        entered_by.append(asyncio.current_task())
        try:
            yield object(), object(), lambda: None
        finally:
            exited_by.append(asyncio.current_task())
            if exited_by[-1] is not entered_by[-1]:
                raise RuntimeError("session context exited from another task")

    class FakeClientSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(transport_module, "streamablehttp_client", fake_streamable_client)
    monkeypatch.setattr(transport_module, "ClientSession", FakeClientSession)
    transport = StreamableHttpMcpTransport(
        "https://mcp.example.invalid/v1",
        SecretStr("test-authorization"),
        timeout_seconds=5,
    )

    assert await asyncio.create_task(transport.list_tools()) == ()
    await transport.aclose()

    assert entered_by == exited_by
