"""Birthday funnel event storage for MAX duplicate prevention."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

PLATFORM_MAX = "max"
BIRTHDAY_NOTIFICATION_TYPE = "birthday"


@dataclass(frozen=True)
class BirthdayFunnelEvent:
    """Persisted yearly birthday notification event."""

    id: int
    platform: str
    platform_user_id: str
    birth_year: int
    notification_type: str
    status: str
    sent_at: str | None
    created_at: str | None


class BirthdayFunnelEventsRepository:
    """Small MAX equivalent of Telegram birthday_funnel_events."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def find_by_user_year(
        self,
        platform_user_id: str,
        birth_year: int,
        *,
        platform: str = PLATFORM_MAX,
        notification_type: str = BIRTHDAY_NOTIFICATION_TYPE,
    ) -> BirthdayFunnelEvent | None:
        """Return an existing yearly event for duplicate prevention."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM birthday_funnel_events
                WHERE platform = ? AND platform_user_id = ? AND birth_year = ? AND notification_type = ?
                LIMIT 1
                """,
                (platform, str(platform_user_id), int(birth_year), notification_type),
            ).fetchone()
        return _row_to_event(row)

    def create_pending(
        self,
        platform_user_id: str,
        birth_year: int,
        *,
        platform: str = PLATFORM_MAX,
        notification_type: str = BIRTHDAY_NOTIFICATION_TYPE,
    ) -> BirthdayFunnelEvent | None:
        """Reserve a yearly birthday event; return existing row on duplicate."""

        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO birthday_funnel_events (
                    platform, platform_user_id, birth_year, notification_type, status
                ) VALUES (?, ?, ?, ?, 'pending')
                """,
                (platform, str(platform_user_id), int(birth_year), notification_type),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT * FROM birthday_funnel_events
                WHERE platform = ? AND platform_user_id = ? AND birth_year = ? AND notification_type = ?
                LIMIT 1
                """,
                (platform, str(platform_user_id), int(birth_year), notification_type),
            ).fetchone()
        return _row_to_event(row)

    def mark_status(self, event_id: int, status: str, *, sent: bool = False) -> BirthdayFunnelEvent | None:
        """Update event status and set sent_at when delivery succeeds."""

        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE birthday_funnel_events
                SET status = ?, sent_at = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE sent_at END
                WHERE id = ?
                """,
                (str(status), 1 if sent else 0, int(event_id)),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM birthday_funnel_events WHERE id = ?", (int(event_id),)).fetchone()
        return _row_to_event(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_event(row: sqlite3.Row | None) -> BirthdayFunnelEvent | None:
    if row is None:
        return None
    return BirthdayFunnelEvent(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        birth_year=int(row["birth_year"]),
        notification_type=str(row["notification_type"]),
        status=str(row["status"]),
        sent_at=str(row["sent_at"]) if row["sent_at"] is not None else None,
        created_at=str(row["created_at"]) if row["created_at"] is not None else None,
    )
