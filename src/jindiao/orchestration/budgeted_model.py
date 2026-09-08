"""Model proxy that accounts every formal Agent request in one Run budget."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from jindiao.application.errors import AgentExecutionError
from jindiao.orchestration.base import BudgetLedger, LLMReservation

_DEFAULT_MAX_OUTPUT_TOKENS = 10_000
_LOCAL_PARAMETERS = frozenset({"output_parser", "parser"})
_OUTPUT_LIMITS = ("max_tokens", "max_output_tokens", "max_completion_tokens")


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _usage_tokens(message: object) -> tuple[int | None, int | None, bool]:
    usage = _field(message, "usage_metadata")
    if usage is None:
        usage = _field(message, "usage")
    if usage is None:
        response = _field(message, "response")
        usage = _field(response, "usage_metadata") or _field(response, "usage")
    input_value = _field(usage, "input_tokens")
    output_value = _field(usage, "output_tokens")
    if input_value is None:
        input_value = _field(usage, "prompt_tokens")
    if output_value is None:
        output_value = _field(usage, "completion_tokens")
    input_tokens = input_value if type(input_value) is int and input_value >= 0 else None
    output_tokens = output_value if type(output_value) is int and output_value >= 0 else None
    return input_tokens, output_tokens, input_tokens is not None and output_tokens is not None


def _text_payload(value: object) -> object:
    """Serialize supported request data without coercing unknown objects to empty text."""

    if isinstance(value, BaseModel):
        return _text_payload(value.model_dump(mode="python", exclude_none=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("request mapping keys must be strings")
        return {key: _text_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text_payload(item) for item in value]
    raise TypeError("unsupported request value")


def _estimate_input_tokens(arguments: Mapping[str, object], configuration: object) -> int:
    # UTF-8 bytes upper-bound text tokens without a provider-specific tokenizer.
    # Include framing headroom and tool/schema/config text, not merely user content.
    payload = _text_payload({"request": arguments, "configuration": configuration})
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return len(encoded.encode("utf-8")) + 256 + 16 * (encoded.count("{") + encoded.count("["))


@dataclass
class _CallUsage:
    dispatched: bool = False
    succeeded: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_usage: bool = False

    def observe(self, value: object) -> None:
        input_tokens, output_tokens, known = _usage_tokens(value)
        if input_tokens is not None:
            self.input_tokens = max(self.input_tokens or 0, input_tokens)
        if output_tokens is not None:
            self.output_tokens = max(self.output_tokens or 0, output_tokens)
        self.provider_usage = self.provider_usage or known


class BudgetedModel:
    """Keep provider calls and reported usage inside the shared atomic ledger."""

    def __init__(self, model: Any, *, budget_ledger: BudgetLedger) -> None:
        self._model = model
        self._budget_ledger = budget_ledger

    async def invoke(self, *args: object, **kwargs: object) -> Any:
        async with self._accounted_call(self._model.invoke, "model.invoke", args, kwargs) as call:
            bound, usage = call
            usage.dispatched = True
            response = await self._model.invoke(*bound.args, **bound.kwargs)
            usage.observe(response)
            usage.succeeded = True
            return response

    async def stream(self, *args: object, **kwargs: object) -> AsyncGenerator[Any, None]:
        async with self._accounted_call(self._model.stream, "model.stream", args, kwargs) as call:
            bound, usage = call
            usage.dispatched = True
            source = self._model.stream(*bound.args, **bound.kwargs)
            try:
                iterator = aiter(source)
                while True:
                    try:
                        remaining = self._budget_ledger.snapshot().deadline_remaining_ms / 1000
                        # SDKs can pull each chunk in a different Task. Keep the
                        # task-affine timeout around this await, never across yield.
                        async with asyncio.timeout(remaining):
                            chunk = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    usage.observe(chunk)
                    yield chunk
                usage.succeeded = True
            finally:
                close = getattr(source, "aclose", None)
                if callable(close):
                    await close()

    def _prepare_call(
        self, method: Callable[..., Any], args: tuple[object, ...], kwargs: dict[str, object]
    ) -> tuple[inspect.BoundArguments, list[tuple[dict[str, Any], str]], int, int]:
        bound = inspect.signature(method).bind(*args, **kwargs)
        bound.apply_defaults()
        extra_name = next(
            (
                name
                for name, parameter in bound.signature.parameters.items()
                if parameter.kind is inspect.Parameter.VAR_KEYWORD
            ),
            None,
        )

        def argument_target(name: str) -> dict[str, Any]:
            if name in bound.signature.parameters:
                return bound.arguments
            if extra_name is not None:
                extra: dict[str, Any] = bound.arguments.setdefault(extra_name, {})
                return extra
            raise TypeError("model does not accept a bounded output allowance")

        target = argument_target("max_tokens")
        configured = getattr(self._model, "model_config", None)
        requested = target.get("max_tokens")
        if requested is None:
            requested = _field(configured, "max_tokens")
        if requested is None:
            requested = _DEFAULT_MAX_OUTPUT_TOKENS
        if type(requested) is not int or requested < 1:
            raise ValueError("max_tokens must be a positive integer")
        configuration = _text_payload(configured)
        options = configuration.copy() if isinstance(configuration, dict) else {}
        options.update(bound.arguments)
        if extra_name is not None:
            options.update(bound.arguments.get(extra_name, {}))
        if options.get("n") not in (None, 1):
            raise ValueError("multiple completions cannot be safely bounded")
        targets = [(target, "max_tokens")]
        for name in _OUTPUT_LIMITS[1:]:
            if options.get(name) is not None:
                limit = options[name]
                if type(limit) is not int or limit < 1:
                    raise ValueError("output limits must be positive integers")
                requested = min(requested, limit)
                targets.append((argument_target(name), name))
        body = options.get("extra_body")
        if body is not None:
            if not isinstance(body, Mapping):
                raise TypeError("extra_body must be a request mapping")
            body = dict(body)
            if body.get("n") not in (None, 1):
                raise ValueError("multiple completions cannot be safely bounded")
            argument_target("extra_body")["extra_body"] = body
            for name in _OUTPUT_LIMITS:
                if body.get(name) is not None:
                    limit = body[name]
                    if type(limit) is not int or limit < 1:
                        raise ValueError("output limits must be positive integers")
                    requested = min(requested, limit)
                    targets.append((body, name))
        # These are local SDK postprocessors, not provider request text. All other
        # unknown objects fail closed, including unknown nested message/tool data.
        estimation_arguments = {
            key: value for key, value in bound.arguments.items() if key not in _LOCAL_PARAMETERS
        }
        if extra_name is not None and extra_name in estimation_arguments:
            estimation_arguments[extra_name] = {
                key: value
                for key, value in estimation_arguments[extra_name].items()
                if key not in _LOCAL_PARAMETERS
            }
        estimated_input = _estimate_input_tokens(estimation_arguments, configured)
        return bound, targets, estimated_input, requested

    @asynccontextmanager
    async def _accounted_call(
        self,
        method: Callable[..., Any],
        operation: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> AsyncIterator[tuple[inspect.BoundArguments, _CallUsage]]:
        try:
            bound, targets, estimated_input, requested_output = self._prepare_call(
                method, args, kwargs
            )
        except Exception:
            raise AgentExecutionError(
                "cannot safely estimate or bound model request",
                details={"operation": operation, **self._budget_ledger.error_details()},
            ) from None
        reservation = await self._budget_ledger.reserve_llm_request(
            operation, input_tokens=estimated_input, output_tokens=requested_output
        )
        for target, name in targets:
            target[name] = reservation.output_tokens
        usage = _CallUsage()
        failure: BaseException | None = None
        try:
            async with self._budget_ledger.operation_slot(operation):
                if operation == "model.stream":
                    yield bound, usage
                else:
                    remaining = self._budget_ledger.snapshot().deadline_remaining_ms / 1000
                    async with asyncio.timeout(remaining):
                        yield bound, usage
        except BaseException as error:
            failure = error
            usage.observe(error)
            raise
        finally:
            try:
                await self._finish(reservation, usage)
            except BaseException as error:
                if failure is None:
                    self._attach_cost(error)
                    raise
            finally:
                if failure is not None:
                    self._attach_cost(failure)

    async def _finish(self, reservation: LLMReservation, usage: _CallUsage) -> None:
        async def settle() -> None:
            if usage.dispatched:
                await self._budget_ledger.complete_llm_request(
                    reservation,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    provider_usage=usage.provider_usage,
                    succeeded=usage.succeeded,
                )
            else:
                await self._budget_ledger.release_llm_request(reservation)

        task = asyncio.create_task(settle())
        cancelled: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancelled = error
        task.result()
        if cancelled is not None:
            raise cancelled

    def _attach_cost(self, error: BaseException) -> None:
        existing = error.details if isinstance(error, AgentExecutionError) else {}
        error.details = {**existing, **self._budget_ledger.error_details()}  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


__all__ = ["BudgetedModel"]
