from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from jindiao.application.errors import AgentExecutionError
from jindiao.contracts.results import AgentResultPhase
from jindiao.orchestration.agent_runtime import (
    AgentExecutionEvent,
    AgentExecutionEventType,
    AgentExecutionRequest,
    AgentExecutionRuntime,
)
from jindiao.orchestration.base import BudgetLedger, CancellationToken, RunBudget
from jindiao.orchestration.budgeted_model import BudgetedModel


def _ledger() -> BudgetLedger:
    return BudgetLedger(
        RunBudget(max_tool_calls=10, max_concurrency=3, timeout_seconds=30, max_repair_rounds=0)
    )


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        run_id="acquisition-run",
        agent_id="context-agent",
        role="enterprise-context",
        phase=AgentResultPhase.ACQUISITION,
        session_id="acquisition-session",
        query="collect context",
        task_ids=("collect",),
        prompt_version="context-v1",
        prompt_sha256="a" * 64,
        timeout_seconds=10,
    )


def _event() -> AgentExecutionEvent:
    request = _request()
    return AgentExecutionEvent(
        run_id=request.run_id,
        agent_id=request.agent_id,
        role=request.role,
        phase=request.phase,
        sequence=1,
        event_type=AgentExecutionEventType.OUTPUT,
        occurred_at=datetime(2026, 9, 8, tzinfo=UTC),
        prompt_version=request.prompt_version,
        prompt_sha256=request.prompt_sha256,
        payload={"message": "context collected"},
    )


class Provider:
    def __init__(self, *, with_usage: bool = True) -> None:
        self.calls = 0
        self.with_usage = with_usage

    async def invoke(self, messages: object, **kwargs: object) -> object:
        self.calls += 1
        if self.with_usage:
            return {"usage_metadata": {"input_tokens": 20, "output_tokens": 3}}
        return {"content": "context collected"}

    async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
        yield await self.invoke(messages, **kwargs)


class ReActInterface:
    def __init__(self, model: object) -> None:
        self.model = model
        self.replacements = 0

    def _get_llm(self) -> object:
        return self.model

    def set_llm(self, model: object) -> None:
        self.model = model
        self.replacements += 1


class Delegate:
    def __init__(self, events: list[AgentExecutionEvent]) -> None:
        self.events = events
        self.received: list[tuple[object, AgentExecutionRequest, CancellationToken | None]] = []
        self.cleaned = 0
        self.shutdown = False
        self.failure: BaseException | None = None

    async def stream(
        self,
        agent: object,
        request: AgentExecutionRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncIterator[AgentExecutionEvent]:
        self.received.append((agent, request, cancellation_token))
        try:
            await cast(Any, agent)._get_llm().invoke(request.query)
            if self.failure is not None:
                raise self.failure
            for event in self.events:
                yield event
        finally:
            self.cleaned += 1

    async def close(self) -> None:
        self.shutdown = True


def _adapter(delegate: AgentExecutionRuntime, ledger: BudgetLedger) -> Any:
    from jindiao.orchestration.accounted_runtime import BudgetedExecutionRuntime

    return BudgetedExecutionRuntime(delegate, budget_ledger=ledger)


@pytest.mark.asyncio
async def test_request_without_usage_or_sdk_events_is_not_reported_as_no_model_calls() -> None:
    provider = Provider(with_usage=False)
    agent = ReActInterface(provider)
    delegate = Delegate([])
    ledger = _ledger()

    events = [event async for event in _adapter(delegate, ledger).stream(agent, _request())]

    assert events == []
    assert provider.calls == 1
    assert ledger.snapshot().llm_requests == 1
    assert ledger.snapshot().unknown_usage_requests == 1
    assert ledger.snapshot().unreported_input_tokens > 0
    assert ledger.error_details()["provider_usage_complete"] is False
    assert delegate.cleaned == 1


@pytest.mark.asyncio
async def test_completed_usage_is_counted_across_context_and_deepsearch_without_new_events() -> (
    None
):
    original = _event()
    delegate = Delegate([original])
    ledger = _ledger()
    runtime = _adapter(delegate, ledger)
    request = _request()
    context = ReActInterface(Provider())
    deepsearch = ReActInterface(Provider())

    for agent in (context, deepsearch):
        events = [event async for event in runtime.stream(agent, request)]
        assert len(events) == 1
        assert events[0] is original
        assert isinstance(agent.model, BudgetedModel)
    assert [item[0] for item in delegate.received] == [context, deepsearch]
    assert all(item[1] is request for item in delegate.received)
    assert ledger.snapshot().llm_requests == 2
    assert ledger.snapshot().input_tokens == 40
    assert ledger.snapshot().output_tokens == 6
    assert ledger.error_details()["provider_usage_complete"] is True
    assert delegate.cleaned == 2


@pytest.mark.asyncio
async def test_repeated_and_nested_runtime_adapters_do_not_double_charge_same_ledger() -> None:
    ledger = _ledger()
    provider = Provider()
    model = BudgetedModel(provider, budget_ledger=ledger)
    agent = ReActInterface(model)
    delegate = Delegate([])
    runtime = _adapter(_adapter(delegate, ledger), ledger)

    for _ in range(2):
        _ = [event async for event in runtime.stream(agent, _request())]
    assert provider.calls == 2
    assert ledger.snapshot().llm_requests == 2
    assert ledger.snapshot().input_tokens == 40
    assert agent.model is model
    assert agent.replacements == 0


@pytest.mark.asyncio
async def test_preexisting_nested_model_proxies_for_same_ledger_are_deduplicated() -> None:
    ledger = _ledger()
    provider = Provider()
    inner = BudgetedModel(provider, budget_ledger=ledger)
    agent = ReActInterface(BudgetedModel(inner, budget_ledger=ledger))

    _ = [event async for event in _adapter(Delegate([]), ledger).stream(agent, _request())]

    assert provider.calls == 1
    assert ledger.snapshot().llm_requests == 1
    assert agent.model is inner


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_inside", [False, True])
async def test_foreign_ledger_proxy_is_rejected_before_delegate_can_start(
    foreign_inside: bool,
) -> None:
    ledger = _ledger()
    other = _ledger()
    provider = Provider()
    model = BudgetedModel(provider, budget_ledger=other)
    if foreign_inside:
        model = BudgetedModel(model, budget_ledger=ledger)
    agent = ReActInterface(model)
    delegate = Delegate([])

    with pytest.raises(AgentExecutionError, match="different budget ledger"):
        _ = [event async for event in _adapter(delegate, ledger).stream(agent, _request())]
    assert delegate.received == []
    assert provider.calls == 0
    assert ledger.snapshot().llm_requests == other.snapshot().llm_requests == 0
    assert agent.model is model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent",
    [object(), SimpleNamespace(_get_llm=lambda: Provider()), ReActInterface(object())],
)
async def test_agents_without_supported_react_model_interface_fail_closed(agent: object) -> None:
    delegate = Delegate([])
    ledger = _ledger()
    with pytest.raises(AgentExecutionError, match="ReAct"):
        _ = [event async for event in _adapter(delegate, ledger).stream(agent, _request())]
    assert delegate.received == []
    assert ledger.snapshot().llm_requests == 0


@pytest.mark.asyncio
async def test_noop_model_setter_cannot_silently_bypass_accounting() -> None:
    class BrokenAgent(ReActInterface):
        def set_llm(self, model: object) -> None:
            pass

    delegate = Delegate([])
    with pytest.raises(AgentExecutionError, match="ReAct"):
        _ = [
            event
            async for event in _adapter(delegate, _ledger()).stream(
                BrokenAgent(Provider()), _request()
            )
        ]
    assert delegate.received == []


@pytest.mark.asyncio
async def test_consumer_close_finishes_delegate_cleanup_before_return() -> None:
    delegate = Delegate([_event(), _event()])
    ledger = _ledger()
    stream = _adapter(delegate, ledger).stream(ReActInterface(Provider()), _request())

    await anext(stream)
    await stream.aclose()

    assert delegate.cleaned == 1
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().input_tokens == 20


@pytest.mark.asyncio
async def test_cancellation_is_passed_through_and_delegate_close_is_preserved() -> None:
    delegate = Delegate([])
    failure = asyncio.CancelledError()
    delegate.failure = failure
    ledger = _ledger()
    runtime = _adapter(delegate, ledger)
    token = cast(CancellationToken, SimpleNamespace(cancelled=False))

    with pytest.raises(asyncio.CancelledError) as caught:
        _ = [
            event
            async for event in runtime.stream(
                ReActInterface(Provider()), _request(), cancellation_token=token
            )
        ]
    assert caught.value is failure
    assert delegate.received[0][2] is token
    assert delegate.cleaned == 1
    assert ledger.snapshot().input_tokens == 20
    await runtime.close()
    assert delegate.shutdown is True


@pytest.mark.asyncio
async def test_pre_cancelled_request_does_not_bind_model_or_start_delegate() -> None:
    delegate = Delegate([])
    agent = ReActInterface(Provider())
    token = cast(CancellationToken, SimpleNamespace(cancelled=True))

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in _adapter(delegate, _ledger()).stream(
                agent, _request(), cancellation_token=token
            )
        ]
    assert agent.replacements == 0
    assert delegate.received == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("delegate failed"), asyncio.CancelledError()])
async def test_cleanup_failure_does_not_replace_original_delegate_failure(
    failure: BaseException,
) -> None:
    class BrokenSource:
        closed = False

        def __aiter__(self) -> BrokenSource:
            return self

        async def __anext__(self) -> AgentExecutionEvent:
            raise failure

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("cleanup failed")

    source = BrokenSource()

    class BrokenDelegate:
        def stream(
            self,
            agent: object,
            request: AgentExecutionRequest,
            *,
            cancellation_token: CancellationToken | None = None,
        ) -> AsyncIterator[AgentExecutionEvent]:
            return source

    with pytest.raises(type(failure)) as caught:
        _ = [
            event
            async for event in _adapter(BrokenDelegate(), _ledger()).stream(
                ReActInterface(Provider()), _request()
            )
        ]
    assert caught.value is failure
    assert source.closed is True


@pytest.mark.asyncio
async def test_mid_request_task_cancellation_keeps_unknown_cost_and_cleans_delegate() -> None:
    entered = asyncio.Event()

    class WaitingProvider(Provider):
        async def invoke(self, messages: object, **kwargs: object) -> object:
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("provider must be cancelled")

    ledger = _ledger()
    delegate = Delegate([])
    provider = WaitingProvider()
    runtime = _adapter(delegate, ledger)

    async def consume() -> None:
        async for _ in runtime.stream(ReActInterface(provider), _request()):
            pytest.fail("cancelled request must not fabricate a success event")

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.snapshot().llm_requests == 1
    assert ledger.snapshot().unknown_usage_requests == 1
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().unreported_output_tokens == 10_000
    assert delegate.cleaned == 1
