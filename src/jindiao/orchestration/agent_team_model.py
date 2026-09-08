"""Shared-budget model client used by every member of one AgentTeams run."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig

from jindiao.orchestration.budgeted_model import BudgetedModel
from jindiao.orchestration.investigation_team_tools import (
    get_investigation_team_state,
)

AGENT_TEAM_BUDGETED_MODEL_PROVIDER = "jindiao_budgeted_agent_team"


class BudgetedAgentTeamModelClient(BaseModelClient):  # type: ignore[misc]
    """Delegate to a normal provider while charging one Run-level ledger."""

    __client_name__ = AGENT_TEAM_BUDGETED_MODEL_PROVIDER
    __client_type__ = "llm"

    def __init__(self, model_config: Any, model_client_config: ModelClientConfig) -> None:
        super().__init__(model_config, model_client_config)
        inner_provider = str(getattr(model_client_config, "inner_provider", "") or "").strip()
        inner_model_name = str(getattr(model_client_config, "inner_model_name", "") or "").strip()
        if not inner_provider or inner_provider == AGENT_TEAM_BUDGETED_MODEL_PROVIDER:
            raise ValueError("budgeted AgentTeams model requires a non-recursive inner provider")
        if not inner_model_name:
            raise ValueError("budgeted AgentTeams model requires inner_model_name")
        self._inner_model_name = inner_model_name
        inner_data = model_client_config.model_dump()
        inner_data.update(
            {
                "client_id": f"{model_client_config.client_id}:inner",
                "client_provider": inner_provider,
                # Each paid attempt must pass through its own budget reservation.
                "max_retries": 0,
            }
        )
        inner_data.pop("inner_provider", None)
        inner_data.pop("inner_model_name", None)
        inner_model_config = (
            model_config.model_copy(update={"model_name": inner_model_name})
            if model_config is not None
            else None
        )
        self._inner = create_model_client(
            client_config=ModelClientConfig.model_validate(inner_data),
            model_config=inner_model_config,
        )

    def _validate_config(self) -> None:
        runtime_key = str(getattr(self.model_client_config, "runtime_key", "") or "").strip()
        if not runtime_key:
            raise ValueError("budgeted AgentTeams model requires runtime_key")
        if not self.model_client_config.api_key or not self.model_client_config.api_base:
            raise ValueError("budgeted AgentTeams model requires inner api_key and api_base")

    @property
    def _ledger(self) -> Any:
        runtime_key = str(self.model_client_config.runtime_key)
        return get_investigation_team_state(runtime_key).budget_ledger

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["model"] = self._inner_model_name
        model = BudgetedModel(self._inner, budget_ledger=self._ledger)
        return await model.invoke(*args, **kwargs)

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        kwargs["model"] = self._inner_model_name
        model = BudgetedModel(self._inner, budget_ledger=self._ledger)
        async with aclosing(model.stream(*args, **kwargs)) as source:
            async for chunk in source:
                yield chunk

    async def generate_image(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.generate_image(*args, **kwargs)

    async def generate_speech(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.generate_speech(*args, **kwargs)

    async def generate_video(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.generate_video(*args, **kwargs)


def register_agent_team_model_client() -> None:
    """Importing this module registers the client; kept as an explicit hook."""


__all__ = [
    "AGENT_TEAM_BUDGETED_MODEL_PROVIDER",
    "BudgetedAgentTeamModelClient",
    "register_agent_team_model_client",
]
