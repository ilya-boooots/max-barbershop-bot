"""Birthday funnel event storage for MAX duplicate prevention and attribution."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

PLATFORM_MAX = "max"
BIRTHDAY_NOTIFICATION_TYPE = "birthday"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    yclients_client_id: str | None = None
    client_tg_id: str | None = None
    birth_date: str | None = None
    birthday_year: int | None = None
    scheduled_send_at_utc: str | None = None
    sent_at_utc: str | None = None
    clicked_at_utc: str | None = None
    yclients_booking_id: str | None = None
    branch_timezone: str | None = None
    source: str | None = None
    is_test: bool = False
    error_summary: str | None = None
    updated_at_utc: str | None = None


class BirthdayFunnelEventsRepository:
    """MAX equivalent of Telegram birthday_funnel_events."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def find_by_client_year(self, client_tg_id: str, birthday_year: int, *, is_test: bool = False) -> BirthdayFunnelEvent | None:
        """Return an existing yearly event for duplicate prevention by Telegram-equivalent key."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM birthday_funnel_events
                WHERE client_tg_id = ? AND birthday_year = ? AND is_test = ?
                ORDER BY id DESC LIMIT 1
                """,
                (str(client_tg_id), int(birthday_year), 1 if is_test else 0),
            ).fetchone()
        return _row_to_event(row)

    def find_by_user_year(
        self,
        platform_user_id: str,
        birth_year: int,
        *,
        platform: str = PLATFORM_MAX,
        notification_type: str = BIRTHDAY_NOTIFICATION_TYPE,
        is_test: bool = False,
    ) -> BirthdayFunnelEvent | None:
        """Return an existing yearly event for duplicate prevention."""

        found = self.find_by_client_year(platform_user_id, birth_year, is_test=is_test)
        if found is not None:
            return found
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM birthday_funnel_events
                WHERE platform = ? AND platform_user_id = ? AND birth_year = ? AND notification_type = ?
                  AND COALESCE(is_test, 0) = ?
                ORDER BY id DESC LIMIT 1
                """,
                (platform, str(platform_user_id), int(birth_year), notification_type, 1 if is_test else 0),
            ).fetchone()
        return _row_to_event(row)

    def create_event(self, **kwargs: object) -> BirthdayFunnelEvent | None:
        """Create a Telegram-equivalent birthday funnel event."""

        now = _now_iso()
        client_tg_id = str(kwargs.get("client_tg_id") or kwargs.get("platform_user_id") or "")
        birthday_year = int(kwargs.get("birthday_year") or kwargs.get("birth_year") or datetime.now(UTC).year)
        is_test = bool(kwargs.get("is_test"))
        notification_type = str(kwargs.get("notification_type") or BIRTHDAY_NOTIFICATION_TYPE)
        storage_notification_type = f"{notification_type}:test" if is_test else notification_type
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO birthday_funnel_events (
                    platform, platform_user_id, birth_year, notification_type,
                    yclients_client_id, client_tg_id, birth_date, birthday_year,
                    scheduled_send_at_utc, sent_at_utc, clicked_at_utc, yclients_booking_id,
                    status, branch_timezone, source, is_test, error_summary,
                    created_at, updated_at_utc, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(kwargs.get("platform") or PLATFORM_MAX),
                    str(kwargs.get("platform_user_id") or client_tg_id),
                    birthday_year,
                    storage_notification_type,
                    _optional(kwargs.get("yclients_client_id")),
                    client_tg_id,
                    _optional(kwargs.get("birth_date")),
                    birthday_year,
                    _optional(kwargs.get("scheduled_send_at_utc")),
                    _optional(kwargs.get("sent_at_utc")),
                    _optional(kwargs.get("clicked_at_utc")),
                    _optional(kwargs.get("yclients_booking_id")),
                    str(kwargs.get("status") or "pending"),
                    _optional(kwargs.get("branch_timezone")),
                    str(kwargs.get("source") or "local_db"),
                    1 if is_test else 0,
                    _optional(kwargs.get("error_summary")),
                    now,
                    now,
                    _optional(kwargs.get("sent_at_utc")),
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM birthday_funnel_events WHERE id = last_insert_rowid()").fetchone()
        return _row_to_event(row)

    def create_pending(
        self,
        platform_user_id: str,
        birth_year: int,
        *,
        platform: str = PLATFORM_MAX,
        notification_type: str = BIRTHDAY_NOTIFICATION_TYPE,
        **kwargs: object,
    ) -> BirthdayFunnelEvent | None:
        """Reserve a yearly birthday event; return existing row on duplicate."""

        existing = self.find_by_user_year(platform_user_id, birth_year, platform=platform, notification_type=notification_type, is_test=bool(kwargs.get("is_test")))
        if existing is not None:
            return existing
        return self.create_event(platform=platform, platform_user_id=platform_user_id, client_tg_id=platform_user_id, birthday_year=birth_year, birth_year=birth_year, notification_type=notification_type, status="pending", **kwargs)

    def get_event(self, event_id: int) -> BirthdayFunnelEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM birthday_funnel_events WHERE id = ?", (int(event_id),)).fetchone()
        return _row_to_event(row)

    def mark_status(self, event_id: int, status: str, *, clicked: bool = False, sent: bool = False, booking_id: str | None = None, error_summary: str | None = None) -> BirthdayFunnelEvent | None:
        """Update event status and Telegram-equivalent timestamps."""

        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE birthday_funnel_events
                SET status = ?,
                    clicked_at_utc = CASE WHEN ? = 1 THEN ? ELSE clicked_at_utc END,
                    sent_at_utc = CASE WHEN ? = 1 THEN ? ELSE sent_at_utc END,
                    sent_at = CASE WHEN ? = 1 THEN ? ELSE sent_at END,
                    yclients_booking_id = COALESCE(?, yclients_booking_id),
                    error_summary = COALESCE(?, error_summary),
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (str(status), 1 if clicked else 0, now, 1 if sent else 0, now, 1 if sent else 0, now, booking_id, error_summary, now, int(event_id)),
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


def _optional(value: object) -> str | None:
    return None if value is None or str(value) == "" else str(value)


def _row_to_event(row: sqlite3.Row | None) -> BirthdayFunnelEvent | None:
    if row is None:
        return None
    keys = set(row.keys())
    birthday_year = row["birthday_year"] if "birthday_year" in keys and row["birthday_year"] is not None else row["birth_year"]
    return BirthdayFunnelEvent(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        birth_year=int(row["birth_year"]),
        notification_type=str(row["notification_type"]),
        status=str(row["status"]),
        sent_at=str(row["sent_at"]) if row["sent_at"] is not None else None,
        created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        yclients_client_id=_get(row, keys, "yclients_client_id"),
        client_tg_id=_get(row, keys, "client_tg_id"),
        birth_date=_get(row, keys, "birth_date"),
        birthday_year=int(birthday_year) if birthday_year is not None else None,
        scheduled_send_at_utc=_get(row, keys, "scheduled_send_at_utc"),
        sent_at_utc=_get(row, keys, "sent_at_utc"),
        clicked_at_utc=_get(row, keys, "clicked_at_utc"),
        yclients_booking_id=_get(row, keys, "yclients_booking_id"),
        branch_timezone=_get(row, keys, "branch_timezone"),
        source=_get(row, keys, "source"),
        is_test=bool(row["is_test"]) if "is_test" in keys and row["is_test"] is not None else False,
        error_summary=_get(row, keys, "error_summary"),
        updated_at_utc=_get(row, keys, "updated_at_utc"),
    )


def _get(row: sqlite3.Row, keys: set[str], key: str) -> str | None:
    return str(row[key]) if key in keys and row[key] is not None else None
