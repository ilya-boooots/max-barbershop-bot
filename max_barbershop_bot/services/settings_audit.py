"""Safe settings audit helper for MAX admin flows."""

from __future__ import annotations

import logging
from os import getenv
from typing import Any, Mapping

from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.repositories.settings_audit import SettingsAuditRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX

logger = logging.getLogger(__name__)

_FORBIDDEN_METADATA_WORDS = (
    "partner_token",
    "user_token",
    "max_bot_token",
    "authorization",
    "password",
    "secret",
    "raw_payload",
    "phone",
)


def log_settings_action(
    *,
    actor_platform_user_id: str | None,
    actor_role: str | None,
    action: str,
    section: str | None = None,
    target_platform_user_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    platform: str = PLATFORM_MAX,
    database_path: str | None = None,
) -> None:
    """Write a safe settings audit row and never break the user flow."""

    try:
        SettingsAuditRepository(database_path or _database_path()).create(
            platform=platform,
            actor_platform_user_id=actor_platform_user_id,
            actor_role=actor_role,
            action=action,
            section=section,
            target_platform_user_id=target_platform_user_id,
            metadata=_sanitize_metadata(metadata),
        )
    except Exception as exc:  # noqa: BLE001 - audit must not interrupt UX.
        logger.warning(
            "Settings audit write failed safely: action=%s section=%s error_class=%s",
            action,
            section,
            type(exc).__name__,
        )


def _sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if _is_forbidden_key(key_text):
            safe[f"{key_text}_present"] = bool(value)
            continue
        safe[key_text] = _safe_value(value)
    return safe


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in _FORBIDDEN_METADATA_WORDS)


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in list(value)[:20]]
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in list(value.items())[:20] if not _is_forbidden_key(str(key))}
    return str(value)


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
