from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

PREFIX = "/runtimes/jindiao-test/invocations"
AUTH = "Bearer test-only-credential"


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/agentarts_prefix_probe.py"
    spec = importlib.util.spec_from_file_location("prefix_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AGENTARTS_AUTHORIZATION", AUTH)
    return module


def intercept(
    monkeypatch: pytest.MonkeyPatch,
    probe: ModuleType,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    client_type = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if handler is not None:
            return handler(request)
        if request.url.path.endswith("/ping"):
            return httpx.Response(200, json={"status": "Healthy"})
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text='data: {"event_type":"run.accepted","sequence":1}\n\n'
                'data: {"event_type":"run.completed","sequence":2}\n\n',
            )
        return httpx.Response(
            202 if request.method == "POST" and request.url.path.endswith("/runs") else 200,
            json={"run_id": "run-test", "status": "completed", "result_available": True},
        )

    def client(**kwargs: Any) -> httpx.Client:
        return client_type(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(probe.httpx, "Client", client)
    monkeypatch.setattr(
        probe.sys, "argv", ["probe", "https://gateway.example", "--custom-prefix", PREFIX]
    )
    return requests


def test_gateway_health_uses_runtime_prefix(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    requests = intercept(monkeypatch, probe)
    assert probe.main() == 0
    assert requests[0].url.path == f"{PREFIX}/ping"
    assert all(request.url.path.startswith(PREFIX) for request in requests)


def test_gateway_auth_applies_to_every_request_without_printing_secret(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    requests = intercept(monkeypatch, probe)
    assert probe.main() == 0
    assert all(request.headers.get("authorization") == AUTH for request in requests)
    assert AUTH not in capsys.readouterr().out


def test_each_probe_execution_gets_fresh_session_and_idempotency_keys(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    requests = intercept(monkeypatch, probe)
    assert probe.main() == 0
    boundary = len(requests)
    assert probe.main() == 0
    first = requests[:boundary]
    second = requests[boundary:]
    assert (
        first[0].headers["X-Hw-Agentarts-Session-Id"]
        != second[0].headers["X-Hw-Agentarts-Session-Id"]
    )
    assert first[1].headers["Idempotency-Key"] != second[1].headers["Idempotency-Key"]


def test_gateway_missing_credentials_fails_before_network(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    requests = intercept(monkeypatch, probe)
    monkeypatch.delenv("AGENTARTS_AUTHORIZATION")
    assert probe.main() == 1
    assert requests == []


def test_gateway_refuses_plaintext_credentials(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    requests = intercept(monkeypatch, probe)
    monkeypatch.setattr(
        probe.sys, "argv", ["probe", "http://gateway.example", "--custom-prefix", PREFIX]
    )
    assert probe.main() == 1
    assert requests == []


def test_empty_sse_is_not_a_successful_workflow(probe: ModuleType) -> None:
    response = httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text="")
    assert probe._sse_summary(response)["valid"] is False


def test_failure_output_does_not_contain_response_body(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    intercept(monkeypatch, probe, lambda _: httpx.Response(401, text=AUTH))
    assert probe.main() == 1
    output = capsys.readouterr().out
    assert AUTH not in output
    assert json.loads(output)["ping"] == 401


def test_gateway_can_load_authorization_from_explicit_env_file(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType, tmp_path: Path
) -> None:
    requests = intercept(monkeypatch, probe)
    monkeypatch.delenv("AGENTARTS_AUTHORIZATION")
    env_file = tmp_path / ".env.gateway"
    env_file.write_text(f"AGENTARTS_AUTHORIZATION={AUTH}\n", encoding="utf-8")
    monkeypatch.setattr(probe.sys, "argv", [*probe.sys.argv, "--env-file", str(env_file)])
    assert probe.main() == 0
    assert all(request.headers.get("authorization") == AUTH for request in requests)


def test_raw_api_key_is_normalized_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    requests = intercept(monkeypatch, probe)
    monkeypatch.setenv("AGENTARTS_AUTHORIZATION", AUTH.removeprefix("Bearer "))
    assert probe.main() == 0
    assert all(request.headers.get("authorization") == AUTH for request in requests)
    assert AUTH.removeprefix("Bearer ") not in capsys.readouterr().out


def test_fixed_endpoint_applies_to_every_gateway_request(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    requests = intercept(monkeypatch, probe)
    monkeypatch.setattr(probe.sys, "argv", [*probe.sys.argv, "--endpoint", "Latest"])
    assert probe.main() == 0
    assert all(request.url.params.get("endpoint") == "Latest" for request in requests)


def test_system_trust_option_preserves_certificate_validation(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    intercept(monkeypatch, probe)
    monkeypatch.setattr(probe.sys, "argv", [*probe.sys.argv, "--tls-trust-store", "system"])
    assert probe.main() == 0


def test_malformed_api_key_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    requests = intercept(monkeypatch, probe)
    secret = AUTH + "\r\nunsafe-header"
    monkeypatch.setenv("AGENTARTS_AUTHORIZATION", secret)
    assert probe.main() == 1
    assert requests == []
    assert AUTH not in capsys.readouterr().out


def test_probe_supports_explicit_certificate_file(
    monkeypatch: pytest.MonkeyPatch, probe: ModuleType
) -> None:
    intercept(monkeypatch, probe)
    monkeypatch.setattr(
        probe.sys,
        "argv",
        [*probe.sys.argv, "--tls-ca-file", "deploy/bff/certs/globalsign-rsa-ov-2018.pem"],
    )
    assert probe.main() == 0
