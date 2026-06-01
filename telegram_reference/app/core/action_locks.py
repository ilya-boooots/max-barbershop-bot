from __future__ import annotations

import time

_LOCKS: dict[str, float] = {}


def _key(parts: tuple[object, ...]) -> str:
    return "|".join(str(part) for part in parts)


def acquire_action_lock(*parts: object, ttl_s: float = 4.0) -> bool:
    now = time.monotonic()
    key = _key(parts)
    expires_at = _LOCKS.get(key, 0.0)
    if now < expires_at:
        return False
    _LOCKS[key] = now + ttl_s
    return True


def release_action_lock(*parts: object) -> None:
    _LOCKS.pop(_key(parts), None)
