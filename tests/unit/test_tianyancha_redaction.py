from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from jindiao.application.errors import error_to_record
from jindiao.tianyancha import (
    McpErrorKind,
    StreamableHttpMcpTransport,
    TianyanchaMcpError,
    redact_sensitive,
)
from jindiao.tianyancha import transport as transport_module

DUMMY_AUTH = "mcp-demo_abcdefghijklmnopqrstuvwxyz"
DUMMY_MODEL_KEY = "sk-demo_abcdefghijklmnopqrstuvwxyz"


def test_recursive_redaction_removes_auth_headers_keys_and_tokens() -> None:
    diagnostic = {
        "headers": {"Authorization": DUMMY_AUTH, "X-Trace": "trace-1"},
        "request": f"Authorization={DUMMY_AUTH}",
        "nested": [{"api_key": DUMMY_MODEL_KEY}, f"token: {DUMMY_AUTH}"],
    }

    sanitized = redact_sensitive(diagnostic)
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert DUMMY_AUTH not in encoded
    assert DUMMY_MODEL_KEY not in encoded
    assert "[REDACTED]" in encoded
    assert "trace-1" in encoded


def test_tianyancha_error_and_public_error_record_are_sanitized() -> None:
    error = TianyanchaMcpError(
        McpErrorKind.PROTOCOL,
        f"invalid response Authorization: {DUMMY_AUTH}",
        details={"apiKey": DUMMY_MODEL_KEY, "tool": "search_companies"},
    )

    record = error_to_record(error)
    encoded = record.model_dump_json()

    assert DUMMY_AUTH not in str(error)
    assert DUMMY_AUTH not in encoded
    assert DUMMY_MODEL_KEY not in encoded
    assert record.details["tool"] == "search_companies"


def test_http_transport_classification_does_not_copy_request_headers() -> None:
    request = httpx.Request(
        "POST",
        "https://mcp.example.invalid/v1",
        headers={"Authorization": DUMMY_AUTH},
    )
    response = httpx.Response(401, request=request)
    raw = httpx.HTTPStatusError(
        f"request failed with Authorization={DUMMY_AUTH}",
        request=request,
        response=response,
    )

    classified = StreamableHttpMcpTransport._classify(raw)
    encoded = json.dumps(classified.details)

    assert classified.kind is McpErrorKind.AUTHENTICATION
    assert DUMMY_AUTH not in str(classified)
    assert DUMMY_AUTH not in encoded
    assert classified.details == {"kind": "authentication", "status_code": 401}


def test_http_transport_uses_explicit_proxy_without_ambient_proxy_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_http_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(httpx, "AsyncClient", fake_http_client)
    monkeypatch.setattr(
        transport_module,
        "UrlUtils",
        SimpleNamespace(get_global_proxy_url=lambda _: "http://proxy.example:8080"),
        raising=False,
    )
    transport = StreamableHttpMcpTransport(
        "https://mcp.example.invalid/v1",
        SecretStr(DUMMY_AUTH),
        timeout_seconds=42,
    )

    built = transport._build_http_client(
        headers={"Authorization": DUMMY_AUTH},
        timeout=httpx.Timeout(42),
        auth=None,
    )

    assert built is not None
    assert captured["proxy"] == "http://proxy.example:8080"
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is True
    assert captured["headers"] == {"Authorization": DUMMY_AUTH}
