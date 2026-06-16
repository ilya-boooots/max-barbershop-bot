"""SQLite repository for MAX repeat visit funnel events."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from max_barbershop_bot.repositories.users import PLATFORM_MAX


@dataclass(frozen=True)
class RepeatVisitEvent:
    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    yclients_client_id: str | None
    scheduled_at: str
    status: str
    sent_at: str | None = None
    skipped_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RepeatVisitEventsRepository:
    """Persist repeat visit events with YClients visit-level de-duplication."""

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
        status: str = "pending",
    ) -> RepeatVisitEvent | None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO repeat_visit_events (
                    platform, platform_user_id, yclients_record_id, yclients_client_id, scheduled_at, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (platform, platform_user_id, yclients_record_id, yclients_client_id, scheduled_at, status),
            )
            connection.commit()
            return self.get_event(platform=platform, platform_user_id=platform_user_id, yclients_record_id=yclients_record_id)

    def get_event(self, *, platform: str, platform_user_id: str, yclients_record_id: str) -> RepeatVisitEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM repeat_visit_events
                WHERE platform = ? AND platform_user_id = ? AND yclients_record_id = ?
                LIMIT 1
                """,
                (platform, platform_user_id, yclients_record_id),
            ).fetchone()
            return _row_to_event(row)

    def find_due(self, now_iso: str, *, limit: int = 100) -> list[RepeatVisitEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM repeat_visit_events
                WHERE status = 'pending' AND scheduled_at <= ?
                ORDER BY scheduled_at ASC, id ASC
                LIMIT ?
                """,
                (now_iso, max(1, int(limit))),
            ).fetchall()
            return [_row_to_event(row) for row in rows if row is not None]

    def set_status(self, event_id: int, status: str, *, sent_at: str | None = None, skipped_reason: str | None = None) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE repeat_visit_events
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


def _row_to_event(row: sqlite3.Row | None) -> RepeatVisitEvent | None:
    if row is None:
        return None
    return RepeatVisitEvent(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        yclients_record_id=str(row["yclients_record_id"]),
        yclients_client_id=_optional(row, "yclients_client_id"),
        scheduled_at=str(row["scheduled_at"]),
        status=str(row["status"]),
        sent_at=_optional(row, "sent_at"),
        skipped_reason=_optional(row, "skipped_reason"),
        created_at=_optional(row, "created_at"),
        updated_at=_optional(row, "updated_at"),
    )


def _optional(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    text = str(value).strip() if value is not None else ""
    return text or None
