"""Official MCP SDK transport for Tianyancha streamable HTTP."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any, cast

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent
from openjiuwen.core.common.security.url_utils import UrlUtils
from pydantic import JsonValue, SecretStr

from .client import (
    McpCallResult,
    McpErrorKind,
    McpToolDefinition,
    TianyanchaMcpError,
)


class StreamableHttpMcpTransport:
    """Maintain one initialized MCP session without exposing SDK objects upstream."""

    def __init__(
        self,
        url: str,
        authorization: SecretStr,
        *,
        timeout_seconds: float,
    ) -> None:
        self._url = url
        self._authorization = authorization
        self._timeout_seconds = timeout_seconds
        self._session: ClientSession | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._session_ready: asyncio.Future[ClientSession] | None = None
        self._stop_requested: asyncio.Event | None = None
        self._connect_lock = asyncio.Lock()

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        session = await self._ensure_session()
        try:
            result = await session.list_tools()
        except Exception as error:
            raise self._classify(error) from error
        return tuple(
            McpToolDefinition(
                name=tool.name,
                description=tool.description or "",
                input_schema=cast(dict[str, JsonValue], tool.inputSchema),
            )
            for tool in result.tools
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        session = await self._ensure_session()
        try:
            result = await session.call_tool(
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
            )
        except Exception as error:
            raise self._classify(error) from error
        if result.isError:
            raise TianyanchaMcpError(
                McpErrorKind.TOOL_ERROR,
                "Tianyancha MCP tool returned an error",
                details={"tool": name},
            )
        text = tuple(item.text for item in result.content if isinstance(item, TextContent))
        structured = (
            cast(dict[str, JsonValue], result.structuredContent)
            if result.structuredContent is not None
            else None
        )
        return McpCallResult(text=text, structured_content=structured)

    async def aclose(self) -> None:
        async with self._connect_lock:
            owner_task = self._owner_task
            stop_requested = self._stop_requested
            if stop_requested is not None:
                stop_requested.set()
        if owner_task is not None:
            await owner_task
        async with self._connect_lock:
            if self._owner_task is owner_task:
                self._owner_task = None
                self._session_ready = None
                self._stop_requested = None

    async def _ensure_session(self) -> ClientSession:
        if self._session is not None:
            return self._session
        ready: asyncio.Future[ClientSession] | None
        async with self._connect_lock:
            if self._session is not None:
                return self._session
            if self._owner_task is None or self._owner_task.done():
                loop = asyncio.get_running_loop()
                ready = loop.create_future()
                stop_requested = asyncio.Event()
                self._session_ready = ready
                self._stop_requested = stop_requested
                self._owner_task = asyncio.create_task(
                    self._run_session_owner(ready, stop_requested),
                    name="jindiao-tianyancha-mcp-session",
                )
            ready = self._session_ready
        if ready is None:  # pragma: no cover - protected by the connection lock
            raise RuntimeError("MCP session owner did not publish a readiness future")
        return await asyncio.shield(ready)

    async def _run_session_owner(
        self,
        ready: asyncio.Future[ClientSession],
        stop_requested: asyncio.Event,
    ) -> None:
        """Own MCP context entry and exit in one asyncio task."""

        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamablehttp_client(
                        self._url,
                        headers={"Authorization": self._authorization.get_secret_value()},
                        timeout=self._timeout_seconds,
                        sse_read_timeout=self._timeout_seconds,
                        httpx_client_factory=self._build_http_client,
                    )
                )
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self._timeout_seconds),
                    )
                )
                await session.initialize()
                self._session = session
                if not ready.done():
                    ready.set_result(session)
                await stop_requested.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except Exception as error:
            classified = self._classify(error)
            if not ready.done():
                ready.set_exception(classified)
                return
            raise classified from error
        finally:
            self._session = None

    def _build_http_client(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """Build the SDK client without re-parsing ambient proxy bypasses."""

        return httpx.AsyncClient(
            follow_redirects=True,
            headers=headers,
            timeout=timeout,
            auth=auth,
            proxy=UrlUtils.get_global_proxy_url(self._url),
            trust_env=False,
        )

    @staticmethod
    def _classify(error: Exception) -> TianyanchaMcpError:
        if isinstance(error, TianyanchaMcpError):
            return error
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status in {401, 403}:
                kind = McpErrorKind.AUTHENTICATION
                message = "Tianyancha MCP authentication failed"
            elif status == 429:
                kind = McpErrorKind.RATE_LIMIT
                message = "Tianyancha MCP rate limited"
            else:
                kind = McpErrorKind.TRANSPORT
                message = "Tianyancha MCP HTTP request failed"
            return TianyanchaMcpError(kind, message, details={"status_code": status})
        if isinstance(error, httpx.TimeoutException | TimeoutError):
            return TianyanchaMcpError(McpErrorKind.TIMEOUT, "Tianyancha MCP timed out")
        return TianyanchaMcpError(
            McpErrorKind.TRANSPORT,
            "Tianyancha MCP transport failed",
        )


__all__ = ["StreamableHttpMcpTransport"]
