from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.db.sqlite import execute, fetchall, fetchone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_event(**kwargs) -> int:
    await execute(
        """
        INSERT INTO lost_client_events (
            yclients_client_id, client_tg_id, threshold_days, segment_key, last_visit_datetime_utc,
            last_visit_id, has_future_booking, scheduled_send_at_utc, sent_at_utc, clicked_at_utc,
            status, source, is_test, error_summary, created_at_utc, updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            kwargs.get("yclients_client_id"), kwargs.get("client_tg_id"), kwargs.get("threshold_days"), kwargs.get("segment_key"), kwargs.get("last_visit_datetime_utc"),
            kwargs.get("last_visit_id"), 1 if kwargs.get("has_future_booking") else 0, kwargs.get("scheduled_send_at_utc"), kwargs.get("sent_at_utc"), kwargs.get("clicked_at_utc"),
            kwargs.get("status", "candidate"), kwargs.get("source", "yclients"), 1 if kwargs.get("is_test") else 0, kwargs.get("error_summary"), now_iso(), now_iso(),
        ),
    )
    row = await fetchone("SELECT last_insert_rowid() AS id")
    return int(row["id"]) if row else 0


async def mark_status(event_id: int, status: str, *, error_summary: str | None = None, clicked: bool = False, sent: bool = False) -> None:
    await execute(
        """
        UPDATE lost_client_events
        SET status=?, error_summary=?, clicked_at_utc=CASE WHEN ?=1 THEN ? ELSE clicked_at_utc END,
            sent_at_utc=CASE WHEN ?=1 THEN ? ELSE sent_at_utc END, updated_at_utc=?
        WHERE id=?
        """,
        (status, error_summary, 1 if clicked else 0, now_iso(), 1 if sent else 0, now_iso(), now_iso(), event_id),
    )


async def has_recent_sent(tg_id: int, threshold_days: int, cooldown_days: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cooldown_days)).isoformat()
    row = await fetchone(
        "SELECT id FROM lost_client_events WHERE client_tg_id=? AND threshold_days=? AND sent_at_utc IS NOT NULL AND sent_at_utc>=? AND is_test=0 ORDER BY id DESC LIMIT 1",
        (tg_id, threshold_days, cutoff),
    )
    return row is not None


async def get_recent_stats(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = await fetchone("SELECT COUNT(1) AS cnt FROM lost_client_events WHERE sent_at_utc IS NOT NULL AND sent_at_utc>=?", (cutoff,))
    return int(row["cnt"] if row else 0)


async def get_event(event_id: int):
    row = await fetchone("SELECT * FROM lost_client_events WHERE id=?", (event_id,))
    return dict(row) if row else None


async def find_latest_by_tg_threshold(tg_id: int, threshold_days: int):
    return await fetchone("SELECT * FROM lost_client_events WHERE client_tg_id=? AND threshold_days=? ORDER BY id DESC LIMIT 1", (tg_id, threshold_days))
