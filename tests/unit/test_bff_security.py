from __future__ import annotations

import importlib.util
from typing import Any

import pytest
from pydantic import ValidationError


def bff_modules() -> tuple[Any, Any]:
    assert importlib.util.find_spec("jindiao.bff") is not None, "BFF package is not implemented"
    from jindiao.bff import config, security

    return config, security


def test_password_hash_and_configuration_fail_closed() -> None:
    config, security = bff_modules()
    hashed = security.hash_password("local-test-password-123")
    assert security.verify_password("local-test-password-123", hashed)
    assert not security.verify_password("wrong-password", hashed)
    assert not security.verify_password("local-test-password-123", "broken")
    assert "local-test-password-123" not in hashed
    values = {
        "gateway_origin": "https://gateway.example",
        "runtime_name": "jindiao-demo",
        "api_key": "test-cloud-key-123456789",
        "identity_key": "test-identity-key-" * 3,
        "public_origin": "https://workbench.example",
        "users": {"alice": hashed},
    }
    settings = config.BffSettings(_env_file=None, **values)
    assert settings.cookie_name == "__Host-jindiao_session"
    assert "test-cloud-key-123456789" not in repr(settings)
    invalid_values: tuple[dict[str, Any], ...] = (
        {"gateway_origin": "http://gateway.example"},
        {"gateway_origin": "https://gateway.example/path"},
        {"gateway_origin": "https://user:secret@gateway.example"},
        {"public_origin": "http://workbench.example"},
        {"api_key": ""},
        {"api_key": "test-key-with-newline\r\n"},
        {"api_key": "x" * 20 + "\x00"},
        {"api_key": "x" * 20 + "密"},
        {"users": {}},
        {"users": {"alice": "plaintext-password"}},
        {"runtime_name": "../../admin"},
        {"endpoint": "default&x=y"},
    )
    for overrides in invalid_values:
        with pytest.raises(ValidationError) as error:
            config.BffSettings(_env_file=None, **(values | overrides))
        assert "test-cloud-key-123456789" not in str(error.value)


def test_password_cli_does_not_echo_plaintext(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert importlib.util.find_spec("jindiao.bff.password") is not None, "Password CLI missing"
    import json

    from jindiao.bff.password import main
    from jindiao.bff.security import verify_password

    monkeypatch.setattr("sys.argv", ["password", "alice"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: "test-cli-password-1234")
    main()
    output = capsys.readouterr().out
    assert "test-cli-password-1234" not in output
    assert verify_password("test-cli-password-1234", json.loads(output)["alice"])


def test_http_exception_is_only_for_explicit_loopback_development() -> None:
    config, security = bff_modules()
    values = {
        "gateway_origin": "http://127.0.0.1:8080",
        "runtime_name": "test-runtime",
        "api_key": "test-cloud-key-123456789",
        "identity_key": "test-identity-key-" * 3,
        "public_origin": "http://127.0.0.1:18082",
        "users": {"alice": security.hash_password("local-test-password-123")},
    }
    with pytest.raises(ValidationError):
        config.BffSettings(_env_file=None, **values)
    settings = config.BffSettings(_env_file=None, development=True, **values)
    assert settings.cookie_name == "jindiao_dev_session"
    with pytest.raises(ValidationError):
        config.BffSettings(
            _env_file=None, development=True, **(values | {"public_origin": "http://0.0.0.0"})
        )


def test_session_expiry_revocation_and_capacity() -> None:
    _, security = bff_modules()
    store = security.SessionStore(ttl=60, capacity=2)
    token, session = store.create("alice", now=100)
    assert len(token) >= 40
    assert store.get(token, now=101) == session
    assert store.get(token + "x", now=101) is None
    assert store.get(token, now=160) is None
    other, _ = store.create("bob", now=200)
    store.revoke(other)
    assert store.get(other, now=201) is None
    store.create("alice", now=300)
    store.create("bob", now=300)
    with pytest.raises(OverflowError):
        store.create("carol", now=300)


def test_fixed_window_limits_and_bounded_registry() -> None:
    _, security = bff_modules()
    limit = security.WindowLimiter(capacity=2)
    assert limit.allow("alice", maximum=2, window=60, now=0)
    assert limit.allow("alice", maximum=2, window=60, now=1)
    assert not limit.allow("alice", maximum=2, window=60, now=2)
    assert limit.allow("bob", maximum=2, window=60, now=2)
    assert not limit.allow("carol", maximum=2, window=60, now=3)
    assert limit.allow("alice", maximum=2, window=60, now=61)


def test_identity_is_stable_separated_and_opaque() -> None:
    _, security = bff_modules()
    key = "test-identity-key-" * 3
    owner = security.derive_id(key, "owner", "alice")
    session = security.derive_id(key, "session", "alice")
    assert owner == security.derive_id(key, "owner", "alice")
    assert owner != session != security.derive_id(key, "session", "bob")
    assert "alice" not in owner
    assert len(session) <= 64
