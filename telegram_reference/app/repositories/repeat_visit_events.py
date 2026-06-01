from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.sqlite import execute, fetchone


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_event(**kwargs) -> int:
    await execute(
        """
        INSERT INTO repeat_visit_events (
            yclients_client_id, client_tg_id, yclients_visit_id, yclients_service_id, service_name,
            last_visit_datetime_utc, delay_days, scheduled_send_at_utc, selected_template_index,
            selected_template_text, sent_at_utc, clicked_at_utc, status, branch_timezone, source,
            is_test, error_summary, created_at_utc, updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            kwargs.get("yclients_client_id"),
            kwargs.get("client_tg_id"),
            kwargs.get("yclients_visit_id"),
            kwargs.get("yclients_service_id"),
            kwargs.get("service_name"),
            kwargs.get("last_visit_datetime_utc"),
            kwargs.get("delay_days", 30),
            kwargs.get("scheduled_send_at_utc"),
            kwargs.get("selected_template_index"),
            kwargs.get("selected_template_text"),
            kwargs.get("sent_at_utc"),
            kwargs.get("clicked_at_utc"),
            kwargs.get("status", "candidate"),
            kwargs.get("branch_timezone"),
            kwargs.get("source", "yclients"),
            1 if kwargs.get("is_test") else 0,
            kwargs.get("error_summary"),
            _now(),
            _now(),
        ),
    )
    row = await fetchone("SELECT last_insert_rowid() AS id")
    return int(row["id"]) if row else 0


async def mark_status(event_id: int, status: str, *, clicked: bool = False, sent: bool = False, error_summary: str | None = None) -> None:
    await execute(
        """
        UPDATE repeat_visit_events
        SET status=?,
            error_summary=?,
            clicked_at_utc=CASE WHEN ?=1 THEN ? ELSE clicked_at_utc END,
            sent_at_utc=CASE WHEN ?=1 THEN ? ELSE sent_at_utc END,
            updated_at_utc=?
        WHERE id=?
        """,
        (status, error_summary, 1 if clicked else 0, _now(), 1 if sent else 0, _now(), _now(), event_id),
    )


async def has_event_for_visit(client_tg_id: int, visit_id: str | None, service_id: str | None) -> bool:
    row = await fetchone(
        """
        SELECT id FROM repeat_visit_events
        WHERE client_tg_id=? AND yclients_visit_id IS ? AND yclients_service_id IS ? AND is_test=0
          AND status IN ('pending','sent','clicked_booking','blocked','failed')
        LIMIT 1
        """,
        (client_tg_id, visit_id, service_id),
    )
    return row is not None


async def has_recent_sent(client_tg_id: int, cooldown_hours: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat()
    row = await fetchone(
        "SELECT id FROM repeat_visit_events WHERE client_tg_id=? AND sent_at_utc IS NOT NULL AND sent_at_utc>=? AND is_test=0 LIMIT 1",
        (client_tg_id, cutoff),
    )
    return row is not None


async def get_event(event_id: int):
    row = await fetchone("SELECT * FROM repeat_visit_events WHERE id=?", (event_id,))
    return dict(row) if row else None
