"""Transport-neutral notification delivery helpers for MAX safe sends."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, Literal

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender, MaxSendResult

logger = logging.getLogger(__name__)

PLATFORM_MAX = "max"
RecipientKind = Literal["user", "chat"]


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """Persisted delivery view independent from a specific transport client."""

    platform: str
    recipient_type: str
    recipient_id: str
    status: str
    platform_user_id: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None
    message_type: str | None = None
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: int = 1
    message_id: str | None = None
    is_blocked: bool = False
    is_stopped: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_max_result(
        cls,
        result: MaxSendResult,
        *,
        platform_user_id: str | None = None,
        max_user_id: str | None = None,
        chat_id: str | None = None,
        message_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        platform: str = PLATFORM_MAX,
    ) -> "NotificationDeliveryResult":
        """Build a delivery record from MaxSendResult."""

        return cls(
            platform=platform,
            platform_user_id=platform_user_id,
            max_user_id=max_user_id,
            chat_id=chat_id,
            message_type=message_type,
            recipient_type=result.recipient_type,
            recipient_id=result.recipient_id,
            status="sent" if result.ok else "failed",
            status_code=result.status_code,
            error_code=result.error_code,
            error_message=result.error_message,
            attempts=result.attempts,
            message_id=result.message_id,
            is_blocked=result.is_blocked,
            is_stopped=result.is_stopped,
            metadata=metadata or {},
        )


async def send_notification(
    sender: MaxMessageSender,
    *,
    recipient_type: RecipientKind,
    recipient_id: int | str,
    text: str,
    database_path: str | None = None,
    keyboard: MaxInlineKeyboard | None = None,
    attachments: list[Mapping[str, Any]] | None = None,
    format: str | None = None,
    platform_user_id: str | None = None,
    max_user_id: str | None = None,
    chat_id: str | None = None,
    message_type: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MaxSendResult:
    """Send a MAX notification and optionally persist its delivery result."""

    if recipient_type == "user":
        result = await sender.send_to_user(
            recipient_id,
            text,
            keyboard=keyboard,
            attachments=attachments,
            format=format,
            metadata=metadata,
        )
    else:
        result = await sender.send_to_chat(
            recipient_id,
            text,
            keyboard=keyboard,
            attachments=attachments,
            format=format,
            metadata=metadata,
        )

    if database_path is not None:
        delivery = NotificationDeliveryResult.from_max_result(
            result,
            platform_user_id=platform_user_id,
            max_user_id=max_user_id or (str(recipient_id) if recipient_type == "user" else None),
            chat_id=chat_id or (str(recipient_id) if recipient_type == "chat" else None),
            message_type=message_type,
            metadata=metadata,
        )
        try:
            save_delivery_result(database_path, delivery)
        except Exception:
            logger.warning(
                "notification_delivery_save_failed platform=%s recipient_type=%s recipient_id=%s "
                "message_type=%s status=%s",
                PLATFORM_MAX,
                result.recipient_type,
                result.recipient_id,
                message_type,
                delivery.status,
                exc_info=True,
            )

    return result


def save_delivery_result(database_path: str, delivery: NotificationDeliveryResult) -> int:
    """Save a delivery result to the notification_delivery SQLite table."""

    metadata_json = _dump_metadata(delivery.metadata)
    with closing(_connect(database_path)) as connection:
        cursor = connection.execute(
            """
            INSERT INTO notification_delivery (
                platform,
                platform_user_id,
                max_user_id,
                chat_id,
                message_type,
                recipient_type,
                recipient_id,
                status,
                status_code,
                error_code,
                error_message,
                attempts,
                message_id,
                is_blocked,
                is_stopped,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delivery.platform,
                delivery.platform_user_id,
                delivery.max_user_id,
                delivery.chat_id,
                delivery.message_type,
                delivery.recipient_type,
                delivery.recipient_id,
                delivery.status,
                delivery.status_code,
                delivery.error_code,
                delivery.error_message,
                max(1, delivery.attempts),
                delivery.message_id,
                int(delivery.is_blocked),
                int(delivery.is_stopped),
                metadata_json,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _dump_metadata(metadata: Mapping[str, Any]) -> str | None:
    if not metadata:
        return None
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, default=str)
