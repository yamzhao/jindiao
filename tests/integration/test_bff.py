from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from jindiao.bff.config import BffSettings
from jindiao.bff.security import hash_password

ORIGIN = "https://workbench.example"
KEY = "test-only-cloud-api-key-0123456789"
PASSWORD = "local-test-password-123"
RUNS = "/api/v2/due-diligence/runs"
BODY = {"enterprise": {"company_name": "金调绿洲科技有限公司"}, "scenario_id": "normal-enterprise"}


@pytest.fixture(scope="module")
def users() -> dict[str, str]:
    hashed = hash_password(PASSWORD)
    return {"alice": hashed, "bob": hashed}


@pytest.fixture
def settings(users: dict[str, str]) -> BffSettings:
    return BffSettings(
        gateway_origin="https://gateway.example",
        runtime_name="jindiao-demo",
        api_key=SecretStr(KEY),
        identity_key=SecretStr("test-only-identity-key-" * 2),
        public_origin=ORIGIN,
        users=users,
        create_per_minute=10,
    )


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def handler(calls: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream", "Set-Cookie": "bad=secret"},
                content=b'id: 1\nevent: run.accepted\ndata: {"sequence":1}\n\n'
                b': ping\n\nid: 2\nevent: run.completed\ndata: {"sequence":2}\n\n',
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"run_id": "run-123", "report_markdown": "report"})
        return httpx.Response(
            202 if request.method == "POST" and request.url.path.endswith("/runs") else 200,
            json={
                "run_id": "run-123",
                "status": "accepted",
                "links": {
                    "self": {"href": "https://gateway.example/internal"},
                    "events": {"href": "https://gateway.example/internal/events"},
                },
            },
            headers={"Location": "https://gateway.example/internal", "Set-Cookie": "bad=secret"},
        )

    return respond


def factory(settings: BffSettings, transport: httpx.AsyncBaseTransport) -> Any:
    assert importlib.util.find_spec("jindiao.bff.app") is not None, "BFF app is not implemented"
    from jindiao.bff.app import create_app

    return create_app(settings=settings, transport=transport)


@pytest.fixture
def client(
    settings: BffSettings, handler: Callable[[httpx.Request], httpx.Response]
) -> Iterator[TestClient]:
    with TestClient(factory(settings, httpx.MockTransport(handler)), base_url=ORIGIN) as client:
        yield client


def login(client: TestClient, user: str = "alice") -> str:
    response = client.post(
        "/auth/login", json={"username": user, "password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["csrf_token"])


def test_bff_startup_uses_explicit_system_trust(
    monkeypatch: pytest.MonkeyPatch, settings: BffSettings
) -> None:
    import ssl

    import truststore

    from jindiao.bff.app import create_app

    monkeypatch.setenv("JINDIAO_BFF_TLS_TRUST_STORE", "system")
    configured = BffSettings(**settings.model_dump(exclude={"tls_trust_store"}))
    contexts: list[Any] = []
    client_type = httpx.AsyncClient

    def capture_client(**kwargs: Any) -> httpx.AsyncClient:
        contexts.append(kwargs.get("verify"))
        return client_type(**kwargs)

    monkeypatch.setattr("jindiao.bff.app.httpx.AsyncClient", capture_client)
    with TestClient(create_app(configured), base_url=ORIGIN) as client:
        assert client.get("/healthz").status_code == 200
    assert len(contexts) == 1
    assert isinstance(contexts[0], truststore.SSLContext)
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname


def test_bff_missing_explicit_ca_file_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, settings: BffSettings, tmp_path: Path
) -> None:
    from jindiao.bff.app import create_app

    monkeypatch.setenv("JINDIAO_BFF_TLS_CA_FILE", str(tmp_path / "missing.pem"))
    configured = BffSettings(**settings.model_dump(exclude={"tls_ca_file"}))
    with pytest.raises(FileNotFoundError), TestClient(create_app(configured), base_url=ORIGIN):
        pass


def create(client: TestClient, csrf: str, **kwargs: Any) -> httpx.Response:
    return client.post(RUNS, json=BODY | kwargs, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


def test_anonymous_cannot_use_proxy(client: TestClient, calls: list[httpx.Request]) -> None:
    assert client.post(RUNS, json=BODY).status_code == 401
    assert client.get(f"{RUNS}/run-123/events").status_code == 401
    assert calls == []
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 404


def test_login_cookie_session_csrf_and_logout(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert all(value in cookie for value in ("secure", "httponly", "samesite=strict", "path=/"))
    assert "domain=" not in cookie
    csrf = response.json()["csrf_token"]
    old_cookie = client.cookies.get("__Host-jindiao_session")
    assert client.get("/auth/session").json()["user_id"] == "alice"
    assert client.post(RUNS, json=BODY, headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(
            RUNS, json=BODY, headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf}
        ).status_code
        == 403
    )
    assert (
        client.get("/auth/session", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    response = client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert (
        client.get(
            "/auth/session", headers={"Cookie": f"__Host-jindiao_session={old_cookie}"}
        ).status_code
        == 401
    )


def test_login_requires_origin_and_does_not_echo_secrets(client: TestClient) -> None:
    for user in ("alice", "unknown"):
        response = client.post(
            "/auth/login", json={"username": user, "password": "secret"}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 401
        assert "secret" not in response.text
    assert (
        client.post("/auth/login", json={"username": "alice", "password": PASSWORD}).status_code
        == 403
    )


def test_cloud_headers_body_session_and_links_are_server_owned(
    client: TestClient,
    calls: list[httpx.Request],
) -> None:
    csrf = login(client)
    response = client.post(
        RUNS,
        json=BODY | {"session_id": "forged-session"},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
            "Authorization": "Bearer browser-secret",
            "X-Hw-Agentgateway-User-Id": "victim",
            "X-Hw-Agentarts-Session-Id": "victim-session",
            "X-Forwarded-For": "victim-ip",
            "Idempotency-Key": "create-1",
        },
    )
    assert response.status_code == 202
    request = calls[-1]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-hw-agentgateway-user-id"] != "victim"
    assert request.headers["x-hw-agentarts-session-id"] not in {"forged-session", "victim-session"}
    assert json.loads(request.content)["session_id"] == request.headers["x-hw-agentarts-session-id"]
    assert "cookie" not in request.headers and "x-forwarded-for" not in request.headers
    assert response.headers["location"] == f"{RUNS}/run-123"
    assert response.json()["links"]["events"]["href"] == f"{RUNS}/run-123/events"
    assert "set-cookie" not in response.headers and KEY not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_cross_user_blocked_before_upstream_and_session_survives_relogin(
    client: TestClient,
    calls: list[httpx.Request],
) -> None:
    csrf = login(client)
    assert create(client, csrf).status_code == 202
    session = calls[-1].headers["x-hw-agentarts-session-id"]
    login(client, "bob")
    count = len(calls)
    for suffix in ("", "/events", "/result"):
        assert client.get(f"{RUNS}/run-123{suffix}").status_code == 404
    assert len(calls) == count
    login(client)
    assert client.get(f"{RUNS}/run-123").status_code == 200
    assert calls[-1].headers["x-hw-agentarts-session-id"] == session


def test_sse_cursor_and_result_and_cancel(client: TestClient, calls: list[httpx.Request]) -> None:
    csrf = login(client)
    create(client, csrf)
    events = client.get(f"{RUNS}/run-123/events", headers={"Last-Event-ID": "1"})
    assert events.status_code == 200
    assert "text/event-stream" in events.headers["content-type"]
    assert ": ping" in events.text and "run.completed" in events.text
    assert events.headers["x-accel-buffering"] == "no"
    assert "set-cookie" not in events.headers
    assert calls[-1].headers["last-event-id"] == "1"
    assert client.get(f"{RUNS}/run-123/result").json()["report_markdown"] == "report"
    assert (
        client.post(
            f"{RUNS}/run-123/cancel", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        ).status_code
        == 200
    )


def test_only_allowlisted_routes_queries_and_payloads_reach_cloud(
    client: TestClient,
    calls: list[httpx.Request],
) -> None:
    csrf = login(client)
    for path in ("/invocations", "/proxy", "/admin", f"{RUNS}/run-123/tools"):
        assert client.get(path).status_code == 404
    assert (
        client.post(
            RUNS + "?endpoint=evil", json=BODY, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        ).status_code
        == 422
    )
    assert create(client, csrf, execution_profile="detached").status_code == 422
    assert create(client, csrf, mode="broken").status_code == 422
    assert (
        client.post(
            RUNS,
            content=b"x" * 70000,
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf, "Content-Type": "application/json"},
        ).status_code
        == 413
    )
    assert calls == []


def test_rate_limits_do_not_trust_forwarded_address(client: TestClient) -> None:
    for i in range(10):
        response = client.post(
            "/auth/login",
            json={"username": "alice", "password": "wrong"},
            headers={"Origin": ORIGIN, "X-Forwarded-For": f"1.1.1.{i}"},
        )
        assert response.status_code == 401
    assert (
        client.post(
            "/auth/login",
            json={"username": "alice", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        ).status_code
        == 429
    )


@pytest.mark.parametrize("status", [301, 401, 403, 500, 503])
def test_upstream_failures_never_echo_headers_or_body(settings: BffSettings, status: int) -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, text=KEY, headers={"Location": "https://evil.example", "Set-Cookie": "bad=1"}
        )

    with TestClient(factory(settings, httpx.MockTransport(bad)), base_url=ORIGIN) as client:
        response = create(client, login(client))
        assert response.status_code == 502
        assert KEY not in response.text
        assert "location" not in response.headers and "set-cookie" not in response.headers


def test_upstream_connection_timeout_is_safe(settings: BffSettings) -> None:
    def bad(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(KEY, request=request)

    with TestClient(factory(settings, httpx.MockTransport(bad)), base_url=ORIGIN) as client:
        response = create(client, login(client))
        assert response.status_code == 504 and KEY not in response.text


def test_success_json_cannot_echo_cloud_key(settings: BffSettings) -> None:
    with TestClient(
        factory(
            settings,
            httpx.MockTransport(
                lambda request: httpx.Response(202, json={"run_id": "run-123", "key": KEY})
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        response = create(client, login(client))
        assert response.status_code == 502 and KEY not in response.text


def test_upstream_cookies_are_never_replayed(
    client: TestClient, calls: list[httpx.Request]
) -> None:
    csrf = login(client)
    create(client, csrf)
    client.get(f"{RUNS}/run-123")
    assert all("cookie" not in request.headers for request in calls)


def test_idempotency_is_stable_but_isolated_between_users(
    client: TestClient,
    calls: list[httpx.Request],
) -> None:
    for user in ("alice", "alice", "bob"):
        csrf = login(client, user)
        client.post(
            RUNS,
            json=BODY,
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": "same-key"},
        )
    keys = [request.headers["idempotency-key"] for request in calls]
    assert keys[0] == keys[1] and keys[0] != keys[2]


def test_creation_and_capacity_limits(
    settings: BffSettings,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    limited = settings.model_copy(update={"create_per_minute": 1})
    with TestClient(factory(limited, httpx.MockTransport(handler)), base_url=ORIGIN) as client:
        csrf = login(client)
        assert create(client, csrf).status_code == 202
        assert create(client, csrf).status_code == 429
    limited = settings.model_copy(update={"max_runs": 1})
    with TestClient(factory(limited, httpx.MockTransport(handler)), base_url=ORIGIN) as client:
        csrf = login(client)
        assert create(client, csrf).status_code == 202
        assert create(client, csrf).status_code == 503


@pytest.mark.parametrize(
    "content,content_type",
    [
        (b"x" * 2000, "application/json"),
        (b'{"run_id":"run-123"}', "text/html"),
        (b'{"run_id":"../../etc"}', "application/json"),
    ],
)
def test_unsafe_gateway_json_rejected(
    settings: BffSettings, content: bytes, content_type: str
) -> None:
    limited = settings.model_copy(update={"max_json_bytes": 1024})
    with TestClient(
        factory(
            limited,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    202, content=content, headers={"Content-Type": content_type}
                )
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        assert create(client, login(client)).status_code == 502


@pytest.mark.parametrize(
    "frame",
    [
        b"data: " + KEY.encode() + b"\n\n",
        b"data: " + b"x" * 1100 + b"\n\n",
        b'data: {"value":"' + b"".join(f"\\u{ord(c):04x}".encode() for c in KEY) + b'"}\n\n',
        b"data: truncated",
    ],
)
def test_unsafe_sse_becomes_explicit_error_and_closes(settings: BffSettings, frame: bytes) -> None:
    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> Any:
            # Deliberately split secrets and delimiters across network chunks.
            for i in range(0, len(frame), 7):
                yield frame[i : i + 7]

        async def aclose(self) -> None:
            self.closed = True

    stream = Chunks()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(202, json={"run_id": "run-123"})

    limited = settings.model_copy(update={"max_event_bytes": 1024})
    with TestClient(factory(limited, httpx.MockTransport(respond)), base_url=ORIGIN) as client:
        create(client, login(client))
        response = client.get(f"{RUNS}/run-123/events")
        assert "proxy.error" in response.text and KEY not in response.text
        assert "run.completed" not in response.text and stream.closed


@pytest.mark.parametrize(
    "limit,report_chars,accepted",
    [(262144, 200000, False), (1048576, 200000, True), (1048576, 400000, False)],
)
def test_large_report_sse_respects_explicit_bounded_limit(
    settings: BffSettings, limit: int, report_chars: int, accepted: bool
) -> None:
    # Synthetic public text: exercise UTF-8 bytes, not character count or live data.
    report = {"report_markdown": "证" * report_chars}
    payload = {"sequence": 1, "event_type": "report.completed", "payload": {"result": report}}
    frame = (
        b"id: 1\nevent: report.completed\ndata: "
        + json.dumps(payload, ensure_ascii=False).encode("utf-8")
        + b"\n\n"
    )
    terminal = b'id: 2\nevent: run.partial\ndata: {"sequence":2,"event_type":"run.partial"}\n\n'
    assert (len(frame) <= limit) is accepted

    class Chunks(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> Any:
            body = frame + terminal
            for offset in range(0, len(body), 8191):
                yield body[offset : offset + 8191]

        async def aclose(self) -> None:
            self.closed = True

    stream = Chunks()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            return httpx.Response(200, stream=stream, headers={"Content-Type": "text/event-stream"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json=report)
        return httpx.Response(202, json={"run_id": "run-123"})

    configured = BffSettings(**(settings.model_dump() | {"max_event_bytes": limit}))
    with TestClient(factory(configured, httpx.MockTransport(respond)), base_url=ORIGIN) as client:
        assert create(client, login(client)).status_code == 202
        response = client.get(f"{RUNS}/run-123/events")
        assert response.status_code == 200 and stream.closed
        if accepted:
            assert response.content == frame + terminal
        else:
            expected_error = b'event: proxy.error\ndata: {"code":"stream_interrupted"}\n\n'
            assert response.content == expected_error
        # A transport error must not hide an independently available report.
        result = client.get(f"{RUNS}/run-123/result")
        assert result.status_code == 200 and result.json() == report


def test_wrong_host_and_chunked_oversize_rejected(client: TestClient) -> None:
    csrf = login(client)
    assert client.get("/auth/session", headers={"Host": "evil.example"}).status_code == 400
    response = client.post(
        RUNS,
        content=iter([b"x" * 35000, b"x" * 35000]),
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413


def test_json_read_has_a_total_deadline(settings: BffSettings) -> None:
    class SlowJson(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            for _ in range(20):
                await asyncio.sleep(0.01)
                yield b" "
            yield b'{"run_id":"run-123"}'

    limited = settings.model_copy(update={"upstream_timeout": 0.04})
    with TestClient(
        factory(
            limited,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    202,
                    stream=SlowJson(),
                    headers={"Content-Type": "application/json"},
                )
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        assert create(client, login(client)).status_code == 504


def test_response_headers_have_a_total_deadline(settings: BffSettings) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(202, json={"run_id": "run-123"})

    limited = settings.model_copy(update={"upstream_timeout": 0.04})
    with TestClient(factory(limited, httpx.MockTransport(slow)), base_url=ORIGIN) as client:
        assert create(client, login(client)).status_code == 504


def test_login_rejects_invalid_unicode_without_server_error(client: TestClient) -> None:
    response = client.post(
        "/auth/login",
        content=b'{"username":"alice","password":"\\ud800"}',
        headers={"Origin": ORIGIN, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_cloud_key_check_uses_decoded_values(settings: BffSettings) -> None:
    key = "test-api-key-'\"-0123456789"
    custom = settings.model_copy(update={"api_key": SecretStr(key)})
    with TestClient(
        factory(
            custom,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    202,
                    json={"run_id": "run-123", "nested": [{"secret": key}]},
                )
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        assert create(client, login(client)).status_code == 502


def test_unrequested_compression_rejected_before_decoding(settings: BffSettings) -> None:
    # The proxy requests identity encoding to enforce bounds before decompression.
    compressed = gzip.compress(b'{"run_id":"run-123"}')
    with TestClient(
        factory(
            settings,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    202,
                    content=compressed,
                    headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
                )
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        assert create(client, login(client)).status_code == 502


@pytest.mark.parametrize("value", [b'"\\ud800"', b"NaN"])
def test_unserializable_upstream_json_returns_safe_error(
    settings: BffSettings, value: bytes
) -> None:
    body = b'{"run_id":"run-123","title":' + value + b"}"
    with TestClient(
        factory(
            settings,
            httpx.MockTransport(
                lambda request: httpx.Response(
                    202,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
            ),
        ),
        base_url=ORIGIN,
    ) as client:
        assert create(client, login(client)).status_code == 502
