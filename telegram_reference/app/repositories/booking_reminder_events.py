from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchall, fetchone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_event(**kwargs) -> int | None:
    ts = now_iso()
    await execute(
        """
        INSERT OR IGNORE INTO booking_reminder_events (
            yclients_record_id,yclients_client_id,client_tg_id,client_phone,company_id,
            visit_datetime_utc,branch_timezone,reminder_type,status,scheduled_at_utc,
            sent_at_utc,clicked_at_utc,error,created_at_utc,updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            kwargs.get("yclients_record_id"), kwargs.get("yclients_client_id"), kwargs.get("client_tg_id"), kwargs.get("client_phone"), kwargs.get("company_id"),
            kwargs.get("visit_datetime_utc"), kwargs.get("branch_timezone") or "UTC", kwargs.get("reminder_type"), kwargs.get("status") or "pending", kwargs.get("scheduled_at_utc"),
            kwargs.get("sent_at_utc"), kwargs.get("clicked_at_utc"), kwargs.get("error"), ts, ts,
        ),
    )
    row = await fetchone("SELECT id FROM booking_reminder_events WHERE yclients_record_id=? AND reminder_type=?", (kwargs.get("yclients_record_id"), kwargs.get("reminder_type")))
    return int(row["id"]) if row else None


async def get_due_events(now_utc: str) -> list[dict]:
    rows = await fetchall("SELECT * FROM booking_reminder_events WHERE status='pending' AND scheduled_at_utc<=? ORDER BY scheduled_at_utc ASC LIMIT 100", (now_utc,))
    return [dict(r) for r in rows]


async def get_event(event_id: int) -> dict | None:
    row = await fetchone("SELECT * FROM booking_reminder_events WHERE id=?", (event_id,))
    return dict(row) if row else None


async def mark_status(event_id: int, status: str, *, error: str | None = None, sent: bool = False, clicked: bool = False) -> None:
    ts = now_iso()
    sent_at = ts if sent else None
    clicked_at = ts if clicked else None
    await execute(
        "UPDATE booking_reminder_events SET status=?, error=?, sent_at_utc=COALESCE(?, sent_at_utc), clicked_at_utc=COALESCE(?, clicked_at_utc), updated_at_utc=? WHERE id=?",
        (status, error, sent_at, clicked_at, ts, event_id),
    )


async def cleanup_dev_test_events() -> None:
    await execute("DELETE FROM booking_reminder_events WHERE company_id='dev_test' OR yclients_record_id LIKE 'dev-test-%'")
