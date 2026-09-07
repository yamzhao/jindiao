"""Model proxy that accounts every formal Agent request in one Run budget."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jindiao.orchestration.base import BudgetLedger


def _usage_tokens(message: object) -> tuple[int, int, bool]:
    usage = getattr(message, "usage_metadata", None)
    if usage is None:
        return 0, 0, False
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
        True,
    )


class BudgetedModel:
    """Keep provider calls and reported usage inside the shared atomic ledger."""

    def __init__(self, model: Any, *, budget_ledger: BudgetLedger) -> None:
        self._model = model
        self._budget_ledger = budget_ledger

    async def invoke(self, *args: object, **kwargs: object) -> Any:
        await self._budget_ledger.claim_llm_request("model.invoke")
        async with self._budget_ledger.operation_slot("model.invoke"):
            response = await self._model.invoke(*args, **kwargs)
        input_tokens, output_tokens, provider_usage = _usage_tokens(response)
        await self._budget_ledger.record_llm_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_usage=provider_usage,
        )
        return response

    async def stream(self, *args: object, **kwargs: object) -> AsyncIterator[Any]:
        await self._budget_ledger.claim_llm_request("model.stream")
        latest_usage = (0, 0, False)
        async with self._budget_ledger.operation_slot("model.stream"):
            async for chunk in self._model.stream(*args, **kwargs):
                current_usage = _usage_tokens(chunk)
                if current_usage[2]:
                    latest_usage = current_usage
                yield chunk
        await self._budget_ledger.record_llm_usage(
            input_tokens=latest_usage[0],
            output_tokens=latest_usage[1],
            provider_usage=latest_usage[2],
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


__all__ = ["BudgetedModel"]
