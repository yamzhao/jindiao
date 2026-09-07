from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import AliasChoices, SecretStr, ValidationError

from jindiao.application.settings import Settings


def _clear_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        if not isinstance(alias, AliasChoices):
            continue
        for choice in alias.choices:
            if isinstance(choice, str) and choice.isupper():
                monkeypatch.delenv(choice, raising=False)


def test_settings_have_safe_single_machine_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment == "development"
    assert settings.agent_runtime_mode == "deterministic_harness"
    assert settings.formal_agent_run is False
    assert settings.model_provider == "openai_compatible"
    assert settings.model_temperature == 0
    assert settings.data_source_mode == "mock"
    assert settings.tianyancha_mcp_url == "https://mcp.tianyancha.com/v1"
    assert settings.tianyancha_annual_report_enabled is True
    assert settings.tianyancha_annual_report_lookback_years == 5
    assert settings.tianyancha_annual_report_timeout_seconds == 15
    assert settings.tianyancha_routes_path == Path("config/tianyancha-capability-routes.json")
    assert settings.live_fallback_scenario_id == "normal-enterprise"
    assert settings.mock_data_root == Path("mock_data")
    assert settings.artifact_root == Path("artifacts")
    assert settings.max_concurrency == 4
    assert settings.request_timeout_seconds == 300
    assert settings.max_tool_calls == 60
    assert settings.max_repair_rounds == 2
    assert settings.max_llm_requests == 64
    assert settings.max_input_tokens == 300_000
    assert settings.max_output_tokens == 100_000
    assert settings.max_total_tokens == 400_000
    assert settings.max_schema_retries == 4
    assert settings.max_snapshot_reads == 240
    assert settings.skill_evolution_auto_approve is False


def test_settings_read_public_configuration_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.setenv("MODEL_NAME", "competition-model")
    monkeypatch.setenv("JINDIAO_MAX_CONCURRENCY", "6")
    monkeypatch.setenv("JINDIAO_MAX_TOOL_CALLS", "42")
    monkeypatch.setenv("TIANYANCHA_MCP_URL", "https://example.invalid/mcp")
    monkeypatch.setenv("TIANYANCHA_MCP_AUTHORIZATION", "secret-token")
    monkeypatch.setenv("JINDIAO_TIANYANCHA_ANNUAL_REPORT_ENABLED", "false")
    monkeypatch.setenv("JINDIAO_TIANYANCHA_ANNUAL_REPORT_LOOKBACK_YEARS", "3")
    monkeypatch.setenv("JINDIAO_TIANYANCHA_ANNUAL_REPORT_TIMEOUT_SECONDS", "12")

    settings = Settings()

    assert settings.model_name == "competition-model"
    assert settings.max_concurrency == 6
    assert settings.max_tool_calls == 42
    assert settings.tianyancha_mcp_url == "https://example.invalid/mcp"
    assert settings.tianyancha_authorization is not None
    assert settings.tianyancha_authorization.get_secret_value() == "secret-token"
    assert settings.tianyancha_annual_report_enabled is False
    assert settings.tianyancha_annual_report_lookback_years == 3
    assert settings.tianyancha_annual_report_timeout_seconds == 12
    assert "secret-token" not in repr(settings)


def test_explicit_constructor_values_override_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "MODEL_PROVIDER=OpenAI\nMODEL_NAME=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings(
        model_provider="offline_mock",
        model_name="explicit-model",
    )

    assert settings.model_provider == "offline_mock"
    assert settings.model_name == "explicit-model"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_concurrency", 0),
        ("request_timeout_seconds", 0),
        ("max_tool_calls", 0),
        ("max_repair_rounds", -1),
        ("max_llm_requests", 0),
        ("max_input_tokens", 0),
        ("max_output_tokens", 0),
        ("max_total_tokens", 0),
        ("max_schema_retries", -1),
        ("max_snapshot_reads", 0),
        ("model_temperature", 2.1),
        ("tianyancha_annual_report_lookback_years", 0),
        ("tianyancha_annual_report_timeout_seconds", 0),
    ],
)
def test_settings_reject_invalid_runtime_limits(field: str, value: int | float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_settings_require_authorization_for_explicit_tianyancha_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError, match="TIANYANCHA_MCP_AUTHORIZATION"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            data_source_mode="tianyancha",
        )


def test_formal_agent_runtime_requires_complete_non_fake_model_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="formal agent runtime requires"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            agent_runtime_mode="formal",
            model_provider="openai_compatible",
            model_name="model-a",
        )
    with pytest.raises(ValidationError, match="cannot use offline_mock"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            agent_runtime_mode="formal",
            model_provider="offline_mock",
            model_name="fake",
            model_base_url="https://example.invalid/v1",
            model_api_key=SecretStr("test-only"),
        )

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        agent_runtime_mode="formal",
        model_provider="openai_compatible",
        model_name="model-a",
        model_base_url="https://example.invalid/v1",
        model_api_key=SecretStr("test-only"),
    )
    assert settings.formal_agent_run is True
