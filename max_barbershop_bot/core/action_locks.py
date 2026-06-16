"""Small in-memory action locks for duplicate MAX confirmations."""

from __future__ import annotations

import time

ACTION_IN_PROGRESS_TEXT = "⏳ Уже выполняем действие, секундочку 🙂"
DEFAULT_ACTION_LOCK_TTL_SECONDS = 4
BOOKING_CREATE_LOCK_TTL_SECONDS = 5

_locks: dict[str, float] = {}


def acquire_action_lock(key: str, ttl_seconds: int | float = DEFAULT_ACTION_LOCK_TTL_SECONDS) -> bool:
    """Acquire a lock until ttl expires; return False if still locked."""

    now = time.monotonic()
    _cleanup(now)
    expires_at = _locks.get(key, 0.0)
    if now < expires_at:
        return False
    _locks[key] = now + float(ttl_seconds)
    return True


def release_action_lock(key: str) -> None:
    """Release a lock if present."""

    _locks.pop(key, None)


def is_action_locked(key: str) -> bool:
    """Return whether a lock is currently active."""

    now = time.monotonic()
    _cleanup(now)
    return now < _locks.get(key, 0.0)


def _cleanup(now: float) -> None:
    expired = [key for key, expires_at in _locks.items() if now >= expires_at]
    for key in expired:
        _locks.pop(key, None)
