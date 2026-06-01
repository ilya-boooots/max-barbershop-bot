from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_event(**kwargs) -> int:
    await execute(
        """
        INSERT INTO birthday_funnel_events (
            yclients_client_id, client_tg_id, birth_date, birthday_year, scheduled_send_at_utc,
            sent_at_utc, clicked_at_utc, yclients_booking_id, status, branch_timezone, source,
            is_test, error_summary, created_at_utc, updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            kwargs.get("yclients_client_id"), kwargs.get("client_tg_id"), kwargs.get("birth_date"), kwargs.get("birthday_year"), kwargs.get("scheduled_send_at_utc"),
            kwargs.get("sent_at_utc"), kwargs.get("clicked_at_utc"), kwargs.get("yclients_booking_id"), kwargs.get("status", "pending"), kwargs.get("branch_timezone"), kwargs.get("source", "local_db"),
            1 if kwargs.get("is_test") else 0, kwargs.get("error_summary"), _now_iso(), _now_iso(),
        ),
    )
    row = await fetchone(
        """
        SELECT id FROM birthday_funnel_events
        WHERE client_tg_id=? AND birthday_year=? AND is_test=?
        ORDER BY id DESC LIMIT 1
        """,
        (kwargs.get("client_tg_id"), kwargs.get("birthday_year"), 1 if kwargs.get("is_test") else 0),
    )
    return int(row["id"]) if row else 0


async def find_by_client_year(client_tg_id: int, birthday_year: int, *, is_test: bool = False):
    return await fetchone(
        "SELECT * FROM birthday_funnel_events WHERE client_tg_id=? AND birthday_year=? AND is_test=? ORDER BY id DESC LIMIT 1",
        (client_tg_id, birthday_year, 1 if is_test else 0),
    )


async def mark_status(event_id: int, status: str, *, clicked: bool = False, sent: bool = False, booking_id: str | None = None, error_summary: str | None = None):
    await execute(
        """
        UPDATE birthday_funnel_events
        SET status=?, clicked_at_utc=CASE WHEN ?=1 THEN ? ELSE clicked_at_utc END,
            sent_at_utc=CASE WHEN ?=1 THEN ? ELSE sent_at_utc END,
            yclients_booking_id=COALESCE(?, yclients_booking_id), error_summary=?, updated_at_utc=?
        WHERE id=?
        """,
        (status, 1 if clicked else 0, _now_iso(), 1 if sent else 0, _now_iso(), booking_id, error_summary, _now_iso(), event_id),
    )


async def get_event(event_id: int):
    row = await fetchone("SELECT * FROM birthday_funnel_events WHERE id=?", (event_id,))
    return dict(row) if row else None
