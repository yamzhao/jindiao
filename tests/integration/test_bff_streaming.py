"""Real loopback sockets: ASGITransport alone cannot prove incremental SSE."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import SecretStr

from jindiao.bff.app import create_app
from jindiao.bff.config import BffSettings
from jindiao.bff.security import hash_password


@contextmanager
def local_server(app: FastAPI, sock: socket.socket) -> Iterator[None]:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=2,
            proxy_headers=False,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Loopback server did not start"
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "Loopback server did not stop"


def loopback_socket() -> socket.socket:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
    except PermissionError:
        sock.close()
        pytest.skip("Sandbox does not allow loopback sockets; rerun with local network permission")
    return sock


@pytest.mark.asyncio
async def test_real_tcp_first_frame_concurrency_and_disconnect_cleanup() -> None:
    upstream = FastAPI()
    closed = threading.Event()
    key = "ephemeral-loopback-api-key-012345"
    prefix = "/runtimes/jindiao-test/invocations/api/v2/due-diligence/runs"
    runs = "/api/v2/due-diligence/runs"

    @upstream.post(prefix)
    async def create(request: Request) -> JSONResponse:
        assert request.headers["authorization"] == f"Bearer {key}"
        return JSONResponse({"run_id": "stream-test"}, status_code=202)

    @upstream.get(prefix + "/stream-test")
    async def status() -> dict[str, str]:
        return {"run_id": "stream-test", "status": "running"}

    @upstream.get(prefix + "/stream-test/events")
    async def events(request: Request) -> StreamingResponse:
        assert request.headers["authorization"] == f"Bearer {key}"

        async def chunks() -> AsyncIterator[bytes]:
            try:
                yield b"id: 1\nevent: run.accepted\ndata: {}\n\n"
                await asyncio.sleep(3600)
            finally:
                closed.set()

        return StreamingResponse(chunks(), media_type="text/event-stream")

    upstream_sock, bff_sock = loopback_socket(), loopback_socket()
    origin = f"http://127.0.0.1:{bff_sock.getsockname()[1]}"
    settings = BffSettings(
        gateway_origin=f"http://127.0.0.1:{upstream_sock.getsockname()[1]}",
        runtime_name="jindiao-test",
        public_origin=origin,
        api_key=SecretStr(key),
        identity_key=SecretStr("ephemeral-loopback-identity-key-012345"),
        development=True,
        users={"alice": hash_password("ephemeral-loopback-password")},
        connections_per_user=1,
    )
    bff = create_app(settings)
    with local_server(upstream, upstream_sock), local_server(bff, bff_sock):
        async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=5) as client:
            login = await client.post(
                "/auth/login",
                headers={"Origin": origin},
                json={
                    "username": "alice",
                    "password": "ephemeral-loopback-password",
                },
            )
            assert login.status_code == 200
            created = await client.post(
                runs,
                headers={
                    "Origin": origin,
                    "X-CSRF-Token": login.json()["csrf_token"],
                },
                json={"enterprise": {"company_name": "本地测试企业"}},
            )
            assert created.status_code == 202
            with anyio.fail_after(3):
                async with client.stream("GET", runs + "/stream-test/events") as stream:
                    assert stream.status_code == 200
                    received = b""
                    iterator = stream.aiter_bytes()
                    async for chunk in iterator:
                        received += chunk
                        if b"\n\n" in received:
                            break
                    assert b"run.accepted" in received and not closed.is_set()
                    limited = await client.get(runs + "/stream-test")
                    assert limited.status_code == 429
            assert await anyio.to_thread.run_sync(closed.wait, 3), "Upstream was not closed"
            # The slot must be reusable after disconnect, without restarting the BFF.
            second = await client.get(runs + "/stream-test")
            assert second.status_code == 200
