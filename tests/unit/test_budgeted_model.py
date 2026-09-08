from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from jindiao.application.errors import AgentExecutionError
from jindiao.orchestration.base import BudgetLedger, RunBudget
from jindiao.orchestration.budgeted_model import BudgetedModel


def ledger_for(**overrides: int) -> BudgetLedger:
    values = {
        "max_tool_calls": 10,
        "max_concurrency": 4,
        "timeout_seconds": 10,
        "max_repair_rounds": 0,
        "max_input_tokens": 10_000,
        "max_output_tokens": 100,
        "max_total_tokens": 10_100,
    }
    values.update(overrides)
    return BudgetLedger(RunBudget(**values))


def response(input_tokens: int = 20, output_tokens: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        content="answer",
        usage_metadata=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.result: object = response()
        self.failure: BaseException | None = None
        self.chunks: list[object] = []

    async def invoke(self, messages: object, **kwargs: object) -> object:
        self.calls.append({"messages": messages, **kwargs})
        if self.failure is not None:
            raise self.failure
        return self.result

    async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
        self.calls.append({"messages": messages, **kwargs})
        for chunk in self.chunks:
            yield chunk
        if self.failure is not None:
            raise self.failure


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["invoke", "stream"])
@pytest.mark.parametrize("field", ["messages", "tools", "response_format"])
async def test_oversized_request_rejected_before_any_provider_call(method: str, field: str) -> None:
    provider = FakeProvider()
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    arguments: dict[str, Any] = {"messages": [{"role": "user", "content": "hello"}]}
    arguments[field] = {"content": "尽调" * 5000}
    with pytest.raises(AgentExecutionError, match="token budget") as caught:
        if method == "invoke":
            await model.invoke(**arguments)
        else:
            _ = [chunk async for chunk in model.stream(**arguments)]
    assert provider.calls == []
    assert ledger.snapshot().llm_requests == 0
    assert "尽调" not in str(caught.value.details)


@pytest.mark.asyncio
async def test_output_is_bounded_without_mutating_messages_or_kwargs() -> None:
    provider = FakeProvider()
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    messages = [{"role": "user", "content": "hello"}]
    kwargs = {"tools": [{"name": "tool", "description": "search"}], "max_tokens": 10_000}
    original = deepcopy((messages, kwargs))
    assert await model.invoke(messages, **kwargs) is provider.result
    assert provider.calls[0]["max_tokens"] == 100
    assert (messages, kwargs) == original
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().input_tokens == 20


@pytest.mark.asyncio
async def test_default_and_explicit_lower_output_limit_are_preserved() -> None:
    provider = FakeProvider()
    model = BudgetedModel(provider, budget_ledger=ledger_for())
    await model.invoke("hello", max_tokens=7)
    await model.invoke("hello")
    assert provider.calls[0]["max_tokens"] == 7
    assert provider.calls[1]["max_tokens"] == 97


@pytest.mark.asyncio
async def test_default_output_allows_batched_check_results_with_a_finite_cap() -> None:
    provider = FakeProvider()
    ledger = ledger_for(max_output_tokens=20_000, max_total_tokens=30_000)
    await BudgetedModel(provider, budget_ledger=ledger).invoke("hello")
    assert provider.calls[0]["max_tokens"] == 10_000


@pytest.mark.asyncio
async def test_pydantic_messages_and_tools_are_estimated_as_utf8_not_ignored() -> None:
    class Message(BaseModel):
        role: str = "user"
        content: str

    provider = FakeProvider()
    model = BudgetedModel(provider, budget_ledger=ledger_for())
    with pytest.raises(AgentExecutionError, match="token budget"):
        await model.invoke([Message(content="测" * 4000)])
    assert not provider.calls


@pytest.mark.asyncio
async def test_unknown_request_objects_fail_closed_without_leaking_their_repr() -> None:
    class Unsupported:
        def __repr__(self) -> str:
            return "PRIVATE_PAYLOAD"

    provider = FakeProvider()
    model = BudgetedModel(provider, budget_ledger=ledger_for())
    with pytest.raises(AgentExecutionError, match="estimate") as caught:
        await model.invoke([Unsupported()])
    assert not provider.calls
    assert "PRIVATE_PAYLOAD" not in str(caught.value.details)


@pytest.mark.asyncio
async def test_positional_max_tokens_does_not_bypass_cap_or_duplicate_argument() -> None:
    class PositionalProvider:
        received_max: int | None = None

        async def invoke(self, messages: object, max_tokens: int | None = None) -> object:
            self.received_max = max_tokens
            return response()

    provider = PositionalProvider()
    model = BudgetedModel(provider, budget_ledger=ledger_for())
    await model.invoke("hello", 10_000)
    assert provider.received_max == 100


@pytest.mark.asyncio
async def test_overshoot_exception_carries_actual_usage_after_provider_response() -> None:
    provider = FakeProvider()
    provider.result = response(16_238, 12)
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    with pytest.raises(AgentExecutionError, match="token budget") as caught:
        await model.invoke("hello")
    assert caught.value.details["execution_cost"]["input_tokens"] == 16_238
    assert ledger.snapshot().reserved_input_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("provider stopped"), asyncio.CancelledError()])
async def test_interrupted_stream_records_latest_usage_and_remains_incomplete(
    failure: BaseException,
) -> None:
    provider = FakeProvider()
    provider.chunks = [response(20, 1), response(20, 3)]
    provider.failure = failure
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    with pytest.raises(type(failure)) as caught:
        _ = [chunk async for chunk in model.stream("hello")]
    usage = ledger.snapshot()
    assert usage.input_tokens == 20
    assert usage.output_tokens == 3
    assert usage.reserved_input_tokens == 0
    assert usage.unknown_usage_requests == 1
    assert usage.unreported_output_tokens == 97
    assert cast(Any, caught.value).details["provider_usage_complete"] is False


@pytest.mark.asyncio
async def test_early_consumer_close_finalizes_stream_and_closes_provider() -> None:
    closed = []

    class StreamingProvider(FakeProvider):
        async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
            try:
                yield response()
                yield response(20, 5)
            finally:
                closed.append(True)

    ledger = ledger_for()
    model = BudgetedModel(StreamingProvider(), budget_ledger=ledger)
    stream = model.stream("hello")
    await anext(stream)
    await stream.aclose()
    assert closed == [True]
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_dispatched_error_without_usage_is_unknown_not_successful_zero() -> None:
    provider = FakeProvider()
    provider.failure = RuntimeError("provider stopped")
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    with pytest.raises(RuntimeError) as caught:
        await model.invoke("hello")
    usage = ledger.snapshot()
    assert usage.llm_requests == 1
    assert usage.successful_llm_requests == 0
    assert usage.unknown_usage_requests == 1
    assert usage.unreported_input_tokens > 0
    assert usage.unreported_output_tokens == 100
    assert usage.reserved_input_tokens == 0
    assert cast(Any, caught.value).details["provider_usage_complete"] is False


@pytest.mark.asyncio
async def test_invoke_error_with_usage_is_recorded_without_masking_failure() -> None:
    provider = FakeProvider()
    failure = RuntimeError("provider stopped")
    failure.usage_metadata = response().usage_metadata  # type: ignore[attr-defined]
    provider.failure = failure
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    with pytest.raises(RuntimeError) as caught:
        await model.invoke("hello")
    assert caught.value is failure
    assert ledger.snapshot().input_tokens == 20
    assert cast(Any, caught.value).details["execution_cost"]["total_tokens"] == 23


@pytest.mark.asyncio
async def test_mapping_usage_and_missing_usage_are_not_confused() -> None:
    provider = FakeProvider()
    provider.result = {"usage_metadata": {"input_tokens": 20, "output_tokens": 3}}
    ledger = ledger_for()
    model = BudgetedModel(provider, budget_ledger=ledger)
    await model.invoke("hello", max_tokens=20)
    provider.result = {"content": "answer", "usage_metadata": {}}
    await model.invoke("hello", max_tokens=20)
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().provider_usage_requests == 1
    assert ledger.snapshot().unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_concurrent_inflight_token_reservation_blocks_second_provider_call() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider(FakeProvider):
        async def invoke(self, messages: object, **kwargs: object) -> object:
            self.calls.append({"messages": messages, **kwargs})
            if len(self.calls) > 1:
                return response()
            entered.set()
            await release.wait()
            return response()

    provider = BlockingProvider()
    ledger = ledger_for(max_input_tokens=2_000, max_total_tokens=2_100)
    model = BudgetedModel(provider, budget_ledger=ledger)
    first = asyncio.create_task(model.invoke("x" * 1100, max_tokens=10))
    await entered.wait()
    try:
        with pytest.raises(AgentExecutionError, match="token budget"):
            await model.invoke("x" * 1100, max_tokens=10)
    finally:
        release.set()
        await first
    assert len(provider.calls) == 1
    assert ledger.snapshot().reserved_input_tokens == 0


@pytest.mark.asyncio
async def test_partial_usage_keeps_observed_input_and_missing_output_unknown() -> None:
    provider = FakeProvider()
    provider.result = {"usage_metadata": {"input_tokens": 20}}
    ledger = ledger_for()
    await BudgetedModel(provider, budget_ledger=ledger).invoke("hello")
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().provider_usage_requests == 0
    assert ledger.snapshot().unreported_output_tokens == 100
    assert ledger.error_details()["provider_usage_complete"] is False


@pytest.mark.asyncio
async def test_provider_error_response_usage_is_not_lost() -> None:
    provider = FakeProvider()
    failure = RuntimeError("provider stopped")
    cast(Any, failure).response = {"usage": {"prompt_tokens": 20, "completion_tokens": 3}}
    provider.failure = failure
    ledger = ledger_for()
    with pytest.raises(RuntimeError):
        await BudgetedModel(provider, budget_ledger=ledger).invoke("hello")
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().output_tokens == 3


@pytest.mark.asyncio
async def test_budget_declined_operation_slot_releases_undispatched_request() -> None:
    provider = FakeProvider()
    ledger = ledger_for(max_concurrency=1)
    async with ledger.operation_slot("already-running"):
        with pytest.raises(AgentExecutionError, match="concurrency") as caught:
            await BudgetedModel(provider, budget_ledger=ledger).invoke("hello")
    assert provider.calls == []
    assert ledger.snapshot().llm_requests == 0
    assert ledger.snapshot().reserved_input_tokens == 0
    assert caught.value.details["execution_cost"]["llm_requests"] == 0


@pytest.mark.asyncio
async def test_real_task_cancellation_records_usage_then_releases_reservations() -> None:
    entered = asyncio.Event()

    class BlockingProvider(FakeProvider):
        async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
            yield response()
            entered.set()
            await asyncio.Event().wait()

    ledger = ledger_for()
    model = BudgetedModel(BlockingProvider(), budget_ledger=ledger)

    async def consume() -> None:
        async for _chunk in model.stream("hello"):
            pass

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().active_operations == 0
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_late_invoke_records_response_usage_before_deadline_error() -> None:
    now = [100.0]
    ledger = BudgetLedger(ledger_for().budget, monotonic=lambda: now[0])

    class LateProvider(FakeProvider):
        async def invoke(self, messages: object, **kwargs: object) -> object:
            now[0] = 111.0
            return response()

    with pytest.raises(AgentExecutionError, match="deadline") as caught:
        await BudgetedModel(LateProvider(), budget_ledger=ledger).invoke("hello")
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().reserved_input_tokens == 0
    assert caught.value.details["execution_cost"]["total_tokens"] == 23


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter", ["max_output_tokens", "max_completion_tokens"])
async def test_alternative_output_caps_cannot_override_reserved_max_tokens(parameter: str) -> None:
    provider = FakeProvider()
    ledger = ledger_for()
    arguments = {parameter: 999_999}
    await BudgetedModel(provider, budget_ledger=ledger).invoke("hello", **arguments)
    assert provider.calls[0][parameter] == 100
    assert arguments[parameter] == 999_999


@pytest.mark.asyncio
async def test_extra_body_cannot_override_output_cap() -> None:
    provider = FakeProvider()
    ledger = ledger_for()
    body = {"max_tokens": 999_999}
    await BudgetedModel(provider, budget_ledger=ledger).invoke("hello", extra_body=body)
    assert cast(dict[str, object], provider.calls[0]["extra_body"])["max_tokens"] == 100
    assert body["max_tokens"] == 999_999


@pytest.mark.asyncio
async def test_multiple_completions_are_rejected_until_aggregate_output_can_be_bounded() -> None:
    provider = FakeProvider()
    with pytest.raises(AgentExecutionError, match="bound"):
        await BudgetedModel(provider, budget_ledger=ledger_for()).invoke("hello", n=2)
    assert not provider.calls


@pytest.mark.asyncio
async def test_model_config_payloads_are_included_and_default_output_is_honored() -> None:
    class Configuration(BaseModel):
        max_tokens: int = 7
        response_format: dict[str, str] = {}

    provider = FakeProvider()
    cast(Any, provider).model_config = Configuration()
    model = BudgetedModel(provider, budget_ledger=ledger_for())
    await model.invoke("hello")
    assert provider.calls[0]["max_tokens"] == 7
    cast(Any, provider).model_config = Configuration(response_format={"schema": "x" * 20_000})
    with pytest.raises(AgentExecutionError, match="token budget"):
        await model.invoke("hello")
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_signature_default_output_is_not_silently_increased() -> None:
    class DefaultProvider:
        received_max: int | None = None

        async def invoke(self, messages: object, *, max_tokens: int = 7) -> object:
            self.received_max = max_tokens
            return response()

    provider = DefaultProvider()
    await BudgetedModel(provider, budget_ledger=ledger_for()).invoke("hello")
    assert provider.received_max == 7


@pytest.mark.asyncio
async def test_large_default_tool_payload_is_also_estimated_before_dispatch() -> None:
    class DefaultProvider:
        called = False

        async def invoke(
            self, messages: object, tools: str = "x" * 20_000, **kwargs: object
        ) -> object:
            self.called = True
            return response()

    provider = DefaultProvider()
    with pytest.raises(AgentExecutionError, match="token budget"):
        await BudgetedModel(provider, budget_ledger=ledger_for()).invoke("hello")
    assert provider.called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["invoke", "stream"])
async def test_shared_ledger_deadline_cancels_provider_before_its_long_timeout(method: str) -> None:
    now = [100.0]
    ledger = BudgetLedger(ledger_for().budget, monotonic=lambda: now[0])
    now[0] = 109.99  # Only 10ms remain in the shared acquisition budget.

    class BlockingProvider(FakeProvider):
        cancelled = False

        async def invoke(self, messages: object, **kwargs: object) -> object:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                now[0] = 111.0
                raise
            raise AssertionError("provider must be cancelled at the Run deadline")

        async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
            yield response(20, 3)
            await self.invoke(messages, **kwargs)

    provider = BlockingProvider()
    model = BudgetedModel(provider, budget_ledger=ledger)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5) as watchdog:
            if method == "invoke":
                await model.invoke("hello", timeout=300)
            else:
                _ = [chunk async for chunk in model.stream("hello", timeout=300)]

    assert watchdog.expired() is False, "the shared deadline must fire before the test watchdog"
    assert provider.cancelled is True
    assert ledger.snapshot().llm_requests == 1
    assert ledger.snapshot().unknown_usage_requests == 1
    assert ledger.snapshot().reserved_input_tokens == 0
    assert ledger.snapshot().active_operations == 0
    assert ledger.snapshot().exhausted_reason == "orchestration deadline exceeded"
    assert ledger.snapshot().input_tokens == (20 if method == "stream" else 0)
    assert ledger.snapshot().output_tokens == (3 if method == "stream" else 0)


@pytest.mark.asyncio
async def test_stream_deadline_survives_sdk_pulling_each_chunk_in_a_different_task() -> None:
    now = [100.0]
    ledger = BudgetLedger(ledger_for().budget, monotonic=lambda: now[0])
    now[0] = 109.99

    class BlockingProvider(FakeProvider):
        async def stream(self, messages: object, **kwargs: object) -> AsyncIterator[object]:
            try:
                yield response(20, 3)
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                now[0] = 111.0
                raise

    stream = BudgetedModel(BlockingProvider(), budget_ledger=ledger).stream("hello")
    # The real SDK's wait_for(__anext__()) also uses a fresh Task per chunk.
    await asyncio.create_task(anext(stream))
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5) as watchdog:
            await asyncio.create_task(anext(stream))

    assert watchdog.expired() is False
    assert ledger.snapshot().input_tokens == 20
    assert ledger.snapshot().output_tokens == 3
    assert ledger.snapshot().unknown_usage_requests == 1
    assert ledger.snapshot().reserved_input_tokens == 0
