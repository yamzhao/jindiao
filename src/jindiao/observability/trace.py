"""Concurrency-safe, privacy-preserving JSONL run traces."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue

from jindiao.security import redact_json, redact_text

if TYPE_CHECKING:
    from jindiao.contracts.events import RunEvent

_ATTRIBUTE_ALLOWLIST = {
    "capability",
    "conflicts_detected",
    "domain",
    "duration_ms",
    "error_code",
    "evidence_count",
    "evidence_ids",
    "message",
    "operation",
    "phase",
    "repairs_completed",
    "repairs_requested",
    "result_count",
    "source_status",
    "status",
    "token_count",
    "tool_calls",
}


def _safe_attributes(attributes: Mapping[str, object] | None) -> dict[str, JsonValue]:
    if attributes is None:
        return {}
    return {
        key: redact_json(value) for key, value in attributes.items() if key in _ATTRIBUTE_ALLOWLIST
    }


class JsonlRunTrace:
    """Append one public event per line under a single run directory."""

    def __init__(
        self,
        *,
        path: Path,
        request_id: str,
        run_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self.request_id = request_id
        self.run_id = run_id
        self._clock = clock
        self._sequence = 0
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def emit(
        self,
        event_type: str,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        tool_id: str | None = None,
        evidence_id: str | None = None,
        attributes: Mapping[str, object] | None = None,
        payload: Mapping[str, object] | None = None,
        public_sequence: int | None = None,
    ) -> None:
        if not event_type:
            raise ValueError("event_type must not be empty")
        with self._lock:
            self._sequence += 1
            record = {
                "timestamp": self._clock().isoformat(),
                "sequence": self._sequence,
                "event_type": event_type,
                "request_id": self.request_id,
                "run_id": self.run_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "tool_id": tool_id,
                "evidence_id": evidence_id,
                "attributes": _safe_attributes(attributes),
                "payload": redact_json(payload or {}),
                "public_sequence": public_sequence,
            }
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def emit_run_event(self, event: RunEvent) -> None:
        """Persist the exact safe payload produced for SSE as a correlated JSONL event."""

        if event.request_id != self.request_id or event.run_id != self.run_id:
            raise ValueError("public event correlation does not match this JSONL trace")
        self.emit(
            event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            payload=event.payload,
            public_sequence=event.sequence,
        )


__all__ = ["JsonlRunTrace", "redact_json", "redact_text"]
