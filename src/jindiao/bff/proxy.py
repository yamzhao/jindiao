"""Allowlisted gateway transport; never a general-purpose reverse proxy."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from fastapi import HTTPException
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from .config import BffSettings
from .security import derive_id

RUNS = "/api/v2/due-diligence/runs"
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class RunBinding:
    user: str
    expires: float


class OwnedStream(StreamingResponse):
    """Close even when ASGI disconnects before the generator's first iteration."""

    def __init__(self, body: AsyncIterator[bytes], close: Callable[[], Any]) -> None:
        super().__init__(body, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
        self.close = close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.close()


class GatewayProxy:
    def __init__(self, settings: BffSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self.runs: dict[str, RunBinding] = {}
        self.pending_creates = 0
        self.connections: dict[str, int] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        self.runs = {key: value for key, value in self.runs.items() if value.expires > now}

    def require_run(self, run_id: str, user: str) -> None:
        self._prune()
        binding = self.runs.get(run_id)
        if not RUN_ID.fullmatch(run_id) or binding is None or binding.user != user:
            raise HTTPException(404, "Run not found")

    def acquire(self, user: str) -> None:
        if (
            sum(self.connections.values()) >= self.settings.max_connections
            or self.connections.get(user, 0) >= self.settings.connections_per_user
        ):
            raise HTTPException(429, "Too many active requests", headers={"Retry-After": "5"})
        self.connections[user] = self.connections.get(user, 0) + 1

    def release(self, user: str) -> None:
        remaining = self.connections.get(user, 1) - 1
        if remaining:
            self.connections[user] = remaining
        else:
            self.connections.pop(user, None)

    def session_id(self, user: str) -> str:
        return derive_id(self.settings.identity_key.get_secret_value(), "session", user)

    def _request(
        self,
        method: str,
        path: str,
        user: str,
        *,
        payload: dict[str, Any] | None = None,
        cursor: str | None = None,
        after: str | None = None,
        idempotency: str | None = None,
    ) -> httpx.Request:
        key = self.settings.identity_key.get_secret_value()
        headers = {
            "Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
            "X-Hw-Agentgateway-User-Id": derive_id(key, "owner", user),
            "X-Hw-Agentarts-Session-Id": self.session_id(user),
            "Accept": "text/event-stream" if path.endswith("/events") else "application/json",
            "Accept-Encoding": "identity",
        }
        if cursor is not None:
            headers["Last-Event-ID"] = cursor
        if idempotency is not None:
            headers["Idempotency-Key"] = derive_id(key, "idempotency", user + "\0" + idempotency)
        params = {}
        if self.settings.endpoint is not None:
            params["endpoint"] = self.settings.endpoint
        if after is not None:
            params["after"] = after
        # Only private callers pass paths constructed from RUNS and validated run IDs.
        request = self.client.build_request(
            method, self.settings.upstream_base + path, headers=headers, params=params, json=payload
        )
        # AsyncClient maintains a cookie jar even when Set-Cookie is not forwarded
        # to browsers. Never replay one upstream user's cookie for another user.
        request.headers.pop("cookie", None)
        return request

    async def _send(self, request: httpx.Request) -> httpx.Response:
        deadline = time.monotonic() + self.settings.upstream_timeout
        try:
            with anyio.fail_after(self.settings.upstream_timeout):
                response = await self.client.send(request, stream=True)
            self.client.cookies.clear()
            response.extensions["bff_deadline"] = deadline
        except (TimeoutError, httpx.TimeoutException):
            raise HTTPException(504, "Gateway timeout") from None
        except httpx.HTTPError:
            raise HTTPException(502, "Gateway unavailable") from None
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise HTTPException(502, "Unsupported gateway encoding")
        if response.status_code not in {200, 202}:
            await response.aclose()
            status = response.status_code if response.status_code in {404, 409, 422, 429} else 502
            raise HTTPException(status, "Gateway request rejected")
        return response

    def _has_secret(self, value: Any) -> bool:
        # Decode JSON first: escaped characters must not bypass this guard.
        if isinstance(value, str):
            return self.settings.api_key.get_secret_value() in value
        if isinstance(value, dict):
            return any(
                self._has_secret(key) or self._has_secret(item) for key, item in value.items()
            )
        if isinstance(value, list):
            return any(self._has_secret(item) for item in value)
        return False

    async def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            if response.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError("Unexpected content type")
            body = bytearray()
            remaining = response.extensions["bff_deadline"] - time.monotonic()
            with anyio.fail_after(max(0.001, remaining)):
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.settings.max_json_bytes:
                        raise ValueError("Oversized response")
            result = json.loads(body)
            if not isinstance(result, dict) or self._has_secret(result):
                raise ValueError("Unsafe response")
            # Match JSONResponse's encoding rules while errors can still become
            # a generic 502, before registering a Run or returning any content.
            json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            return result
        except (TimeoutError, httpx.TimeoutException):
            raise HTTPException(504, "Gateway timeout") from None
        except (ValueError, httpx.HTTPError, RecursionError):
            raise HTTPException(502, "Invalid gateway response") from None
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()

    @staticmethod
    def browser_view(result: dict[str, Any], run_id: str) -> dict[str, Any]:
        result.pop("owner_id", None)
        result.pop("session_id", None)
        if "links" in result:
            result["links"] = {
                name: {"href": RUNS + "/" + run_id + suffix, "method": method}
                for name, suffix, method in (
                    ("self", "", "GET"),
                    ("events", "/events", "GET"),
                    ("result", "/result", "GET"),
                    ("cancel", "/cancel", "POST"),
                )
            }
        return result

    async def create(
        self,
        user: str,
        payload: dict[str, Any],
        idempotency: str,
    ) -> tuple[int, dict[str, Any]]:
        self._prune()
        if len(self.runs) + self.pending_creates >= self.settings.max_runs:
            raise HTTPException(503, "Run capacity reached")
        self.acquire(user)
        self.pending_creates += 1
        try:
            payload["session_id"] = self.session_id(user)
            response = await self._send(
                self._request("POST", RUNS, user, payload=payload, idempotency=idempotency)
            )
            result = await self._json(response)
            run_id = result.get("run_id")
            if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
                raise HTTPException(502, "Invalid gateway Run")
            existing = self.runs.get(run_id)
            if existing is not None and existing.user != user:
                raise HTTPException(502, "Invalid gateway Run")
            self.runs[run_id] = RunBinding(user, time.monotonic() + self.settings.run_ttl)
            return response.status_code, self.browser_view(result, run_id)
        finally:
            self.pending_creates -= 1
            self.release(user)

    async def query(
        self, user: str, run_id: str, suffix: str, method: str
    ) -> tuple[int, dict[str, Any]]:
        self.require_run(run_id, user)
        self.acquire(user)
        try:
            response = await self._send(self._request(method, f"{RUNS}/{run_id}{suffix}", user))
            result = await self._json(response)
            if "run_id" in result and result["run_id"] != run_id:
                raise HTTPException(502, "Invalid gateway Run")
            return response.status_code, self.browser_view(result, run_id)
        finally:
            self.release(user)

    def _safe_frame(self, frame: bytes) -> None:
        if len(frame) > self.settings.max_event_bytes:
            raise ValueError("Oversized event")
        text = frame.decode("utf-8")
        if self._has_secret(text):
            raise ValueError("Unsafe event")
        data = "\n".join(
            line[5:].lstrip(" ") for line in text.splitlines() if line.startswith("data:")
        )
        if data:
            try:
                value = json.loads(data)
            except json.JSONDecodeError:
                value = data
            if self._has_secret(value):
                raise ValueError("Unsafe event")

    async def events(
        self,
        user: str,
        run_id: str,
        *,
        cursor: str | None,
        after: str | None,
        duration: float,
        authorized: Callable[[], bool],
    ) -> OwnedStream:
        self.require_run(run_id, user)
        self.acquire(user)
        try:
            response = await self._send(
                self._request("GET", f"{RUNS}/{run_id}/events", user, cursor=cursor, after=after)
            )
        except BaseException:
            self.release(user)
            raise
        closed = False

        async def close() -> None:
            nonlocal closed
            if not closed:
                closed = True
                try:
                    await response.aclose()
                finally:
                    self.release(user)

        if response.headers.get("content-type", "").split(";")[0] != "text/event-stream":
            await close()
            raise HTTPException(502, "Invalid gateway event stream")

        async def body() -> AsyncIterator[bytes]:
            buffer = bytearray()
            try:
                with anyio.fail_after(max(0.001, min(duration, self.settings.stream_timeout))):
                    async for chunk in response.aiter_bytes():
                        if not authorized():
                            raise ValueError("Session ended")
                        buffer.extend(chunk)
                        while match := re.search(rb"\r?\n\r?\n", buffer):
                            frame = bytes(buffer[: match.end()])
                            del buffer[: match.end()]
                            self._safe_frame(frame)
                            yield frame
                        if len(buffer) > self.settings.max_event_bytes:
                            raise ValueError("Oversized event")
                    if buffer:
                        raise ValueError("Truncated event")
            except (ValueError, RecursionError, TimeoutError, httpx.HTTPError):
                # Never turn a broken upstream into a successful Run terminal event.
                yield b'event: proxy.error\ndata: {"code":"stream_interrupted"}\n\n'
            finally:
                with anyio.CancelScope(shield=True):
                    await close()

        return OwnedStream(body(), close)
