"""Transport-neutral notification delivery and business history helpers for MAX."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender, MaxSendResult
from max_barbershop_bot.repositories.users import UserProfileUpdate, UsersRepository

logger = logging.getLogger(__name__)

PLATFORM_MAX = "max"
RecipientKind = Literal["user", "chat"]

BOOKING_CONFIRMATION_IMMEDIATE = "booking_confirmation_immediate"
BOOKING_REMINDER_48H = "booking_reminder_48h"
BOOKING_REMINDER_6H = "booking_reminder_6h"
BOOKING_REMINDER_2H = "booking_reminder_2h"
BOOKING_NOTIFICATION_TYPES = {
    BOOKING_CONFIRMATION_IMMEDIATE,
    BOOKING_REMINDER_48H,
    BOOKING_REMINDER_6H,
    BOOKING_REMINDER_2H,
}
_ACTIVE_HISTORY_STATUSES = {"scheduled", "sending", "sent"}


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


@dataclass(frozen=True)
class NotificationHistoryRecord:
    """Business-level notification history row used for duplicate prevention."""

    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    notification_type: str
    status: str
    max_user_id: str | None = None
    chat_id: str | None = None
    yclients_client_id: str | None = None
    scheduled_for: str | None = None
    sent_at: str | None = None
    delivery_status_code: int | None = None
    delivery_error_code: str | None = None
    delivery_error_message: str | None = None
    message_id: str | None = None
    attempts: int = 0
    is_blocked: bool = False
    is_stopped: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


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


async def send_business_notification(
    sender: MaxMessageSender,
    *,
    database_path: str,
    platform_user_id: str,
    yclients_record_id: str,
    notification_type: str,
    text: str,
    recipient_type: RecipientKind,
    recipient_id: int | str,
    platform: str = PLATFORM_MAX,
    max_user_id: str | None = None,
    chat_id: str | None = None,
    yclients_client_id: str | None = None,
    scheduled_for: str | None = None,
    keyboard: MaxInlineKeyboard | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> NotificationHistoryRecord | None:
    """Send one business notification once and persist both history and delivery."""

    existing = get_notification_history(
        database_path,
        platform=platform,
        platform_user_id=platform_user_id,
        yclients_record_id=yclients_record_id,
        notification_type=notification_type,
    )
    if existing and existing.status in _ACTIVE_HISTORY_STATUSES:
        logger.info(
            "notification_duplicate_skipped platform=%s platform_user_id=%s yclients_record_id=%s "
            "notification_type=%s status=%s",
            platform,
            platform_user_id,
            yclients_record_id,
            notification_type,
            existing.status,
        )
        return existing
    if existing and (existing.is_blocked or existing.is_stopped):
        logger.info(
            "notification_blocked_skipped platform=%s platform_user_id=%s yclients_record_id=%s "
            "notification_type=%s blocked=%s stopped=%s",
            platform,
            platform_user_id,
            yclients_record_id,
            notification_type,
            existing.is_blocked,
            existing.is_stopped,
        )
        return existing

    history_id = reserve_notification_history(
        database_path,
        platform=platform,
        platform_user_id=platform_user_id,
        max_user_id=max_user_id,
        chat_id=chat_id,
        yclients_record_id=yclients_record_id,
        yclients_client_id=yclients_client_id,
        notification_type=notification_type,
        scheduled_for=scheduled_for,
        metadata=metadata,
    )
    if history_id is None:
        return get_notification_history(
            database_path,
            platform=platform,
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            notification_type=notification_type,
        )

    result = await send_notification(
        sender,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        text=text,
        database_path=database_path,
        keyboard=keyboard,
        platform_user_id=platform_user_id,
        max_user_id=max_user_id,
        chat_id=chat_id,
        message_type=notification_type,
        metadata={**dict(metadata or {}), "yclients_record_id": yclients_record_id},
    )
    status = "sent" if result.ok else "blocked" if result.is_blocked else "stopped" if result.is_stopped else "failed"
    updated = mark_notification_history_result(database_path, history_id=history_id, status=status, result=result)
    if result.is_blocked or result.is_stopped:
        _disable_user_notifications(database_path, platform=platform, platform_user_id=platform_user_id)
    return updated


def reserve_notification_history(
    database_path: str,
    *,
    platform: str,
    platform_user_id: str,
    yclients_record_id: str,
    notification_type: str,
    max_user_id: str | None = None,
    chat_id: str | None = None,
    yclients_client_id: str | None = None,
    scheduled_for: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> int | None:
    """Create a sending marker; return None when the unique key already exists."""

    with closing(_connect(database_path)) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO notification_history (
                platform, platform_user_id, max_user_id, chat_id, yclients_record_id,
                yclients_client_id, notification_type, scheduled_for, status, attempts, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sending', 0, ?)
            """,
            (
                _required_text(platform, "platform"),
                _required_text(platform_user_id, "platform_user_id"),
                _optional_text(max_user_id),
                _optional_text(chat_id),
                _required_text(yclients_record_id, "yclients_record_id"),
                _optional_text(yclients_client_id),
                _required_text(notification_type, "notification_type"),
                _optional_text(scheduled_for),
                _dump_metadata(metadata or {}),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None


def mark_notification_history_result(
    database_path: str,
    *,
    history_id: int,
    status: str,
    result: MaxSendResult,
) -> NotificationHistoryRecord | None:
    """Save MAX delivery result on a business notification history row."""

    sent_at = datetime.now(UTC).isoformat() if result.ok else None
    with closing(_connect(database_path)) as connection:
        connection.execute(
            """
            UPDATE notification_history
            SET status = ?, sent_at = COALESCE(?, sent_at), delivery_status_code = ?,
                delivery_error_code = ?, delivery_error_message = ?, message_id = ?, attempts = ?,
                is_blocked = ?, is_stopped = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                _required_text(status, "status"),
                sent_at,
                result.status_code,
                result.error_code,
                result.error_message,
                result.message_id,
                max(1, result.attempts),
                int(result.is_blocked),
                int(result.is_stopped),
                history_id,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM notification_history WHERE id = ?", (history_id,)).fetchone()
        return _row_to_history(row)


def mark_notification_history_skipped(
    database_path: str,
    *,
    platform: str,
    platform_user_id: str,
    yclients_record_id: str,
    notification_type: str,
    scheduled_for: str | None,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> NotificationHistoryRecord | None:
    """Record a non-delivery decision for a business notification."""

    with closing(_connect(database_path)) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_history (
                platform, platform_user_id, yclients_record_id, notification_type,
                scheduled_for, status, delivery_error_message, metadata_json
            ) VALUES (?, ?, ?, ?, ?, 'skipped', ?, ?)
            """,
            (
                _required_text(platform, "platform"),
                _required_text(platform_user_id, "platform_user_id"),
                _required_text(yclients_record_id, "yclients_record_id"),
                _required_text(notification_type, "notification_type"),
                _optional_text(scheduled_for),
                reason[:240],
                _dump_metadata(metadata or {}),
            ),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT * FROM notification_history
            WHERE platform = ? AND platform_user_id = ? AND yclients_record_id = ? AND notification_type = ?
            LIMIT 1
            """,
            (platform, platform_user_id, yclients_record_id, notification_type),
        ).fetchone()
        return _row_to_history(row)


def get_notification_history(
    database_path: str,
    *,
    platform: str,
    platform_user_id: str,
    yclients_record_id: str,
    notification_type: str,
) -> NotificationHistoryRecord | None:
    """Return one history row by the business duplicate-prevention key."""

    with closing(_connect(database_path)) as connection:
        row = connection.execute(
            """
            SELECT * FROM notification_history
            WHERE platform = ? AND platform_user_id = ? AND yclients_record_id = ? AND notification_type = ?
            LIMIT 1
            """,
            (
                _required_text(platform, "platform"),
                _required_text(platform_user_id, "platform_user_id"),
                _required_text(yclients_record_id, "yclients_record_id"),
                _required_text(notification_type, "notification_type"),
            ),
        ).fetchone()
        return _row_to_history(row)


def save_delivery_result(database_path: str, delivery: NotificationDeliveryResult) -> int:
    """Save a delivery result to the notification_delivery SQLite table."""

    metadata_json = _dump_metadata(delivery.metadata)
    with closing(_connect(database_path)) as connection:
        cursor = connection.execute(
            """
            INSERT INTO notification_delivery (
                platform, platform_user_id, max_user_id, chat_id, message_type,
                recipient_type, recipient_id, status, status_code, error_code, error_message,
                attempts, message_id, is_blocked, is_stopped, metadata_json
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


def _disable_user_notifications(database_path: str, *, platform: str, platform_user_id: str) -> None:
    try:
        UsersRepository(database_path).update_profile(
            platform_user_id,
            UserProfileUpdate(notifications_enabled=False),
            platform=platform,
        )
    except Exception:
        logger.warning(
            "notification_disable_user_failed platform=%s platform_user_id=%s",
            platform,
            platform_user_id,
            exc_info=True,
        )


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_history(row: sqlite3.Row | None) -> NotificationHistoryRecord | None:
    if row is None:
        return None
    return NotificationHistoryRecord(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        max_user_id=_row_optional_text(row, "max_user_id"),
        chat_id=_row_optional_text(row, "chat_id"),
        yclients_record_id=str(row["yclients_record_id"]),
        yclients_client_id=_row_optional_text(row, "yclients_client_id"),
        notification_type=str(row["notification_type"]),
        scheduled_for=_row_optional_text(row, "scheduled_for"),
        sent_at=_row_optional_text(row, "sent_at"),
        status=str(row["status"]),
        delivery_status_code=_row_optional_int(row, "delivery_status_code"),
        delivery_error_code=_row_optional_text(row, "delivery_error_code"),
        delivery_error_message=_row_optional_text(row, "delivery_error_message"),
        message_id=_row_optional_text(row, "message_id"),
        attempts=int(row["attempts"] or 0),
        is_blocked=bool(row["is_blocked"]),
        is_stopped=bool(row["is_stopped"]),
        metadata=_load_metadata(_row_optional_text(row, "metadata_json")),
        created_at=_row_optional_text(row, "created_at"),
        updated_at=_row_optional_text(row, "updated_at"),
    )


def _dump_metadata(metadata: Mapping[str, Any]) -> str | None:
    if not metadata:
        return None
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, default=str)


def _load_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _required_text(value: str, field_name: str) -> str:
    clean = str(value).strip()
    if not clean:
        raise ValueError(f"{field_name} не может быть пустым")
    return clean


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _row_optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = row[column]
    return str(value) if value is not None else None


def _row_optional_int(row: sqlite3.Row, column: str) -> int | None:
    value = row[column]
    return int(value) if value is not None else None
