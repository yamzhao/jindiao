"""Central runtime configuration for Jindiao."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        protected_namespaces=(),
    )

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("environment", "JINDIAO_ENV"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("log_level", "JINDIAO_LOG_LEVEL"),
    )

    model_provider: str = Field(
        default="openai_compatible",
        validation_alias=AliasChoices("model_provider", "MODEL_PROVIDER"),
    )
    model_name: str = Field(
        default="",
        validation_alias=AliasChoices("model_name", "MODEL_NAME"),
    )
    model_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("model_base_url", "MODEL_BASE_URL"),
    )
    model_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("model_api_key", "MODEL_API_KEY"),
    )
    model_temperature: float = Field(
        default=0,
        ge=0,
        le=2,
        validation_alias=AliasChoices("model_temperature", "MODEL_TEMPERATURE"),
    )
    agent_runtime_mode: Literal["formal", "deterministic_harness"] = Field(
        default="deterministic_harness",
        validation_alias=AliasChoices(
            "agent_runtime_mode",
            "JINDIAO_AGENT_RUNTIME_MODE",
        ),
    )

    tianyancha_mcp_url: str = Field(
        default="https://mcp.tianyancha.com/v1",
        validation_alias=AliasChoices("tianyancha_mcp_url", "TIANYANCHA_MCP_URL"),
    )
    tianyancha_authorization: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "tianyancha_authorization",
            "TIANYANCHA_MCP_AUTHORIZATION",
        ),
    )
    tianyancha_annual_report_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "tianyancha_annual_report_enabled",
            "JINDIAO_TIANYANCHA_ANNUAL_REPORT_ENABLED",
        ),
    )
    tianyancha_annual_report_lookback_years: int = Field(
        default=5,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "tianyancha_annual_report_lookback_years",
            "JINDIAO_TIANYANCHA_ANNUAL_REPORT_LOOKBACK_YEARS",
        ),
    )
    tianyancha_annual_report_timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=60,
        validation_alias=AliasChoices(
            "tianyancha_annual_report_timeout_seconds",
            "JINDIAO_TIANYANCHA_ANNUAL_REPORT_TIMEOUT_SECONDS",
        ),
    )
    data_source_mode: Literal["mock", "tianyancha"] = Field(
        default="mock",
        validation_alias=AliasChoices("data_source_mode", "JINDIAO_DATA_SOURCE_MODE"),
    )
    tianyancha_routes_path: Path = Field(
        default=Path("config/tianyancha-capability-routes.json"),
        validation_alias=AliasChoices(
            "tianyancha_routes_path",
            "JINDIAO_TIANYANCHA_ROUTES_PATH",
        ),
    )
    live_fallback_scenario_id: str = Field(
        default="normal-enterprise",
        min_length=1,
        validation_alias=AliasChoices(
            "live_fallback_scenario_id",
            "JINDIAO_LIVE_FALLBACK_SCENARIO_ID",
        ),
    )

    mock_data_root: Path = Field(
        default=Path("mock_data"),
        validation_alias=AliasChoices("mock_data_root", "JINDIAO_MOCK_DATA_ROOT"),
    )
    artifact_root: Path = Field(
        default=Path("artifacts"),
        validation_alias=AliasChoices("artifact_root", "JINDIAO_ARTIFACT_ROOT"),
    )
    max_concurrency: int = Field(
        default=4,
        ge=1,
        validation_alias=AliasChoices("max_concurrency", "JINDIAO_MAX_CONCURRENCY"),
    )
    multi_demo_partial_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "multi_demo_partial_enabled", "JINDIAO_MULTI_DEMO_PARTIAL_ENABLED"
        ),
    )
    single_demo_partial_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "single_demo_partial_enabled", "JINDIAO_SINGLE_DEMO_PARTIAL_ENABLED"
        ),
    )
    request_timeout_seconds: int = Field(
        default=300,
        ge=1,
        validation_alias=AliasChoices(
            "request_timeout_seconds",
            "JINDIAO_REQUEST_TIMEOUT_SECONDS",
        ),
    )
    max_tool_calls: int = Field(
        default=60,
        ge=1,
        validation_alias=AliasChoices("max_tool_calls", "JINDIAO_MAX_TOOL_CALLS"),
    )
    max_repair_rounds: int = Field(
        default=2,
        ge=0,
        validation_alias=AliasChoices("max_repair_rounds", "JINDIAO_MAX_REPAIR_ROUNDS"),
    )
    max_llm_requests: int = Field(
        default=64,
        ge=1,
        validation_alias=AliasChoices("max_llm_requests", "JINDIAO_MAX_LLM_REQUESTS"),
    )
    max_input_tokens: int = Field(
        default=300_000,
        ge=1,
        validation_alias=AliasChoices("max_input_tokens", "JINDIAO_MAX_INPUT_TOKENS"),
    )
    enforce_token_budget: bool = Field(
        default=True,
        validation_alias=AliasChoices("enforce_token_budget", "JINDIAO_ENFORCE_TOKEN_BUDGET"),
    )
    max_output_tokens: int = Field(
        default=100_000,
        ge=1,
        validation_alias=AliasChoices("max_output_tokens", "JINDIAO_MAX_OUTPUT_TOKENS"),
    )
    max_total_tokens: int = Field(
        default=400_000,
        ge=1,
        validation_alias=AliasChoices("max_total_tokens", "JINDIAO_MAX_TOTAL_TOKENS"),
    )
    max_schema_retries: int = Field(
        default=4,
        ge=0,
        validation_alias=AliasChoices("max_schema_retries", "JINDIAO_MAX_SCHEMA_RETRIES"),
    )
    max_snapshot_reads: int = Field(
        default=240,
        ge=1,
        validation_alias=AliasChoices("max_snapshot_reads", "JINDIAO_MAX_SNAPSHOT_READS"),
    )
    allow_degraded_mock: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "allow_degraded_mock",
            "JINDIAO_ALLOW_DEGRADED_MOCK",
        ),
    )
    skill_evolution_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "skill_evolution_enabled",
            "JINDIAO_SKILL_EVOLUTION_ENABLED",
        ),
    )
    skill_evolution_auto_approve: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "skill_evolution_auto_approve",
            "JINDIAO_SKILL_EVOLUTION_AUTO_APPROVE",
        ),
    )
    rule_version: str = Field(
        default="v1",
        validation_alias=AliasChoices("rule_version", "JINDIAO_RULE_VERSION"),
    )
    execution_profile: Literal["attached", "detached"] = Field(
        default="attached",
        validation_alias=AliasChoices("execution_profile", "JINDIAO_EXECUTION_PROFILE"),
    )
    shared_storage_backend: Literal["memory", "local", "session", "sfs"] = Field(
        default="memory",
        validation_alias=AliasChoices("shared_storage_backend", "JINDIAO_STORAGE_BACKEND"),
    )
    reporting_demo_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("reporting_demo_enabled", "JINDIAO_REPORTING_DEMO_ENABLED"),
    )
    reporting_public_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "reporting_public_enabled", "JINDIAO_REPORTING_PUBLIC_ENABLED"
        ),
    )
    reporting_public_origin: str = Field(
        default="",
        validation_alias=AliasChoices("reporting_public_origin", "JINDIAO_REPORTING_PUBLIC_ORIGIN"),
    )
    detached_probe_passed: bool = Field(
        default=False,
        validation_alias=AliasChoices("detached_probe_passed", "JINDIAO_DETACHED_PROBE_PASSED"),
    )
    max_event_queue: int = Field(
        default=256,
        ge=1,
        validation_alias=AliasChoices("max_event_queue", "JINDIAO_MAX_EVENT_QUEUE"),
    )
    max_events_per_run: int = Field(
        default=10_000,
        ge=1,
        validation_alias=AliasChoices("max_events_per_run", "JINDIAO_MAX_EVENTS_PER_RUN"),
    )
    event_retention_seconds: int = Field(
        default=86_400,
        ge=60,
        validation_alias=AliasChoices("event_retention_seconds", "JINDIAO_EVENT_RETENTION_SECONDS"),
    )

    @model_validator(mode="after")
    def require_authorization_for_live_mode(self) -> Settings:
        if self.reporting_public_enabled:
            if self.environment != "integration" or self.shared_storage_backend != "local":
                raise ValueError("public reporting requires integration and local storage")
            origin = self.reporting_public_origin
            try:
                parsed = urlsplit(origin)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and parsed.path in {"", "/"}
                    and not parsed.query
                    and not parsed.fragment
                    and not any(char.isspace() for char in origin)
                    and "\\" not in origin
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("public reporting requires one valid frontend origin")
            self.reporting_public_origin = origin.rstrip("/")
        if self.reporting_demo_enabled and (
            self.shared_storage_backend not in {"memory", "local"}
            or self.environment not in {"development", "test"}
        ):
            raise ValueError("reporting demo requires development/test and memory/local storage")
        if self.max_total_tokens > self.max_input_tokens + self.max_output_tokens:
            raise ValueError("max_total_tokens cannot exceed combined input/output token limits")
        if self.data_source_mode == "tianyancha" and self.tianyancha_authorization is None:
            raise ValueError("Tianyancha mode requires TIANYANCHA_MCP_AUTHORIZATION")
        if self.skill_evolution_auto_approve:
            raise ValueError("Skill evolution candidates require explicit human approval")
        if self.agent_runtime_mode == "formal":
            if self.model_provider.casefold() == "offline_mock":
                raise ValueError("formal agent runtime cannot use offline_mock")
            missing = []
            if not self.model_provider.strip():
                missing.append("MODEL_PROVIDER")
            if not self.model_name.strip():
                missing.append("MODEL_NAME")
            if self.model_base_url is None or not self.model_base_url.strip():
                missing.append("MODEL_BASE_URL")
            if self.model_api_key is None or not self.model_api_key.get_secret_value().strip():
                missing.append("MODEL_API_KEY")
            if missing:
                raise ValueError(
                    "formal agent runtime requires a complete model route; "
                    f"missing: {', '.join(missing)}"
                )
        if self.execution_profile == "detached":
            if not self.detached_probe_passed:
                raise ValueError("detached execution requires a passing deployment probe")
            if self.shared_storage_backend not in {"session", "sfs"}:
                raise ValueError("detached execution requires session or sfs storage")
        return self

    @property
    def formal_agent_run(self) -> bool:
        return self.agent_runtime_mode == "formal"


__all__ = ["Settings"]
