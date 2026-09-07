"""Cancellation-aware encoding of RunEvents for EventSourceResponse."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from jindiao.contracts.events import RunEvent

_END = object()


async def stream_sse_events(
    source: AsyncIterator[RunEvent],
    *,
    disconnect_check: Callable[[], Awaitable[bool]],
    poll_interval: float = 0.1,
) -> AsyncIterator[dict[str, Any]]:
    """Cancel the producer as soon as the client connection disappears."""

    queue: asyncio.Queue[RunEvent | object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in source:
                await queue.put(event)
        finally:
            await queue.put(_END)

    producer = asyncio.create_task(produce())
    try:
        while True:
            if await disconnect_check():
                producer.cancel()
                with suppress(asyncio.CancelledError):
                    await producer
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=poll_interval)
            except TimeoutError:
                continue
            if item is _END:
                break
            if not isinstance(item, RunEvent):
                continue
            event_name = (
                item.event_type.value if hasattr(item.event_type, "value") else str(item.event_type)
            )
            yield {
                "event": event_name,
                "id": str(item.sequence),
                "retry": 3000,
                "data": json.dumps(
                    item.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
    finally:
        if not producer.done():
            producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
        if isinstance(source, AsyncGenerator):
            await source.aclose()


__all__ = ["stream_sse_events"]
