from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone

GMT_PLUS_4 = timezone(timedelta(hours=4))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_local_iso() -> str:
    return datetime.now(GMT_PLUS_4).isoformat()


def local_today_str() -> str:
    return datetime.now(GMT_PLUS_4).date().isoformat()


async def create_booking(
    *,
    user_id: int,
    name: str,
    phone: str,
    date_value: str,
    time_value: str,
    guests: int,
    comment: str,
) -> int:
    created_utc = now_utc_iso()
    created_local = now_local_iso()
    await execute(
        """
        INSERT INTO bookings (
            created_ts_utc,
            created_ts_local,
            user_id,
            name,
            phone,
            date,
            time,
            guests,
            comment,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            created_utc,
            created_local,
            user_id,
            name,
            phone,
            date_value,
            time_value,
            guests,
            comment,
        ),
    )
    row = await fetchone("SELECT id FROM bookings ORDER BY id DESC LIMIT 1")
    return int(row["id"]) if row else 0


async def get_booking_by_id(booking_id: int) -> dict[str, Any] | None:
    row = await fetchone("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    return dict(row) if row else None


async def count_user_bookings_today(user_id: int) -> int:
    row = await fetchone(
        """
        SELECT COUNT(*) AS cnt
        FROM bookings
        WHERE user_id = ?
          AND substr(COALESCE(created_ts_local, created_ts_utc), 1, 10) = ?
          AND status IN ('pending', 'approved', 'cancelled')
        """,
        (user_id, local_today_str()),
    )
    return int(row["cnt"]) if row else 0


async def get_booking_stats_for_user(user_id: int) -> dict[str, Any]:
    row = await fetchone(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
            SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count
        FROM bookings
        WHERE user_id = ?
        """,
        (user_id,),
    )
    last_row = await fetchone(
        """
        SELECT created_ts_local, created_ts_utc, status
        FROM bookings
        WHERE user_id = ?
        ORDER BY created_ts_utc DESC
        LIMIT 1
        """,
        (user_id,),
    )
    return {
        "total_count": int(row["total_count"] or 0) if row else 0,
        "approved_count": int(row["approved_count"] or 0) if row else 0,
        "cancelled_count": int(row["cancelled_count"] or 0) if row else 0,
        "last_booking": dict(last_row) if last_row else None,
    }


async def mark_hostess_notified(booking_id: int) -> None:
    await execute(
        "UPDATE bookings SET notified_hostess_ts_utc = ? WHERE id = ?",
        (now_utc_iso(), booking_id),
    )


async def approve_booking(booking_id: int, staff_id: int) -> bool:
    await execute(
        """
        UPDATE bookings
        SET status = 'approved',
            status_ts_utc = ?,
            status_by_staff_id = ?,
            last_reminder_ts_utc = NULL,
            status_reason = NULL
        WHERE id = ? AND status = 'pending'
        """,
        (now_utc_iso(), staff_id, booking_id),
    )
    updated = await fetchone("SELECT status, status_by_staff_id FROM bookings WHERE id = ?", (booking_id,))
    return bool(updated and updated["status"] == 'approved' and int(updated["status_by_staff_id"] or 0) == staff_id)


async def cancel_booking(booking_id: int, staff_id: int, reason: str | None = None) -> bool:
    await execute(
        """
        UPDATE bookings
        SET status = 'cancelled',
            status_ts_utc = ?,
            status_by_staff_id = ?,
            last_reminder_ts_utc = NULL,
            status_reason = ?
        WHERE id = ? AND status = 'pending'
        """,
        (now_utc_iso(), staff_id, reason, booking_id),
    )
    updated = await fetchone("SELECT status, status_by_staff_id FROM bookings WHERE id = ?", (booking_id,))
    return bool(updated and updated["status"] == 'cancelled' and int(updated["status_by_staff_id"] or 0) == staff_id)


async def get_pending_for_reminders(now_utc: datetime) -> list[dict[str, Any]]:
    five_minutes_ago = (now_utc - timedelta(minutes=5)).isoformat()
    rows = await fetchall(
        """
        SELECT *
        FROM bookings
        WHERE status = 'pending'
          AND created_ts_utc <= ?
          AND (
              last_reminder_ts_utc IS NULL
              OR last_reminder_ts_utc <= ?
          )
        ORDER BY id ASC
        """,
        (five_minutes_ago, five_minutes_ago),
    )
    return [dict(row) for row in rows]


async def touch_reminder(booking_id: int) -> None:
    await execute(
        """
        UPDATE bookings
        SET last_reminder_ts_utc = ?,
            reminder_count = COALESCE(reminder_count, 0) + 1
        WHERE id = ?
        """,
        (now_utc_iso(), booking_id),
    )
