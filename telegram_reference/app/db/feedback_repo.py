from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone

GMT_PLUS_4 = timezone(timedelta(hours=4))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_local_iso() -> str:
    return datetime.now(GMT_PLUS_4).isoformat()


def format_gmt4(value: str | None) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(GMT_PLUS_4).strftime("%d.%m.%Y %H:%M")


async def create_feedback(*, user_id: int, rating: int, text: str | None = None) -> int:
    ts_utc = now_utc_iso()
    ts_local = now_local_iso()
    await execute(
        """
        INSERT INTO feedback (ts_utc, ts_local, user_id, rating, text, status)
        VALUES (?, ?, ?, ?, ?, 'open')
        """,
        (ts_utc, ts_local, user_id, rating, text),
    )
    row = await fetchone("SELECT id FROM feedback ORDER BY id DESC LIMIT 1")
    return int(row["id"]) if row else 0


async def get_feedback_by_id(feedback_id: int) -> dict[str, Any] | None:
    row = await fetchone("SELECT * FROM feedback WHERE id = ?", (feedback_id,))
    return dict(row) if row else None


async def close_feedback(*, feedback_id: int, admin_id: int) -> None:
    await execute(
        """
        UPDATE feedback
        SET status = 'closed',
            closed_by = ?,
            closed_ts_utc = ?
        WHERE id = ?
        """,
        (admin_id, now_utc_iso(), feedback_id),
    )


async def save_feedback_reply(*, feedback_id: int, admin_id: int, text: str) -> int:
    await execute(
        """
        INSERT INTO feedback_replies (feedback_id, ts_utc, admin_id, text)
        VALUES (?, ?, ?, ?)
        """,
        (feedback_id, now_utc_iso(), admin_id, text),
    )
    row = await fetchone("SELECT id FROM feedback_replies ORDER BY id DESC LIMIT 1")
    return int(row["id"]) if row else 0


async def get_admin_ids() -> list[int]:
    rows = await fetchall("SELECT user_id FROM users WHERE role = 'admin'")
    return [int(row["user_id"]) for row in rows]


async def get_feedback_user_context(user_id: int) -> dict[str, Any]:
    row = await fetchone(
        """
        SELECT user_id, username, phone, name, display_name
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )
    booking_stats = await fetchone(
        """
        SELECT
            COUNT(*) AS bookings_total,
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS bookings_approved,
            SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS bookings_cancelled
        FROM bookings
        WHERE user_id = ?
        """,
        (user_id,),
    )
    last_booking = await fetchone(
        """
        SELECT COALESCE(created_ts_local, created_ts_utc) AS ts_value, status
        FROM bookings
        WHERE user_id = ?
        ORDER BY created_ts_utc DESC
        LIMIT 1
        """,
        (user_id,),
    )
    return {
        "user": dict(row) if row else None,
        # TODO: replace with real visits source when available.
        "visits_count": 0,
        "bookings_total": int(booking_stats["bookings_total"] or 0) if booking_stats else 0,
        "bookings_approved": int(booking_stats["bookings_approved"] or 0) if booking_stats else 0,
        "bookings_cancelled": int(booking_stats["bookings_cancelled"] or 0) if booking_stats else 0,
        "last_booking": dict(last_booking) if last_booking else None,
    }
