"""Bounded, process-local demo authentication. Restart invalidates all sessions."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

HASH_PATTERN = re.compile(r"scrypt\$[0-9a-f]{32}\$[0-9a-f]{64}\Z")


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not HASH_PATTERN.fullmatch(encoded) or len(password) > 256:
        return False
    _, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
    )
    return hmac.compare_digest(actual, bytes.fromhex(expected))


def derive_id(key: str, purpose: str, value: str) -> str:
    return hmac.new(key.encode(), f"{purpose}\0{value}".encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Session:
    user: str
    csrf: str
    expires_at: float


class SessionStore:
    def __init__(self, *, ttl: int, capacity: int) -> None:
        self.ttl = ttl
        self.capacity = capacity
        self._sessions: dict[str, Session] = {}

    def create(self, user: str, *, now: float | None = None) -> tuple[str, Session]:
        now = time.time() if now is None else now
        self._sessions = {
            key: item for key, item in self._sessions.items() if item.expires_at > now
        }
        if len(self._sessions) >= self.capacity:
            raise OverflowError("Session capacity reached")
        token = secrets.token_urlsafe(32)
        session = Session(user, secrets.token_urlsafe(32), now + self.ttl)
        self._sessions[hashlib.sha256(token.encode()).hexdigest()] = session
        return token, session

    def get(self, token: str, *, now: float | None = None) -> Session | None:
        if len(token) > 128:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        session = self._sessions.get(key)
        now = time.time() if now is None else now
        if session is not None and session.expires_at <= now:
            self._sessions.pop(key, None)
            return None
        return session

    def revoke(self, token: str) -> None:
        self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


class WindowLimiter:
    def __init__(self, *, capacity: int = 4096) -> None:
        self.capacity = capacity
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, *, maximum: int, window: int = 60, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._buckets = {key: item for key, item in self._buckets.items() if item[0] > now}
        if key not in self._buckets and len(self._buckets) >= self.capacity:
            return False
        expires, count = self._buckets.get(key, (now + window, 0))
        if count >= maximum:
            return False
        self._buckets[key] = (expires, count + 1)
        return True
