"""Attach request-level accounting before a ReAct runtime can call its model."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from jindiao.application.errors import AgentExecutionError

from .agent_runtime import AgentExecutionEvent, AgentExecutionRequest, AgentExecutionRuntime
from .base import BudgetLedger, CancellationToken, check_cancellation
from .budgeted_model import BudgetedModel


class BudgetedExecutionRuntime:
    """Meter actual model calls, independently of optional SDK usage events.

    Only the synchronous ReAct ``_get_llm`` / ``set_llm`` interface is supported.
    The budgeted model stays installed for later calls on the same agent. Stream
    events and delegate lifecycle methods are otherwise passed through unchanged.
    """

    def __init__(self, delegate: AgentExecutionRuntime, *, budget_ledger: BudgetLedger) -> None:
        self._delegate = delegate
        self._budget_ledger = budget_ledger

    async def stream(
        self,
        agent: object,
        request: AgentExecutionRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncGenerator[AgentExecutionEvent, None]:
        check_cancellation(cancellation_token)
        self._bind_model(agent)
        source = self._delegate.stream(agent, request, cancellation_token=cancellation_token)
        failure: BaseException | None = None
        try:
            async for event in source:
                yield event
        except BaseException as error:
            failure = error
            raise
        finally:
            # Exhaustion, cancellation and explicit aclose must all finish the
            # delegate's own session/tool cleanup, without fabricating events.
            close = getattr(source, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    if failure is None:
                        raise

    def _bind_model(self, agent: object) -> None:
        getter = getattr(agent, "_get_llm", None)
        setter = getattr(agent, "set_llm", None)
        if not callable(getter) or not callable(setter):
            raise self._binding_error("budgeted runtime requires a ReAct model interface")
        try:
            original = getter()
        except Exception:
            raise self._binding_error("cannot obtain ReAct model for accounting") from None

        current = original
        selected: BudgetedModel | None = None
        seen: set[int] = set()
        while isinstance(current, BudgetedModel):
            if current._budget_ledger is not self._budget_ledger:
                raise self._binding_error("ReAct model is bound to a different budget ledger")
            if id(current) in seen:
                raise self._binding_error("ReAct model contains a cyclic budget wrapper")
            seen.add(id(current))
            selected = current
            current = current._model
        if not callable(getattr(current, "invoke", None)) or not callable(
            getattr(current, "stream", None)
        ):
            raise self._binding_error("ReAct model does not expose supported chat calls")
        if selected is None:
            selected = BudgetedModel(current, budget_ledger=self._budget_ledger)
        if original is selected:
            return
        try:
            setter(selected)
            installed = getter()
        except Exception:
            raise self._binding_error("cannot install ReAct model accounting") from None
        if installed is not selected:
            raise self._binding_error("ReAct model setter did not install accounting")

    def _binding_error(self, message: str) -> AgentExecutionError:
        return AgentExecutionError(
            message,
            details={"operation": "runtime.bind_model", **self._budget_ledger.error_details()},
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


__all__ = ["BudgetedExecutionRuntime"]
