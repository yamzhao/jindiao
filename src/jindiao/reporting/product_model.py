"""Reuse the project model client and account reporting after prior Run costs."""

from __future__ import annotations

import asyncio
import json
import time

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from jindiao.application.errors import AgentExecutionError
from jindiao.application.settings import Settings
from jindiao.contracts.execution import ExecutionCost
from jindiao.orchestration.base import BudgetLedger, RunBudget
from jindiao.orchestration.budgeted_model import BudgetedModel
from jindiao.orchestration.react_model import model_client_provider


class OpenJiuwenReportModel:
    def __init__(
        self,
        *,
        settings: Settings,
        budget: RunBudget,
        prior_cost: ExecutionCost,
        elapsed_seconds: float,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._prior = prior_cost
        self._prior_usage_complete = prior_cost.provider_usage_requests == prior_cost.llm_requests
        self._deadline = time.monotonic() + budget.timeout_seconds - elapsed_seconds
        remaining = {
            "max_llm_requests": budget.max_llm_requests - prior_cost.llm_requests,
        }
        if budget.enforce_token_budget:
            remaining.update(
                max_input_tokens=budget.max_input_tokens - prior_cost.input_tokens,
                max_output_tokens=budget.max_output_tokens - prior_cost.output_tokens,
                max_total_tokens=budget.max_total_tokens - prior_cost.total_tokens,
            )
        self._ledger = (
            BudgetLedger(budget.model_copy(update=remaining))
            if min(remaining.values()) > 0
            else None
        )
        self._model: BudgetedModel | None = None

    @property
    def cost(self) -> ExecutionCost:
        return (
            self._ledger.to_execution_cost() if self._ledger is not None else ExecutionCost.zero()
        )

    @property
    def provider_usage_complete(self) -> bool:
        return self._prior_usage_complete and (
            self._ledger is None or bool(self._ledger.error_details()["provider_usage_complete"])
        )

    async def generate(self, *, prompt: str, schema: dict[str, object]) -> str:
        if not self._prior_usage_complete:
            raise AgentExecutionError("prior provider usage is incomplete; refusing report request")
        if self._ledger is None:
            raise AgentExecutionError("reporting Run budget exhausted")
        total = ExecutionCost.combine(self._prior, self.cost)
        remaining_seconds = self._deadline - time.monotonic()
        if total.llm_requests >= self._budget.max_llm_requests or remaining_seconds <= 0:
            raise AgentExecutionError("reporting Run budget exhausted")
        message = prompt + "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        # UTF-8 bytes give a conservative input-token upper bound before dispatch.
        reservation = len(message.encode("utf-8"))
        remaining_input = self._budget.max_input_tokens - total.input_tokens
        remaining_total = self._budget.max_total_tokens - total.total_tokens
        max_output = 10000
        if self._budget.enforce_token_budget:
            max_output = min(
                max_output,
                self._budget.max_output_tokens - total.output_tokens,
                remaining_total - reservation,
            )
        if self._budget.enforce_token_budget and (reservation > remaining_input or max_output <= 0):
            raise AgentExecutionError("reporting token budget exhausted")
        if self.cost.llm_requests:
            if total.schema_retries >= self._budget.max_schema_retries:
                raise AgentExecutionError("reporting schema repair budget exhausted")
            await self._ledger.claim_schema_retry("reporting.schema_repair")
        if self._model is None:
            settings = self._settings
            if not settings.model_api_key or not settings.model_base_url:
                raise AgentExecutionError("reporting model route is unavailable")
            model = Model(
                model_client_config=ModelClientConfig(
                    client_provider=model_client_provider(settings.model_provider),
                    api_key=settings.model_api_key.get_secret_value(),
                    api_base=settings.model_base_url,
                    timeout=remaining_seconds,
                    max_retries=0,
                ),
                model_config=ModelRequestConfig(model=settings.model_name, temperature=0),
            )
            self._model = BudgetedModel(model, budget_ledger=self._ledger)
        async with asyncio.timeout(remaining_seconds):
            response = await self._model.invoke(
                messages=[{"role": "user", "content": message}],
                max_tokens=max_output,
                response_format={"type": "json_object"},
            )
        content = response.content
        if not isinstance(content, str):
            raise ValueError("report model must return JSON text")
        return content
