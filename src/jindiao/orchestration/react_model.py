"""Version-pinned openJiuwen ReAct model configuration helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from openjiuwen.core.common.logging import LogEventType, llm_logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
    OpenAIModelClient,
)
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.single_agent import ReActAgentConfig

if TYPE_CHECKING:
    import openai


JINDIAO_OPENAI_COMPATIBLE_PROVIDER = "jindiao_openai_compatible"
_OPENAI_COMPATIBLE_PROVIDER_ALIASES = {
    "openai",
    "openai_compatible",
}


class JindiaoOpenAICompatibleModelClient(OpenAIModelClient):  # type: ignore[misc]
    """OpenAI-compatible client immune to malformed ambient proxy bypasses."""

    __client_name__ = JINDIAO_OPENAI_COMPATIBLE_PROVIDER

    def _build_async_openai_client(
        self,
        timeout: float | None = None,
    ) -> openai.AsyncOpenAI:
        """Build openJiuwen's client while relying only on its explicit proxy."""
        from openai import AsyncOpenAI

        ssl_verify = self.model_client_config.verify_ssl
        ssl_cert = self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify
        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
            trust_env=False,
        )
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        llm_logger.info(
            "Before create proxy-safe openai client, model client config params ready.",
            event_type=LogEventType.LLM_CALL_START,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
        )
        return AsyncOpenAI(
            api_key=self.model_client_config.api_key,
            base_url=self.model_client_config.api_base,
            http_client=http_client,  # type: ignore[arg-type]
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
        )


def model_client_provider(provider: str) -> str:
    """Map OpenAI-compatible aliases to the project proxy-safe adapter."""

    normalized = provider.strip()
    alias = normalized.casefold().replace("-", "_")
    if alias in _OPENAI_COMPATIBLE_PROVIDER_ALIASES:
        return JINDIAO_OPENAI_COMPATIBLE_PROVIDER
    return normalized


def build_react_agent_config(
    *,
    model_name: str,
    model_provider: str,
    model_api_key: str,
    model_base_url: str,
    model_temperature: float,
    model_timeout_seconds: float,
    system_prompt: str,
    max_iterations: int,
    parallel_tool_calls: bool = False,
) -> ReActAgentConfig:
    """Populate both legacy display fields and the configs used at invocation."""

    if model_timeout_seconds <= 0:
        raise ValueError("model timeout must be positive")
    route = tuple(
        value.strip() for value in (model_name, model_provider, model_api_key, model_base_url)
    )
    model_client_config = None
    model_config_obj = None
    if all(route):
        model_client_config = ModelClientConfig(
            client_provider=model_client_provider(model_provider),
            api_key=model_api_key,
            api_base=model_base_url,
            timeout=model_timeout_seconds,
            stream_first_chunk_timeout=model_timeout_seconds,
            stream_idle_timeout=model_timeout_seconds,
            upstream_provider=model_provider,
        )
        model_config_obj = ModelRequestConfig(
            model=model_name,
            temperature=model_temperature,
        )
    return ReActAgentConfig(
        model_name=model_name,
        model_provider=model_provider,
        api_key=model_api_key,
        api_base=model_base_url,
        model_client_config=model_client_config,
        model_config_obj=model_config_obj,
        prompt_template=[{"role": "system", "content": system_prompt}],
        max_iterations=max_iterations,
        parallel_tool_calls=parallel_tool_calls,
    )


__all__ = [
    "JINDIAO_OPENAI_COMPATIBLE_PROVIDER",
    "JindiaoOpenAICompatibleModelClient",
    "build_react_agent_config",
    "model_client_provider",
]
