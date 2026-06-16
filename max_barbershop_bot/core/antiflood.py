"""Small in-memory anti-flood guard for MAX updates."""

from __future__ import annotations

import time

TEXT_THROTTLE_SECONDS = 1.2
CALLBACK_THROTTLE_SECONDS = 0.8

_text_hits: dict[str, float] = {}
_callback_hits: dict[str, float] = {}


def is_text_allowed(platform_user_id: str | None, chat_id: str | None) -> bool:
    """Return False when text messages from the same user/chat are too frequent."""

    return _is_allowed(_key(platform_user_id, chat_id), TEXT_THROTTLE_SECONDS, _text_hits)


def is_callback_allowed(platform_user_id: str | None, chat_id: str | None, payload: str | None = None) -> bool:
    """Return False when callbacks from the same user/chat are too frequent."""

    del payload  # Payload is intentionally not part of Telegram-compatible per-user throttling.
    return _is_allowed(_key(platform_user_id, chat_id), CALLBACK_THROTTLE_SECONDS, _callback_hits)


def _is_allowed(key: str, interval_seconds: float, storage: dict[str, float]) -> bool:
    now = time.monotonic()
    _cleanup(storage, now=now, max_age_seconds=max(interval_seconds * 4, 5.0))
    last_hit = storage.get(key, 0.0)
    if now - last_hit < interval_seconds:
        return False
    storage[key] = now
    return True


def _cleanup(storage: dict[str, float], *, now: float, max_age_seconds: float) -> None:
    expired = [key for key, hit_at in storage.items() if now - hit_at > max_age_seconds]
    for key in expired:
        storage.pop(key, None)


def _key(platform_user_id: str | None, chat_id: str | None) -> str:
    return f"{platform_user_id or 'unknown'}:{chat_id or 'unknown'}"
