"""Internal normalized events for MAX updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from max_barbershop_bot.max_api.models import MaxUpdate

logger = logging.getLogger(__name__)

SUPPORTED_UPDATE_TYPES = {"bot_started", "message_created", "message_callback"}


@dataclass(frozen=True)
class NormalizedEvent:
    """Transport-independent event object used by MAX bot handlers."""

    update_type: str
    platform_user_id: str | None
    max_user_id: str | None
    chat_id: str | None
    text: str | None
    callback_payload: str | None
    callback_id: str | None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    attachments: list[Any] = field(default_factory=list)
    raw_update: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def normalize_update(update: MaxUpdate | dict[str, Any]) -> NormalizedEvent:
    """Convert a raw MAX update or transport model into a normalized event."""

    raw_update = _raw_update(update)
    try:
        max_update = update if isinstance(update, MaxUpdate) else MaxUpdate.from_payload(raw_update)
    except (TypeError, ValueError, AttributeError) as error:
        logger.warning("Unknown MAX update received: type=%s", _safe_update_type(raw_update))
        logger.debug("MAX update normalization failed: %s", error)
        return _unknown_event(raw_update)

    if max_update.update_type not in SUPPORTED_UPDATE_TYPES:
        logger.warning("Unknown MAX update received: type=%s", _safe_update_type(raw_update))
        return _unknown_event(raw_update)

    if max_update.update_type == "message_created":
        return _message_created_event(max_update, raw_update)
    if max_update.update_type == "message_callback":
        return _message_callback_event(max_update, raw_update)
    if max_update.update_type == "bot_started":
        return _bot_started_event(max_update, raw_update)

    return _unknown_event(raw_update)


def _message_created_event(update: MaxUpdate, raw_update: dict[str, Any]) -> NormalizedEvent:
    message = update.message
    user_id = _to_str(message.user_id if message is not None else None)
    chat_id = _to_str((message.chat_id if message is not None else None) or update.chat_id)
    return NormalizedEvent(
        update_type="message_created",
        platform_user_id=user_id,
        max_user_id=user_id,
        chat_id=chat_id,
        text=message.text if message is not None else None,
        callback_payload=None,
        callback_id=None,
        first_name=message.first_name if message is not None else None,
        last_name=message.last_name if message is not None else None,
        username=message.username if message is not None else None,
        attachments=list(message.attachments) if message is not None else [],
        raw_update=raw_update,
    )


def _message_callback_event(update: MaxUpdate, raw_update: dict[str, Any]) -> NormalizedEvent:
    callback = update.callback
    message = callback.message if callback is not None else None
    user_id = _to_str(callback.user_id if callback is not None else None)
    chat_id = _to_str((message.chat_id if message is not None else None) or update.chat_id)
    return NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user_id,
        max_user_id=user_id,
        chat_id=chat_id,
        text=message.text if message is not None else None,
        callback_payload=callback.payload if callback is not None else None,
        callback_id=callback.callback_id if callback is not None else None,
        first_name=message.first_name if message is not None else None,
        last_name=message.last_name if message is not None else None,
        username=message.username if message is not None else None,
        attachments=list(message.attachments) if message is not None else [],
        raw_update=raw_update,
    )


def _bot_started_event(update: MaxUpdate, raw_update: dict[str, Any]) -> NormalizedEvent:
    user_id = _to_str(update.user.user_id if update.user is not None else None)
    return NormalizedEvent(
        update_type="bot_started",
        platform_user_id=user_id,
        max_user_id=user_id,
        chat_id=_to_str(update.chat_id),
        text=None,
        callback_payload=_payload_to_str(raw_update.get("payload")),
        callback_id=None,
        first_name=update.user.first_name if update.user is not None else None,
        last_name=update.user.last_name if update.user is not None else None,
        username=update.user.username if update.user is not None else None,
        attachments=[],
        raw_update=raw_update,
    )


def _unknown_event(raw_update: dict[str, Any]) -> NormalizedEvent:
    return NormalizedEvent(
        update_type="unknown",
        platform_user_id=None,
        max_user_id=None,
        chat_id=None,
        text=None,
        callback_payload=None,
        callback_id=None,
        first_name=update.user.first_name if update.user is not None else None,
        last_name=update.user.last_name if update.user is not None else None,
        username=update.user.username if update.user is not None else None,
        attachments=[],
        raw_update=raw_update,
    )


def _raw_update(update: MaxUpdate | dict[str, Any]) -> dict[str, Any]:
    if isinstance(update, MaxUpdate):
        return update._raw
    if isinstance(update, dict):
        return update
    return {}


def _safe_update_type(raw_update: dict[str, Any]) -> str:
    update_type = raw_update.get("update_type")
    return str(update_type) if update_type is not None else "<missing>"


def _to_str(value: int | str | None) -> str | None:
    return str(value) if value is not None else None


def _payload_to_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
