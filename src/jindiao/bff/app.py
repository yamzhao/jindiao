"""Browser BFF factory. Run one worker, separately from the agent runtime."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jindiao.contracts.runs import ExecutionProfile, RunCreateRequest
from jindiao.security.tls import create_tls_context

from .config import BffSettings
from .proxy import RUNS, GatewayProxy
from .security import Session, SessionStore, WindowLimiter, hash_password, verify_password


class BrowserBoundary:
    def __init__(self, app: ASGIApp, origin: str) -> None:
        self.app = app
        self.origin = origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
        if scope["path"] != "/healthz" and host != urlsplit(self.origin).netloc:
            await JSONResponse({"detail": "Invalid host"}, status_code=400)(scope, receive, send)
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ]
                if self.origin.startswith("https:"):
                    message["headers"].append((b"strict-transport-security", b"max-age=31536000"))
            await send(message)

        await self.app(scope, receive, secure_send)


async def read_json(request: Request, limit: int) -> dict[str, Any]:
    body = bytearray()
    try:
        with anyio.fail_after(10):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > limit:
                    raise HTTPException(413, "Request body too large")
    except TimeoutError:
        raise HTTPException(408, "Request body timeout") from None
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON required")
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        # JSON permits escaped lone surrogates; Python UTF-8 hashing/transport
        # does not. Reject them at the input boundary instead of returning 500.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return value
    except (ValueError, RecursionError):
        raise HTTPException(422, "Invalid JSON") from None


def create_app(
    settings: BffSettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings if settings is not None else BffSettings()  # type: ignore[call-arg]
    sessions = SessionStore(ttl=config.session_ttl, capacity=config.max_sessions)
    limiter = WindowLimiter()
    dummy_hash = hash_password(secrets.token_urlsafe(32))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.password_limiter = anyio.CapacityLimiter(2)
        async with httpx.AsyncClient(
            transport=transport,
            verify=create_tls_context(config.tls_trust_store, ca_file=config.tls_ca_file),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(config.upstream_timeout, connect=10, pool=5),
            limits=httpx.Limits(
                max_connections=config.max_connections,
                max_keepalive_connections=config.max_connections,
            ),
        ) as client:
            app.state.gateway = GatewayProxy(config, client)
            yield

    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False
    )
    app.add_middleware(BrowserBoundary, origin=config.public_origin)

    def origin_guard(request: Request, *, required: bool) -> None:
        origin = request.headers.get("origin")
        if (
            (required or origin is not None) and origin != config.public_origin
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Same-origin request required")

    def authorize(request: Request) -> Session:
        session = sessions.get(request.cookies.get(config.cookie_name, ""))
        if session is None:
            raise HTTPException(401, "Authentication required")
        modifying = request.method not in {"GET", "HEAD"}
        origin_guard(request, required=modifying)
        if modifying and not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(),
            session.csrf.encode(),
        ):
            raise HTTPException(403, "Invalid CSRF token")
        if not limiter.allow("requests:" + session.user, maximum=config.requests_per_minute):
            raise HTTPException(429, "Request rate exceeded", headers={"Retry-After": "60"})
        return session

    def query_guard(request: Request, allowed: set[str] | None = None) -> None:
        keys = list(request.query_params.keys())
        if set(keys) - (allowed or set()) or len(request.query_params.multi_items()) != len(keys):
            raise HTTPException(422, "Unsupported query")

    def session_view(session: Session) -> dict[str, Any]:
        return {
            "user_id": session.user,
            "csrf_token": session.csrf,
            "expires_at": session.expires_at,
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login")
    async def login(request: Request) -> Response:
        origin_guard(request, required=True)
        query_guard(request)
        ip = request.client.host if request.client is not None else "unknown"
        if not limiter.allow("login:global", maximum=60) or not limiter.allow(
            "login:" + ip, maximum=10
        ):
            raise HTTPException(429, "Login rate exceeded", headers={"Retry-After": "60"})
        payload = await read_json(request, 4096)
        user, password = payload.get("username"), payload.get("password")
        if (
            set(payload) != {"username", "password"}
            or not isinstance(user, str)
            or not isinstance(password, str)
            or len(user) > 64
            or len(password) > 256
        ):
            raise HTTPException(422, "Invalid login request")
        hashed = config.users.get(user, dummy_hash)
        valid = await anyio.to_thread.run_sync(
            verify_password, password, hashed, limiter=request.app.state.password_limiter
        )
        if not valid or user not in config.users:
            raise HTTPException(401, "Invalid username or password")
        sessions.revoke(request.cookies.get(config.cookie_name, ""))
        try:
            token, session = sessions.create(user)
        except OverflowError:
            raise HTTPException(503, "Session capacity reached") from None
        response = JSONResponse(session_view(session))
        response.set_cookie(
            config.cookie_name,
            token,
            max_age=config.session_ttl,
            httponly=True,
            secure=config.public_origin.startswith("https:"),
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/auth/session")
    async def session_info(request: Request) -> dict[str, Any]:
        session = authorize(request)
        query_guard(request)
        return session_view(session)

    @app.post("/auth/logout")
    async def logout(request: Request) -> Response:
        authorize(request)
        query_guard(request)
        sessions.revoke(request.cookies.get(config.cookie_name, ""))
        response = Response(status_code=204)
        response.delete_cookie(
            config.cookie_name,
            path="/",
            httponly=True,
            samesite="strict",
            secure=config.public_origin.startswith("https:"),
        )
        return response

    @app.post(RUNS)
    async def create_run(request: Request) -> Response:
        session = authorize(request)
        query_guard(request)
        payload = await read_json(request, config.max_body_bytes)
        # Never allow the caller to choose a cloud routing session.
        payload.pop("session_id", None)
        try:
            parsed = RunCreateRequest.model_validate(payload)
        except ValidationError:
            raise HTTPException(422, "Invalid Run request") from None
        if parsed.execution_profile is not ExecutionProfile.ATTACHED:
            raise HTTPException(422, "Only attached Runs are enabled")
        idempotency = request.headers.get("idempotency-key", secrets.token_urlsafe(32))
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency):
            raise HTTPException(422, "Invalid idempotency key")
        if not limiter.allow("create:" + session.user, maximum=config.create_per_minute):
            raise HTTPException(429, "Run creation rate exceeded", headers={"Retry-After": "60"})
        gateway: GatewayProxy = request.app.state.gateway
        status, result = await gateway.create(
            session.user, parsed.model_dump(mode="json"), idempotency
        )
        return JSONResponse(
            result, status_code=status, headers={"Location": f"{RUNS}/{result['run_id']}"}
        )

    async def run_json(request: Request, run_id: str, suffix: str = "") -> Response:
        session = authorize(request)
        query_guard(request)
        gateway: GatewayProxy = request.app.state.gateway
        status, result = await gateway.query(session.user, run_id, suffix, request.method)
        return JSONResponse(result, status_code=status)

    @app.get(RUNS + "/{run_id}")
    async def get_run(request: Request, run_id: str) -> Response:
        return await run_json(request, run_id)

    @app.get(RUNS + "/{run_id}/result")
    async def get_result(request: Request, run_id: str) -> Response:
        return await run_json(request, run_id, "/result")

    @app.post(RUNS + "/{run_id}/cancel")
    async def cancel_run(request: Request, run_id: str) -> Response:
        return await run_json(request, run_id, "/cancel")

    @app.get(RUNS + "/{run_id}/events")
    async def get_events(request: Request, run_id: str) -> Response:
        session = authorize(request)
        query_guard(request, {"after"})
        cursor, after = request.headers.get("last-event-id"), request.query_params.get("after")
        for value in (cursor, after):
            if value is not None and not re.fullmatch(r"[0-9]{1,19}", value):
                raise HTTPException(422, "Invalid event cursor")
        gateway: GatewayProxy = request.app.state.gateway
        return await gateway.events(
            session.user,
            run_id,
            cursor=cursor,
            after=after,
            duration=session.expires_at - time.time(),
            authorized=lambda: (
                sessions.get(request.cookies.get(config.cookie_name, "")) is not None
            ),
        )

    return app
