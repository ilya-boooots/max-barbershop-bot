"""SQLite repository for MAX cancellation recovery events."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from max_barbershop_bot.repositories.users import PLATFORM_MAX


@dataclass(frozen=True)
class CancellationRecoveryEvent:
    """Delayed cancellation recovery event."""

    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    scheduled_at: str
    status: str
    yclients_client_id: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None
    sent_at: str | None = None
    skipped_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CancellationRecoveryEventsRepository:
    """Persist cancellation recovery events with record-level de-duplication."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_event(
        self,
        *,
        platform_user_id: str,
        yclients_record_id: str,
        scheduled_at: str,
        platform: str = PLATFORM_MAX,
        yclients_client_id: str | None = None,
        max_user_id: str | None = None,
        chat_id: str | None = None,
    ) -> CancellationRecoveryEvent | None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO cancellation_recovery_events (
                    platform, platform_user_id, yclients_record_id, yclients_client_id,
                    max_user_id, chat_id, scheduled_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (platform, platform_user_id, yclients_record_id, yclients_client_id, max_user_id, chat_id, scheduled_at),
            )
            connection.commit()
            return self.get_event(platform=platform, platform_user_id=platform_user_id, yclients_record_id=yclients_record_id)

    def find_due(self, now_iso: str, *, limit: int = 50) -> list[CancellationRecoveryEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE status = 'pending' AND scheduled_at <= ?
                ORDER BY scheduled_at ASC, id ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    def get_event(self, *, platform: str, platform_user_id: str, yclients_record_id: str) -> CancellationRecoveryEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE platform = ? AND platform_user_id = ? AND yclients_record_id = ?
                LIMIT 1
                """,
                (platform, platform_user_id, yclients_record_id),
            ).fetchone()
            return _row_to_event(row)

    def set_status(self, event_id: int, status: str, *, sent_at: str | None = None, skipped_reason: str | None = None) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE cancellation_recovery_events
                SET status = ?, sent_at = COALESCE(?, sent_at), skipped_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, sent_at, skipped_reason, event_id),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_event(row: sqlite3.Row | None) -> CancellationRecoveryEvent | None:
    if row is None:
        return None
    return CancellationRecoveryEvent(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        yclients_record_id=str(row["yclients_record_id"]),
        yclients_client_id=_optional(row, "yclients_client_id"),
        max_user_id=_optional(row, "max_user_id"),
        chat_id=_optional(row, "chat_id"),
        scheduled_at=str(row["scheduled_at"]),
        status=str(row["status"]),
        sent_at=_optional(row, "sent_at"),
        skipped_reason=_optional(row, "skipped_reason"),
        created_at=_optional(row, "created_at"),
        updated_at=_optional(row, "updated_at"),
    )


def _optional(row: Mapping[str, Any], key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    text = str(value).strip()
    return text or None
