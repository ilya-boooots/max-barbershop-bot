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
    """Delayed cancellation recovery event with Telegram-equivalent fields."""

    id: int
    platform: str
    platform_user_id: str
    yclients_record_id: str
    scheduled_at: str | None
    status: str
    yclients_client_id: str | None = None
    client_tg_id: str | None = None
    staff_id: str | None = None
    staff_name: str | None = None
    service_id: str | None = None
    service_name: str | None = None
    cancelled_booking_datetime_utc: str | None = None
    cancellation_detected_at_utc: str | None = None
    scheduled_send_at_utc: str | None = None
    branch_timezone: str | None = None
    is_test: bool = False
    source: str | None = None
    clicked_at_utc: str | None = None
    error_summary: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None
    sent_at: str | None = None
    skipped_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def effective_scheduled_send_at_utc(self) -> str | None:
        """Return Telegram scheduled field with old MAX fallback."""

        return self.scheduled_send_at_utc or self.scheduled_at


class CancellationRecoveryEventsRepository:
    """Persist cancellation recovery events with Telegram-equivalent de-duplication."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_event(
        self,
        *,
        yclients_record_id: str | None,
        scheduled_send_at_utc: str | None = None,
        platform_user_id: str | None = None,
        platform: str = PLATFORM_MAX,
        yclients_client_id: str | None = None,
        client_tg_id: str | int | None = None,
        staff_id: str | None = None,
        staff_name: str | None = None,
        service_id: str | None = None,
        service_name: str | None = None,
        cancelled_booking_datetime_utc: str | None = None,
        cancellation_detected_at_utc: str | None = None,
        branch_timezone: str | None = None,
        is_test: bool = False,
        source: str | None = None,
        max_user_id: str | None = None,
        chat_id: str | None = None,
        scheduled_at: str | None = None,
        status: str = "pending",
    ) -> CancellationRecoveryEvent | None:
        now = now_utc_iso()
        clean_record_id = _optional_text(yclients_record_id)
        clean_platform_user_id = _optional_text(platform_user_id) or _optional_text(max_user_id) or _optional_text(client_tg_id)
        clean_source = _optional_text(source) or "yclients"
        is_test_int = 1 if is_test else 0
        scheduled = _optional_text(scheduled_send_at_utc) or _optional_text(scheduled_at)
        if not clean_record_id and not clean_platform_user_id:
            return None

        with closing(self._connect()) as connection:
            existing = self._find_existing_for_create(
                connection,
                yclients_record_id=clean_record_id,
                source=clean_source,
                is_test=is_test_int,
                platform=platform,
                platform_user_id=clean_platform_user_id,
            )
            if existing is not None:
                return _row_to_event(existing)

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO cancellation_recovery_events (
                    platform, platform_user_id, yclients_record_id, yclients_client_id,
                    client_tg_id, staff_id, staff_name, service_id, service_name,
                    cancelled_booking_datetime_utc, cancellation_detected_at_utc,
                    scheduled_send_at_utc, branch_timezone, is_test, source,
                    max_user_id, chat_id, scheduled_at, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    clean_platform_user_id or "",
                    clean_record_id or "",
                    _optional_text(yclients_client_id),
                    _optional_text(client_tg_id),
                    _optional_text(staff_id),
                    _optional_text(staff_name),
                    _optional_text(service_id),
                    _optional_text(service_name),
                    _optional_text(cancelled_booking_datetime_utc),
                    _optional_text(cancellation_detected_at_utc) or now,
                    scheduled,
                    _optional_text(branch_timezone) or "UTC",
                    is_test_int,
                    clean_source,
                    _optional_text(max_user_id),
                    _optional_text(chat_id),
                    scheduled,
                    status,
                    now,
                    now,
                ),
            )
            connection.commit()
            if cursor.rowcount:
                row = connection.execute("SELECT * FROM cancellation_recovery_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return _row_to_event(row)
            existing = self._find_existing_for_create(
                connection,
                yclients_record_id=clean_record_id,
                source=clean_source,
                is_test=is_test_int,
                platform=platform,
                platform_user_id=clean_platform_user_id,
            )
            return _row_to_event(existing)

    def find_pending_to_send(self, now_utc: str, *, limit: int = 50) -> list[CancellationRecoveryEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE status = 'pending'
                  AND (
                    (scheduled_send_at_utc IS NOT NULL AND scheduled_send_at_utc <= ?)
                    OR ((scheduled_send_at_utc IS NULL OR TRIM(scheduled_send_at_utc) = '') AND scheduled_at IS NOT NULL AND scheduled_at <= ?)
                  )
                ORDER BY COALESCE(NULLIF(scheduled_send_at_utc, ''), scheduled_at) ASC, id ASC
                LIMIT ?
                """,
                (now_utc, now_utc, limit),
            ).fetchall()
            events = [_row_to_event(row) for row in rows]
            return [event for event in events if event is not None]

    def find_due(self, now_iso: str, *, limit: int = 50) -> list[CancellationRecoveryEvent]:
        """Backward-compatible alias for older MAX call sites."""

        return self.find_pending_to_send(now_iso, limit=limit)

    def get_event(self, event_id: int | None = None, *, platform: str | None = None, platform_user_id: str | None = None, yclients_record_id: str | None = None) -> CancellationRecoveryEvent | None:
        with closing(self._connect()) as connection:
            if event_id is not None:
                row = connection.execute("SELECT * FROM cancellation_recovery_events WHERE id = ? LIMIT 1", (event_id,)).fetchone()
                return _row_to_event(row)
            row = connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE platform = ? AND platform_user_id = ? AND yclients_record_id = ?
                LIMIT 1
                """,
                (platform or PLATFORM_MAX, _optional_text(platform_user_id) or "", _optional_text(yclients_record_id) or ""),
            ).fetchone()
            return _row_to_event(row)

    def set_status(self, event_id: int, status: str, **fields: Any) -> None:
        allowed = {
            "sent_at_utc": "sent_at",
            "sent_at": "sent_at",
            "clicked_at_utc": "clicked_at_utc",
            "error_summary": "error_summary",
            "skipped_reason": "skipped_reason",
            "scheduled_send_at_utc": "scheduled_send_at_utc",
            "scheduled_at": "scheduled_at",
            "source": "source",
            "is_test": "is_test",
        }
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now_utc_iso()]
        for key, value in fields.items():
            column = allowed.get(key)
            if column is None:
                continue
            sets.append(f"{column} = ?")
            if column == "is_test":
                params.append(1 if value else 0)
            else:
                params.append(_safe_error_text(value) if column == "error_summary" else value)
        if "error_summary" in fields and "skipped_reason" not in fields and status.startswith("skipped"):
            sets.append("skipped_reason = ?")
            params.append(_safe_error_text(fields.get("error_summary")))
        params.append(event_id)
        with closing(self._connect()) as connection:
            connection.execute(f"UPDATE cancellation_recovery_events SET {', '.join(sets)} WHERE id = ?", tuple(params))
            connection.commit()

    def _find_existing_for_create(
        self,
        connection: sqlite3.Connection,
        *,
        yclients_record_id: str | None,
        source: str,
        is_test: int,
        platform: str,
        platform_user_id: str | None,
    ) -> sqlite3.Row | None:
        if yclients_record_id:
            row = connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE yclients_record_id = ? AND COALESCE(source, 'yclients') = ? AND COALESCE(is_test, 0) = ?
                LIMIT 1
                """,
                (yclients_record_id, source, is_test),
            ).fetchone()
            if row is not None:
                return row
        if platform_user_id:
            return connection.execute(
                """
                SELECT * FROM cancellation_recovery_events
                WHERE platform = ? AND platform_user_id = ? AND COALESCE(yclients_record_id, '') = COALESCE(?, '')
                LIMIT 1
                """,
                (platform, platform_user_id, yclients_record_id or ""),
            ).fetchone()
        return None

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
        platform=_row_optional_text(row, "platform") or PLATFORM_MAX,
        platform_user_id=_row_optional_text(row, "platform_user_id") or "",
        yclients_record_id=_row_optional_text(row, "yclients_record_id") or "",
        yclients_client_id=_row_optional_text(row, "yclients_client_id"),
        client_tg_id=_row_optional_text(row, "client_tg_id"),
        staff_id=_row_optional_text(row, "staff_id"),
        staff_name=_row_optional_text(row, "staff_name"),
        service_id=_row_optional_text(row, "service_id"),
        service_name=_row_optional_text(row, "service_name"),
        cancelled_booking_datetime_utc=_row_optional_text(row, "cancelled_booking_datetime_utc"),
        cancellation_detected_at_utc=_row_optional_text(row, "cancellation_detected_at_utc"),
        scheduled_send_at_utc=_row_optional_text(row, "scheduled_send_at_utc"),
        branch_timezone=_row_optional_text(row, "branch_timezone"),
        is_test=bool(int(_row_optional_text(row, "is_test") or 0)),
        source=_row_optional_text(row, "source"),
        clicked_at_utc=_row_optional_text(row, "clicked_at_utc"),
        error_summary=_row_optional_text(row, "error_summary"),
        max_user_id=_row_optional_text(row, "max_user_id"),
        chat_id=_row_optional_text(row, "chat_id"),
        scheduled_at=_row_optional_text(row, "scheduled_at"),
        status=_row_optional_text(row, "status") or "pending",
        sent_at=_row_optional_text(row, "sent_at"),
        skipped_reason=_row_optional_text(row, "skipped_reason"),
        created_at=_row_optional_text(row, "created_at"),
        updated_at=_row_optional_text(row, "updated_at"),
    )


def _row_optional_text(row: Mapping[str, Any], key: str) -> str | None:
    if key not in row.keys():
        return None
    return _optional_text(row[key])


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_error_text(value: object) -> str | None:
    text = _optional_text(value)
    return text[:200] if text else None
