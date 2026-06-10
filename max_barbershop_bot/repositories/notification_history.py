"""SQLite repository for MAX notification history diagnostics."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationHistoryRecord:
    """Safe business notification history row for diagnostics screens."""

    id: int
    platform: str
    platform_user_id: str
    max_user_id: str | None
    chat_id: str | None
    yclients_record_id: str
    yclients_client_id: str | None
    notification_type: str
    scheduled_for: str | None
    sent_at: str | None
    status: str
    delivery_status_code: int | None
    delivery_error_code: str | None
    delivery_error_message: str | None
    message_id: str | None
    attempts: int
    is_blocked: bool
    is_stopped: bool
    metadata_json: str | None
    created_at: str | None
    updated_at: str | None


class NotificationHistoryRepository:
    """Read-only notification history repository for admin diagnostics."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def list_recent(self, limit: int = 20) -> list[NotificationHistoryRecord]:
        """Return recent notification history rows."""

        return self._fetch_many(
            """
            SELECT * FROM notification_history
            ORDER BY COALESCE(sent_at, updated_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (_safe_limit(limit),),
        )

    def get_by_id(self, history_id: int) -> NotificationHistoryRecord | None:
        """Return one notification history row by id."""

        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM notification_history WHERE id = ? LIMIT 1", (history_id,)).fetchone()
        return _row_to_record(row)

    def list_by_platform_user_id(self, platform_user_id: str, limit: int = 20) -> list[NotificationHistoryRecord]:
        """Return recent rows for one MAX platform user id."""

        return self._fetch_many(
            """
            SELECT * FROM notification_history
            WHERE platform_user_id = ?
            ORDER BY COALESCE(sent_at, updated_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (str(platform_user_id), _safe_limit(limit)),
        )

    def list_by_yclients_record_id(self, yclients_record_id: str) -> list[NotificationHistoryRecord]:
        """Return rows connected with one YClients record id."""

        return self._fetch_many(
            """
            SELECT * FROM notification_history
            WHERE yclients_record_id = ?
            ORDER BY COALESCE(sent_at, updated_at, created_at) DESC, id DESC
            """,
            (str(yclients_record_id),),
        )

    def list_by_status(self, status: str, limit: int = 20) -> list[NotificationHistoryRecord]:
        """Return recent rows with an exact delivery status."""

        return self._fetch_many(
            """
            SELECT * FROM notification_history
            WHERE status = ?
            ORDER BY COALESCE(sent_at, updated_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (str(status), _safe_limit(limit)),
        )

    def list_recent_failed(self, limit: int = 20) -> list[NotificationHistoryRecord]:
        """Return recent rows that need delivery diagnostics."""

        return self._fetch_many(
            """
            SELECT * FROM notification_history
            WHERE status IN ('failed', 'blocked', 'stopped')
               OR is_blocked = 1
               OR is_stopped = 1
               OR delivery_error_code IS NOT NULL
               OR delivery_error_message IS NOT NULL
            ORDER BY COALESCE(sent_at, updated_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (_safe_limit(limit),),
        )

    def count_by_status(self) -> dict[str, int]:
        """Return notification history row counts grouped by status."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM notification_history
                GROUP BY status
                """
            ).fetchall()
        return {str(row["status"]): int(row["count"] or 0) for row in rows}

    def _fetch_many(self, sql: str, params: tuple[object, ...]) -> list[NotificationHistoryRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows if row is not None]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def _safe_limit(limit: int) -> int:
    return max(1, min(int(limit), 50))


def _row_to_record(row: sqlite3.Row | None) -> NotificationHistoryRecord | None:
    if row is None:
        return None
    return NotificationHistoryRecord(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        max_user_id=_optional_text(row, "max_user_id"),
        chat_id=_optional_text(row, "chat_id"),
        yclients_record_id=str(row["yclients_record_id"]),
        yclients_client_id=_optional_text(row, "yclients_client_id"),
        notification_type=str(row["notification_type"]),
        scheduled_for=_optional_text(row, "scheduled_for"),
        sent_at=_optional_text(row, "sent_at"),
        status=str(row["status"]),
        delivery_status_code=_optional_int(row, "delivery_status_code"),
        delivery_error_code=_optional_text(row, "delivery_error_code"),
        delivery_error_message=_optional_text(row, "delivery_error_message"),
        message_id=_optional_text(row, "message_id"),
        attempts=int(row["attempts"] or 0),
        is_blocked=bool(row["is_blocked"]),
        is_stopped=bool(row["is_stopped"]),
        metadata_json=_optional_text(row, "metadata_json"),
        created_at=_optional_text(row, "created_at"),
        updated_at=_optional_text(row, "updated_at"),
    )


def _optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = row[column]
    return str(value) if value is not None else None


def _optional_int(row: sqlite3.Row, column: str) -> int | None:
    value = row[column]
    return int(value) if value is not None else None
