"""SQLite repository for MAX lost/inactive client funnel events."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from max_barbershop_bot.repositories.users import PLATFORM_MAX

VALID_STATUSES = {
    "candidate",
    "pending",
    "sent",
    "clicked_booking",
    "skipped_has_future_booking",
    "skipped",
    "failed",
    "blocked",
}


@dataclass(frozen=True)
class LostClientEvent:
    id: int
    platform: str
    platform_user_id: str | None
    yclients_client_id: str | None
    client_tg_id: str | None
    threshold_days: int
    segment_key: str | None
    last_visit_datetime_utc: str | None
    last_visit_id: str | None
    has_future_booking: bool
    scheduled_send_at_utc: str | None
    sent_at_utc: str | None
    clicked_at_utc: str | None
    status: str
    source: str | None
    is_test: bool
    error_summary: str | None
    created_at_utc: str | None
    updated_at_utc: str | None
    max_user_id: str | None = None
    chat_id: str | None = None


class LostClientEventsRepository:
    """Persist Telegram-equivalent lost client funnel events for MAX."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_event(
        self,
        *,
        yclients_client_id: str | None = None,
        client_tg_id: int | str | None = None,
        platform_user_id: int | str | None = None,
        threshold_days: int = 0,
        segment_key: str | None = None,
        last_visit_datetime_utc: str | None = None,
        last_visit_id: str | None = None,
        has_future_booking: bool = False,
        scheduled_send_at_utc: str | None = None,
        sent_at_utc: str | None = None,
        clicked_at_utc: str | None = None,
        status: str = "candidate",
        source: str | None = "yclients",
        is_test: bool = False,
        error_summary: str | None = None,
        max_user_id: str | None = None,
        chat_id: str | None = None,
        platform: str = PLATFORM_MAX,
    ) -> LostClientEvent:
        now = _now()
        platform_user_id_text = _clean(platform_user_id) or _clean(client_tg_id)
        client_tg_id_text = _clean(client_tg_id) or platform_user_id_text
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO lost_client_events (
                    platform, platform_user_id, yclients_client_id, client_tg_id, threshold_days, segment_key,
                    last_visit_datetime_utc, last_visit_id, has_future_booking, scheduled_send_at_utc,
                    sent_at_utc, clicked_at_utc, status, source, is_test, error_summary,
                    created_at_utc, updated_at_utc, max_user_id, chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    platform_user_id_text,
                    _clean(yclients_client_id),
                    client_tg_id_text,
                    int(threshold_days or 0),
                    _clean(segment_key),
                    _clean(last_visit_datetime_utc),
                    _clean(last_visit_id),
                    1 if has_future_booking else 0,
                    _clean(scheduled_send_at_utc),
                    _clean(sent_at_utc),
                    _clean(clicked_at_utc),
                    _normalize_status(status),
                    _clean(source) or "yclients",
                    1 if is_test else 0,
                    _clean(error_summary),
                    now,
                    now,
                    _clean(max_user_id),
                    _clean(chat_id),
                ),
            )
            connection.commit()
            event = self.get_event(int(cursor.lastrowid))
            if event is None:  # pragma: no cover
                raise RuntimeError("lost_client_event_create_failed")
            return event

    def mark_status(self, event_id: int, status: str, *, error_summary: str | None = None, clicked: bool = False, sent: bool = False) -> LostClientEvent | None:
        now = _now()
        normalized = _normalize_status(status)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE lost_client_events
                SET status = ?,
                    error_summary = ?,
                    clicked_at_utc = CASE WHEN ? = 1 THEN ? ELSE clicked_at_utc END,
                    sent_at_utc = CASE WHEN ? = 1 THEN ? ELSE sent_at_utc END,
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (_normalize_status(normalized), _clean(error_summary), 1 if clicked else 0, now, 1 if sent else 0, now, now, int(event_id)),
            )
            connection.commit()
        return self.get_event(event_id)

    def has_recent_sent(self, client_tg_id: int | str, threshold_days: int, cooldown_days: int) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(cooldown_days)))).isoformat()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id FROM lost_client_events
                WHERE client_tg_id = ? AND threshold_days = ? AND sent_at_utc IS NOT NULL
                  AND sent_at_utc >= ? AND COALESCE(is_test, 0) = 0
                ORDER BY id DESC LIMIT 1
                """,
                (_clean(client_tg_id), int(threshold_days), cutoff),
            ).fetchone()
        return row is not None

    def get_recent_stats(self, days: int = 7) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(1) AS cnt FROM lost_client_events WHERE sent_at_utc IS NOT NULL AND sent_at_utc >= ?",
                (cutoff,),
            ).fetchone()
        return int(row["cnt"] if row else 0)

    def get_event(self, event_id: int) -> LostClientEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM lost_client_events WHERE id = ?", (int(event_id),)).fetchone()
        return _row_to_event(row)

    def find_latest_by_tg_threshold(self, client_tg_id: int | str, threshold_days: int) -> LostClientEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM lost_client_events
                WHERE client_tg_id = ? AND threshold_days = ?
                ORDER BY id DESC LIMIT 1
                """,
                (_clean(client_tg_id), int(threshold_days)),
            ).fetchone()
        return _row_to_event(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_event(row: sqlite3.Row | None) -> LostClientEvent | None:
    if row is None:
        return None
    return LostClientEvent(
        id=int(row["id"]),
        platform=str(row["platform"] or PLATFORM_MAX),
        platform_user_id=row["platform_user_id"],
        yclients_client_id=row["yclients_client_id"],
        client_tg_id=row["client_tg_id"],
        threshold_days=int(row["threshold_days"] or 0),
        segment_key=row["segment_key"],
        last_visit_datetime_utc=row["last_visit_datetime_utc"],
        last_visit_id=row["last_visit_id"],
        has_future_booking=bool(row["has_future_booking"]),
        scheduled_send_at_utc=row["scheduled_send_at_utc"],
        sent_at_utc=row["sent_at_utc"],
        clicked_at_utc=row["clicked_at_utc"],
        status=str(row["status"] or "candidate"),
        source=row["source"],
        is_test=bool(row["is_test"]),
        error_summary=row["error_summary"],
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
        max_user_id=row["max_user_id"],
        chat_id=row["chat_id"],
    )


def _normalize_status(status: str) -> str:
    clean = str(status or "candidate").strip()
    if clean == "clicked_rebook":
        clean = "clicked_booking"
    return clean if clean in VALID_STATUSES else "failed"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None
