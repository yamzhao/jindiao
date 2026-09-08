"""Adapter from openJiuwen AgentTeams streaming chunks to safe runtime events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Protocol, cast

from openjiuwen.core.runner import Runner
from pydantic import JsonValue

from .base import CancellationToken, TeamRuntimeEvent, check_cancellation

_QUIESCENCE_CHECK_SECONDS = 15.0

_PRIVATE_KEYS = {
    "chain_of_thought",
    "prompt",
    "reasoning",
    "reasoning_content",
    "system_prompt",
}


def _chunk_type(chunk: object) -> str | None:
    value = getattr(chunk, "type", None)
    return str(value) if value else None


def _coordination_established(chunk: object, raw_payload: object) -> bool:
    """Track Runner activation even when tool results use tracer chunks."""

    if isinstance(raw_payload, Mapping) and raw_payload.get("event_type") == "team.runtime_ready":
        return True
    if _chunk_type(chunk) != "tool_result" or not isinstance(raw_payload, Mapping):
        return False
    if raw_payload.get("tool_name") != "build_team":
        return False
    result = str(raw_payload.get("tool_result", "")).casefold()
    return bool(result) and not any(
        marker in result for marker in ("success=false", "failed to build", "internal error")
    )


def _public_json(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _public_json(item)
            for key, item in value.items()
            if str(key).casefold() not in _PRIVATE_KEYS
        }
    if isinstance(value, list | tuple):
        return [_public_json(item) for item in value]
    return str(value)


class TeamRuntimeDriver(Protocol):
    def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
    ) -> AsyncIterator[TeamRuntimeEvent]: ...


class OpenJiuwenTeamRuntime:
    """Run a TeamAgentSpec through the official Runner streaming facade."""

    @staticmethod
    async def _is_quiescent(*, team_name: str, session_id: str) -> bool:
        monitor = await Runner.get_agent_team_monitor(team_name=team_name, session_id=session_id)
        if monitor is None:
            return False
        await monitor.start()
        try:
            tasks = await monitor.get_tasks()
            members = await monitor.get_members()
            return (
                bool(tasks and members)
                and all(task.status in {"completed", "cancelled"} for task in tasks)
                and all(
                    member.status in {"ready", "stopped", "shutdown"}
                    and member.execution_status in {"idle", "completed", "cancelled", "failed"}
                    for member in members
                )
            )
        finally:
            await monitor.stop()

    async def wait_for_tasks_terminal(
        self,
        *,
        team_name: str,
        session_id: str,
        leader_member_name: str,
    ) -> None:
        """Wait until persisted tasks are terminal and teammate rounds are quiescent."""

        monitor = await Runner.get_agent_team_monitor(
            team_name=team_name,
            session_id=session_id,
        )
        if monitor is None:
            raise RuntimeError(f"AgentTeams monitor unavailable for team {team_name}")
        await monitor.start()
        event_stream = monitor.events()
        terminal_statuses = {"completed", "cancelled"}
        active_member_statuses = {
            "starting",
            "busy",
            "restarting",
            "shutdown_requested",
        }
        try:
            while True:
                tasks = await monitor.get_tasks()
                members = await monitor.get_members()
                teammates_quiescent = all(
                    member.status not in active_member_statuses
                    for member in members
                    if member.member_name != leader_member_name
                )
                if (
                    tasks
                    and all(task.status in terminal_statuses for task in tasks)
                    and teammates_quiescent
                ):
                    return
                try:
                    await anext(event_stream)
                except StopAsyncIteration as error:
                    raise RuntimeError(
                        f"AgentTeams monitor stopped before team {team_name} became terminal"
                    ) from error
        finally:
            close = getattr(event_stream, "aclose", None)
            if close is not None:
                await close()
            await monitor.stop()

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        check_cancellation(cancellation_token)
        source = Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs=inputs,
            session=session_id,
        )
        team_name = getattr(spec, "team_name", None)
        team_created = False
        # The native generator binds contextvars across yields. Keep every
        # iteration and its cleanup in one task instead of spawning per chunk.
        chunks: asyncio.Queue[tuple[bool, object]] = asyncio.Queue(maxsize=1)

        async def produce() -> None:
            try:
                async for chunk in source:
                    await chunks.put((True, chunk))
            except Exception as error:
                await chunks.put((False, error))
            else:
                await chunks.put((False, None))
            finally:
                close_source = getattr(source, "aclose", None)
                if close_source is not None:
                    await close_source()

        producer = asyncio.create_task(produce())
        pending: asyncio.Task[tuple[bool, object]] | None = None
        quiescent_observations = 0
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(chunks.get())
                done, _ = await asyncio.wait({pending}, timeout=_QUIESCENCE_CHECK_SECONDS)
                if not done:
                    check_cancellation(cancellation_token)
                    if (
                        team_created
                        and isinstance(team_name, str)
                        and await self._is_quiescent(team_name=team_name, session_id=session_id)
                    ):
                        quiescent_observations += 1
                    else:
                        quiescent_observations = 0
                    if quiescent_observations >= 2:
                        yield TeamRuntimeEvent(event_type="team.runtime.quiescent")
                        break
                    continue
                has_chunk, chunk = pending.result()
                pending = None
                if not has_chunk:
                    if isinstance(chunk, Exception):
                        raise chunk
                    break
                quiescent_observations = 0
                check_cancellation(cancellation_token)
                raw_payload = getattr(chunk, "payload", chunk)
                established = _coordination_established(chunk, raw_payload)
                if established:
                    team_created = True
                payload_value = _public_json(raw_payload)
                payload = (
                    payload_value if isinstance(payload_value, dict) else {"value": payload_value}
                )
                chunk_type = _chunk_type(chunk)
                event_type = str(
                    payload.get(
                        "event_type",
                        payload.get(
                            "event",
                            f"team.{chunk_type}" if chunk_type else "team.chunk",
                        ),
                    )
                )
                member = payload.get(
                    "member_name",
                    payload.get(
                        "agent_id",
                        payload.get("source", getattr(chunk, "source_member", None)),
                    ),
                )
                yield TeamRuntimeEvent(
                    event_type=event_type,
                    member_name=str(member) if member is not None else None,
                    payload=cast(dict[str, JsonValue], payload),
                )
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
            if not producer.done():
                producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer
            if team_created and isinstance(team_name, str) and team_name:
                await Runner.stop_agent_team(
                    team_name=team_name,
                    session_id=session_id,
                )
            if team_created and isinstance(team_name, str) and team_name:
                deleted = await Runner.delete_agent_team(
                    team_name=team_name,
                    session_ids=[session_id],
                    force=False,
                )
                if not deleted:
                    raise RuntimeError(f"AgentTeams lifecycle cleanup failed for team {team_name}")


class OfflineTeamRuntime:
    """Credential-free deterministic event shell for frozen Mock demonstrations."""

    async def stream(
        self,
        spec: object,
        inputs: dict[str, object],
        *,
        session_id: str,
        cancellation_token: CancellationToken | None = None,
    ) -> AsyncIterator[TeamRuntimeEvent]:
        check_cancellation(cancellation_token)
        del spec
        yield TeamRuntimeEvent(
            event_type="team.runtime_ready",
            member_name="leader",
            payload={"session_id": session_id},
        )
        raw_plan = inputs.get("plan")
        if not isinstance(raw_plan, Mapping):
            return
        raw_tasks = raw_plan.get("tasks")
        if not isinstance(raw_tasks, list):
            return
        for raw_task in raw_tasks:
            check_cancellation(cancellation_token)
            if not isinstance(raw_task, Mapping) or raw_task.get("status") == "skipped":
                continue
            agent_id = str(raw_task.get("assigned_agent", "unknown-agent"))
            yield TeamRuntimeEvent(
                event_type="member.started",
                member_name=agent_id,
                payload={"task_id": str(raw_task.get("task_id", "unknown-task"))},
            )


__all__ = ["OfflineTeamRuntime", "OpenJiuwenTeamRuntime", "TeamRuntimeDriver"]
