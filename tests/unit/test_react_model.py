from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from jindiao.orchestration.react_model import (
    JINDIAO_OPENAI_COMPATIBLE_PROVIDER,
    JindiaoOpenAICompatibleModelClient,
    build_react_agent_config,
)


def test_openai_compatible_react_config_uses_registered_proxy_safe_client() -> None:
    config = build_react_agent_config(
        model_name="qwen-plus",
        model_provider="OpenAI",
        model_api_key="test-only",
        model_base_url="https://model.example/v1",
        model_temperature=0.3,
        model_timeout_seconds=42,
        system_prompt="Use tools.",
        max_iterations=4,
    )

    client_config = config.model_client_config
    assert client_config is not None
    assert client_config.client_provider == JINDIAO_OPENAI_COMPATIBLE_PROVIDER
    assert client_config.upstream_provider == "OpenAI"
    assert client_config.max_retries == 0  # Retries must not bypass one request reservation.
    client = create_model_client(client_config, config.model_config_obj)
    assert isinstance(client, JindiaoOpenAICompatibleModelClient)


def test_proxy_safe_client_disables_ambient_proxies_but_keeps_explicit_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_http_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(httpx, "AsyncClient", fake_http_client)
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        "jindiao.orchestration.react_model.UrlUtils.get_global_proxy_url",
        lambda _: "http://proxy.example:8080",
    )
    client = JindiaoOpenAICompatibleModelClient(
        ModelRequestConfig(model="qwen-plus", temperature=0.3),
        ModelClientConfig(
            client_provider=JINDIAO_OPENAI_COMPATIBLE_PROVIDER,
            api_key="test-only",
            api_base="https://model.example/v1",
            upstream_provider="OpenAI",
        ),
    )

    built = client._build_async_openai_client(timeout=42)

    assert captured["proxy"] == "http://proxy.example:8080"
    assert captured["trust_env"] is False
    assert cast(Any, built).http_client is not None
