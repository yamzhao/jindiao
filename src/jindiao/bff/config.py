"""Explicit server-only configuration, never loaded from the agent's .env."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .security import HASH_PATTERN


class BffSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JINDIAO_BFF_", extra="forbid", hide_input_in_errors=True
    )

    gateway_origin: str
    runtime_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,46}[a-z0-9]$")
    api_key: SecretStr = Field(min_length=16)
    identity_key: SecretStr = Field(min_length=32)
    public_origin: str
    users: dict[str, str] = Field(repr=False)
    endpoint: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    development: bool = False
    tls_trust_store: Literal["certifi", "system"] = "certifi"
    tls_ca_file: Path | None = None
    session_ttl: int = Field(default=3600, ge=60, le=28800)
    max_sessions: int = Field(default=256, ge=1, le=4096)
    max_runs: int = Field(default=1024, ge=1, le=10000)
    run_ttl: int = Field(default=28800, ge=60, le=86400)
    create_per_minute: int = Field(default=3, ge=1, le=60)
    requests_per_minute: int = Field(default=120, ge=1, le=600)
    max_connections: int = Field(default=32, ge=1, le=128)
    connections_per_user: int = Field(default=4, ge=1, le=16)
    max_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    max_json_bytes: int = Field(default=16777216, ge=1024, le=33554432)
    max_event_bytes: int = Field(default=262144, ge=1024, le=1048576)
    upstream_timeout: float = Field(default=120, gt=0, le=300)
    stream_timeout: float = Field(default=1800, gt=0, le=3600)

    @model_validator(mode="after")
    def validate_security(self) -> BffSettings:
        for origin in (self.gateway_origin, self.public_origin):
            url = urlsplit(origin)
            if (
                not url.hostname
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or url.username
                or url.password
                or any(char.isspace() for char in origin)
                or "\\" in origin
            ):
                raise ValueError("Expected an origin without credentials, path or query")
            _ = url.port  # Reject malformed ports during startup.
            if url.scheme != "https" and not (
                self.development
                and url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("HTTPS required; development HTTP must be loopback-only")
        self.gateway_origin = self.gateway_origin.rstrip("/")
        self.public_origin = self.public_origin.rstrip("/")
        if not re.fullmatch(r"[!-~]{16,4096}", self.api_key.get_secret_value()):
            raise ValueError("API key must be a bounded printable ASCII header value")
        if not self.users or len(self.users) > 100:
            raise ValueError("Configure 1 to 100 named users")
        for name, encoded in self.users.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not HASH_PATTERN.fullmatch(
                encoded
            ):
                raise ValueError("Users require safe unique names and scrypt password hashes")
        return self

    @property
    def cookie_name(self) -> str:
        return (
            "jindiao_dev_session"
            if self.public_origin.startswith("http:")
            else "__Host-jindiao_session"
        )

    @property
    def upstream_base(self) -> str:
        return f"{self.gateway_origin}/runtimes/{self.runtime_name}/invocations"
