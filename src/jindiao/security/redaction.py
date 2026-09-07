"""One recursive redaction engine with source and public-output profiles."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import JsonValue

_REDACTED = "[REDACTED]"
_SECRET_KEYS = frozenset(
    {
        "authorization",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "credential",
    }
)
_PRIVATE_OUTPUT_KEYS = frozenset(
    {
        "chainofthought",
        "prompt",
        "prompttext",
        "reasoning",
        "reasoningcontent",
        "rawmcpresponse",
        "rawresponse",
        "rawwebresponse",
        "systemprompt",
        "userprompt",
        "webpagecontent",
        "webpagehtml",
    }
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|token|secret)"
    r"(\s*[:=]\s*)([^\s,;}\]]+)"
)
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+\-/]+=*", re.I)
_TOKEN_VALUE = re.compile(
    r"\b(?:mcp[a-z0-9-]*_|sk-[a-z0-9_-]*)(?:[a-z0-9_-]{8,})\b",
    re.I,
)


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _redact_inline_text(value: str) -> str:
    assigned = _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
        value,
    )
    assigned = _BEARER.sub(_REDACTED, assigned)
    return _TOKEN_VALUE.sub(_REDACTED, assigned)


def redact_text(value: str) -> str:
    """Redact a public free-text field as a whole when it contains a secret."""

    redacted = _redact_inline_text(value)
    return _REDACTED if redacted != value else value


def redact_sensitive(value: object) -> object:
    """Sanitize source diagnostics while preserving non-secret external data."""

    if isinstance(value, str):
        return _redact_inline_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                _REDACTED if _normalized_key(key) in _SECRET_KEYS else redact_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def redact_json(value: object) -> JsonValue:
    """Produce JSON-safe public data without prompts, reasoning, raw payloads or secrets."""

    if value is None or isinstance(value, int | float | bool):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _SECRET_KEYS or normalized in _PRIVATE_OUTPUT_KEYS:
                continue
            result[str(key)] = redact_json(item)
        return result
    if isinstance(value, list | tuple):
        return [redact_json(item) for item in value]
    return redact_text(str(value))


__all__ = ["redact_json", "redact_sensitive", "redact_text"]
