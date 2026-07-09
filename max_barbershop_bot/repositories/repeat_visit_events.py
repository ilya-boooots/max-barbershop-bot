"""SQLite repository for MAX repeat visit funnel events."""
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
    "skipped_no_telegram",
    "skipped_unsubscribed",
    "skipped_antispam",
    "skipped_outside_working_hours",
    "skipped_duplicate",
    "failed",
    "blocked",
}
_ACTIVE_DEDUP_STATUSES = ("pending", "sent", "clicked_booking", "blocked", "failed")


@dataclass(frozen=True)
class RepeatVisitEvent:
    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str | None
    yclients_client_id: str | None
    scheduled_at: str | None
    status: str
    sent_at: str | None = None
    skipped_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    yclients_visit_id: str | None = None
    yclients_service_id: str | None = None
    service_name: str | None = None
    last_visit_datetime_utc: str | None = None
    delay_days: int | None = None
    scheduled_send_at_utc: str | None = None
    selected_template_index: int | None = None
    selected_template_text: str | None = None
    sent_at_utc: str | None = None
    clicked_at_utc: str | None = None
    branch_timezone: str | None = None
    source: str | None = None
    is_test: bool = False
    error_summary: str | None = None
    created_at_utc: str | None = None
    updated_at_utc: str | None = None


class RepeatVisitEventsRepository:
    """Persist repeat visit events with Telegram-equivalent visit/service de-duplication."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_event(
        self,
        *,
        platform_user_id: str,
        yclients_record_id: str | None = None,
        scheduled_at: str | None = None,
        platform: str = PLATFORM_MAX,
        yclients_client_id: str | None = None,
        status: str = "candidate",
        yclients_visit_id: str | None = None,
        yclients_service_id: str | None = None,
        service_name: str | None = None,
        last_visit_datetime_utc: str | None = None,
        delay_days: int | None = None,
        scheduled_send_at_utc: str | None = None,
        selected_template_index: int | None = None,
        selected_template_text: str | None = None,
        sent_at_utc: str | None = None,
        clicked_at_utc: str | None = None,
        branch_timezone: str | None = None,
        source: str | None = "yclients",
        is_test: bool = False,
        error_summary: str | None = None,
    ) -> RepeatVisitEvent | None:
        now = _now()
        visit_id = _clean(yclients_visit_id) or _clean(yclients_record_id)
        record_id = _clean(yclients_record_id) or visit_id
        send_at = _clean(scheduled_send_at_utc) or _clean(scheduled_at)
        legacy_scheduled = _clean(scheduled_at) or send_at or now
        with closing(self._connect()) as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO repeat_visit_events (
                        platform, platform_user_id, yclients_record_id, yclients_client_id, scheduled_at,
                        status, sent_at, skipped_reason, yclients_visit_id, yclients_service_id, service_name,
                        last_visit_datetime_utc, delay_days, scheduled_send_at_utc, selected_template_index,
                        selected_template_text, sent_at_utc, clicked_at_utc, branch_timezone, source, is_test,
                        error_summary, created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        platform,
                        platform_user_id,
                        record_id,
                        yclients_client_id,
                        legacy_scheduled,
                        _normalize_status(status),
                        sent_at_utc,
                        _status_to_skipped_reason(status, error_summary),
                        visit_id,
                        _clean(yclients_service_id),
                        _clean(service_name),
                        _clean(last_visit_datetime_utc),
                        delay_days,
                        send_at,
                        selected_template_index,
                        selected_template_text,
                        sent_at_utc,
                        clicked_at_utc,
                        _clean(branch_timezone),
                        _clean(source) or "yclients",
                        1 if is_test else 0,
                        _clean(error_summary),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                return self.get_event(platform=platform, platform_user_id=platform_user_id, yclients_record_id=record_id or "")
            connection.commit()
            return self.get_event_by_id(int(cursor.lastrowid))

    def get_event(self, *args, **kwargs) -> RepeatVisitEvent | None:
        """Get by event id or by legacy MAX tuple for backward compatibility."""
        if args and isinstance(args[0], int):
            return self.get_event_by_id(args[0])
        event_id = kwargs.get("event_id") or kwargs.get("id")
        if event_id is not None:
            return self.get_event_by_id(int(event_id))
        platform = kwargs["platform"]
        platform_user_id = kwargs["platform_user_id"]
        yclients_record_id = kwargs["yclients_record_id"]
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM repeat_visit_events
                WHERE platform = ? AND platform_user_id = ?
                  AND (yclients_record_id = ? OR yclients_visit_id = ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (platform, platform_user_id, yclients_record_id, yclients_record_id),
            ).fetchone()
            return _row_to_event(row)

    def get_event_by_id(self, event_id: int) -> RepeatVisitEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM repeat_visit_events WHERE id = ?", (event_id,)).fetchone()
            return _row_to_event(row)

    def find_due(self, now_iso: str, *, limit: int = 100) -> list[RepeatVisitEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM repeat_visit_events
                WHERE status = 'pending' AND COALESCE(scheduled_send_at_utc, scheduled_at) <= ?
                ORDER BY COALESCE(scheduled_send_at_utc, scheduled_at) ASC, id ASC
                LIMIT ?
                """,
                (now_iso, max(1, int(limit))),
            ).fetchall()
            return [event for row in rows if (event := _row_to_event(row)) is not None]

    def mark_status(self, event_id: int, status: str, *, clicked: bool = False, sent: bool = False, error_summary: str | None = None) -> None:
        now = _now()
        normalized = _normalize_status(status)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE repeat_visit_events
                SET status = ?,
                    skipped_reason = ?,
                    error_summary = ?,
                    clicked_at_utc = CASE WHEN ? = 1 THEN ? ELSE clicked_at_utc END,
                    sent_at_utc = CASE WHEN ? = 1 THEN ? ELSE sent_at_utc END,
                    sent_at = CASE WHEN ? = 1 THEN ? ELSE sent_at END,
                    updated_at_utc = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    normalized,
                    _status_to_skipped_reason(normalized, error_summary),
                    _clean(error_summary),
                    1 if clicked else 0,
                    now,
                    1 if sent else 0,
                    now,
                    1 if sent else 0,
                    now,
                    now,
                    event_id,
                ),
            )
            connection.commit()

    def set_status(self, event_id: int, status: str, *, sent_at: str | None = None, skipped_reason: str | None = None) -> None:
        self.mark_status(event_id, status, sent=status == "sent" or bool(sent_at), error_summary=skipped_reason)

    def has_event_for_visit(self, platform_user_id: str, visit_id: str | None, service_id: str | None, *, platform: str = PLATFORM_MAX) -> bool:
        visit = _clean(visit_id)
        service = _clean(service_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT id FROM repeat_visit_events
                WHERE platform = ? AND platform_user_id = ? AND is_test = 0
                  AND (yclients_visit_id IS ? OR yclients_record_id IS ?)
                  AND yclients_service_id IS ?
                  AND status IN ({','.join('?' for _ in _ACTIVE_DEDUP_STATUSES)})
                LIMIT 1
                """,
                (platform, platform_user_id, visit, visit, service, *_ACTIVE_DEDUP_STATUSES),
            ).fetchone()
            return row is not None

    def has_recent_sent(self, platform_user_id: str, cooldown_hours: int, *, platform: str = PLATFORM_MAX) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(hours=cooldown_hours)).isoformat()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id FROM repeat_visit_events
                WHERE platform = ? AND platform_user_id = ? AND is_test = 0
                  AND COALESCE(sent_at_utc, sent_at) IS NOT NULL
                  AND COALESCE(sent_at_utc, sent_at) >= ?
                LIMIT 1
                """,
                (platform, platform_user_id, cutoff),
            ).fetchone()
            return row is not None

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
        yclients_record_id=_optional(row, "yclients_record_id"),
        yclients_client_id=_optional(row, "yclients_client_id"),
        scheduled_at=_optional(row, "scheduled_at"),
        status=str(row["status"]),
        sent_at=_optional(row, "sent_at"),
        skipped_reason=_optional(row, "skipped_reason"),
        created_at=_optional(row, "created_at"),
        updated_at=_optional(row, "updated_at"),
        yclients_visit_id=_optional(row, "yclients_visit_id") or _optional(row, "yclients_record_id"),
        yclients_service_id=_optional(row, "yclients_service_id"),
        service_name=_optional(row, "service_name"),
        last_visit_datetime_utc=_optional(row, "last_visit_datetime_utc"),
        delay_days=_optional_int(row, "delay_days"),
        scheduled_send_at_utc=_optional(row, "scheduled_send_at_utc") or _optional(row, "scheduled_at"),
        selected_template_index=_optional_int(row, "selected_template_index"),
        selected_template_text=_optional(row, "selected_template_text"),
        sent_at_utc=_optional(row, "sent_at_utc") or _optional(row, "sent_at"),
        clicked_at_utc=_optional(row, "clicked_at_utc"),
        branch_timezone=_optional(row, "branch_timezone"),
        source=_optional(row, "source"),
        is_test=bool(_optional_int(row, "is_test") or 0),
        error_summary=_optional(row, "error_summary"),
        created_at_utc=_optional(row, "created_at_utc") or _optional(row, "created_at"),
        updated_at_utc=_optional(row, "updated_at_utc") or _optional(row, "updated_at"),
    )


def _normalize_status(status: str) -> str:
    text = str(status or "").strip() or "candidate"
    return text if text in VALID_STATUSES else "failed"


def _status_to_skipped_reason(status: str, error_summary: str | None) -> str | None:
    if status.startswith("skipped_"):
        return status
    if status in {"failed", "blocked"}:
        return _clean(error_summary) or status
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional(row: sqlite3.Row, key: str) -> str | None:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return None
    return _clean(value)


def _optional_int(row: sqlite3.Row, key: str) -> int | None:
    value = _optional(row, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
