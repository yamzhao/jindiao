"""Shared single-agent execution boundary over openJiuwen ``ReActAgent``.

Compatibility is pinned by ``test_openjiuwen_runtime_probe``: openJiuwen 0.1.17
supports ``Runner.run_agent[_streaming]`` for a configured ``ReActAgent``;
executable tools must be registered as a ``ToolCard`` plus resource through
``AbilityManager.add_ability``; provider usage is emitted in ``llm_usage``;
and early cancellation still requires session and owned-tool cleanup.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from openjiuwen.core.runner import Runner
from pydantic import AwareDatetime, BaseModel, Field, JsonValue, field_validator

from jindiao.application.errors import AgentTimeoutError
from jindiao.contracts.base import ContractModel
from jindiao.contracts.results import AgentResultPhase
from jindiao.security import redact_json

from .base import CancellationToken, check_cancellation


class AgentExecutionEventType(StrEnum):
    STARTED = "agent.started"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_REQUEST_COMPLETED = "model.request.completed"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    OUTPUT = "agent.output"
    COMPLETED = "agent.completed"
    FAILED = "agent.failed"


class AgentExecutionRequest(ContractModel):
    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    phase: AgentResultPhase
    session_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    task_ids: tuple[str, ...] = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(gt=0)


class AgentExecutionEvent(ContractModel):
    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    phase: AgentResultPhase
    sequence: int = Field(ge=1)
    event_type: AgentExecutionEventType
    occurred_at: AwareDatetime
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str | None = None
    check_id: str | None = None
    tool_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload", mode="before")
    @classmethod
    def remove_private_fields(cls, value: object) -> dict[str, JsonValue]:
        safe = redact_json(value)
        if isinstance(safe, dict):
            return safe
        return {"value": safe}


class AgentExecutionRuntime(Protocol):
    def stream(
        self,
        agent: object,
        request: AgentExecutionRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncIterator[AgentExecutionEvent]: ...


def _chunk_payload(chunk: object) -> dict[str, JsonValue]:
    raw = getattr(chunk, "payload", chunk)
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    elif not isinstance(raw, Mapping):
        raw = {"value": raw}
    safe = redact_json(raw)
    if isinstance(safe, dict):
        return safe
    return {"value": safe}


def _event_type(chunk: object) -> AgentExecutionEventType | None:
    chunk_type = str(getattr(chunk, "type", "") or "")
    if chunk_type == "llm_reasoning":
        return None
    if chunk_type in {"llm_usage", "context.usage"}:
        return AgentExecutionEventType.MODEL_REQUEST_COMPLETED
    if chunk_type in {"tool_call", "tool.call.started"}:
        return AgentExecutionEventType.TOOL_CALL_STARTED
    if chunk_type in {"tool_result", "tool.call.completed"}:
        return AgentExecutionEventType.TOOL_CALL_COMPLETED
    return AgentExecutionEventType.OUTPUT


class OpenJiuwenAgentExecutionRuntime:
    """Translate ReAct streaming chunks into one privacy-safe public schema."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock

    async def stream(
        self,
        agent: object,
        request: AgentExecutionRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncIterator[AgentExecutionEvent]:
        sequence = 0
        check_cancellation(cancellation_token)

        def event(
            event_type: AgentExecutionEventType,
            *,
            payload: dict[str, JsonValue] | None = None,
        ) -> AgentExecutionEvent:
            nonlocal sequence
            sequence += 1
            return AgentExecutionEvent(
                run_id=request.run_id,
                agent_id=request.agent_id,
                role=request.role,
                phase=request.phase,
                sequence=sequence,
                event_type=event_type,
                occurred_at=self._clock(),
                prompt_version=request.prompt_version,
                prompt_sha256=request.prompt_sha256,
                payload=payload or {},
            )

        yield event(
            AgentExecutionEventType.STARTED,
            payload={"task_ids": list(request.task_ids)},
        )
        source = Runner.run_agent_streaming(
            agent=agent,
            inputs={
                "query": request.query,
                "conversation_id": request.session_id,
            },
            session=request.session_id,
        )
        try:
            async with asyncio.timeout(request.timeout_seconds):
                async for chunk in source:
                    check_cancellation(cancellation_token)
                    event_type = _event_type(chunk)
                    if event_type is None:
                        continue
                    yield event(event_type, payload=_chunk_payload(chunk))
            yield event(AgentExecutionEventType.COMPLETED)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            public_error = AgentTimeoutError(
                "Agent execution timed out",
                details={
                    "agent_id": request.agent_id,
                    "phase": request.phase.value,
                    "timeout_seconds": request.timeout_seconds,
                },
            )
            yield event(
                AgentExecutionEventType.FAILED,
                payload={"error_type": "TimeoutError", "message": public_error.message},
            )
            raise public_error from error
        except Exception as error:
            yield event(
                AgentExecutionEventType.FAILED,
                payload={"error_type": type(error).__name__, "message": str(error)},
            )
            raise
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                with suppress(Exception):
                    await close()
            clear_session = getattr(agent, "clear_session", None)
            if clear_session is not None:
                with suppress(Exception):
                    await clear_session(request.session_id)
            ability_manager = getattr(agent, "ability_manager", None)
            teardown_tools = getattr(ability_manager, "teardown_tools", None)
            if teardown_tools is not None:
                teardown_tools()


__all__ = [
    "AgentExecutionEvent",
    "AgentExecutionEventType",
    "AgentExecutionRequest",
    "AgentExecutionRuntime",
    "OpenJiuwenAgentExecutionRuntime",
]
