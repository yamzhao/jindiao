"""Transport-independent asynchronous Tianyancha MCP client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, ClassVar, Protocol, TypeVar, cast

from pydantic import Field, JsonValue

from jindiao.application.errors import SourceUnavailableError
from jindiao.contracts.base import ContractModel

from .redaction import redact_sensitive


class McpErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    PROTOCOL = "protocol"
    TOOL_ERROR = "tool_error"


class McpToolDefinition(ContractModel):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, JsonValue]


class McpCallResult(ContractModel):
    text: tuple[str, ...] = ()
    structured_content: dict[str, JsonValue] | None = None


class TianyanchaMcpError(SourceUnavailableError):
    """A sanitized source error classified for retry decisions."""

    _RETRYABLE_KINDS: ClassVar[frozenset[McpErrorKind]] = frozenset(
        {
            McpErrorKind.RATE_LIMIT,
            McpErrorKind.TIMEOUT,
            McpErrorKind.TRANSPORT,
        }
    )

    def __init__(
        self,
        kind: McpErrorKind,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.kind = kind
        safe_details = {"kind": kind.value}
        redacted_details = redact_sensitive(details or {})
        safe_details.update(cast(dict[str, Any], redacted_details))
        super().__init__(cast(str, redact_sensitive(message)), details=safe_details)

    @property
    def retryable(self) -> bool:
        return self.kind in self._RETRYABLE_KINDS


class TianyanchaMcpTransport(Protocol):
    async def list_tools(self) -> tuple[McpToolDefinition, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult: ...

    async def aclose(self) -> None: ...


ResultT = TypeVar("ResultT")


class TianyanchaMcpClient:
    """Apply bounded timeouts and retries around an injectable MCP transport."""

    def __init__(
        self,
        transport: TianyanchaMcpTransport,
        *,
        timeout_seconds: float,
        max_retries: int = 2,
        retry_base_seconds: float = 0.2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._sleep = sleep

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        return await self._execute(self._transport.list_tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        if not name:
            raise ValueError("tool name must not be empty")
        return await self._execute(lambda: self._transport.call_tool(name, arguments))

    async def aclose(self) -> None:
        async with asyncio.timeout(self._timeout_seconds):
            await self._transport.aclose()

    async def _execute(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        for attempt in range(1, self._max_retries + 2):
            error: TianyanchaMcpError
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await operation()
            except TimeoutError:
                error = TianyanchaMcpError(McpErrorKind.TIMEOUT, "Tianyancha MCP timed out")
            except TianyanchaMcpError as caught:
                error = caught
            except Exception:
                error = TianyanchaMcpError(
                    McpErrorKind.TRANSPORT,
                    "Tianyancha MCP transport failed",
                )

            if not error.retryable or attempt > self._max_retries:
                raise TianyanchaMcpError(
                    error.kind,
                    error.message,
                    details=error.details | {"attempts": attempt},
                ) from error
            await self._sleep(self._retry_base_seconds * (2 ** (attempt - 1)))
        raise AssertionError("bounded retry loop exited unexpectedly")


__all__ = [
    "McpCallResult",
    "McpErrorKind",
    "McpToolDefinition",
    "TianyanchaMcpClient",
    "TianyanchaMcpError",
    "TianyanchaMcpTransport",
]
