from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_event(payload: dict[str, Any]) -> int | None:
    now = _now()
    await execute(
        """
        INSERT OR IGNORE INTO post_visit_feedback_events (
            yclients_record_id, yclients_client_id, client_tg_id, client_name, client_phone,
            staff_id, staff_name, service_id, service_name, visit_datetime_utc, branch_timezone,
            status, created_at_utc, updated_at_utc, is_test, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            payload.get("yclients_record_id"),
            payload.get("yclients_client_id"),
            payload.get("client_tg_id"),
            payload.get("client_name"),
            payload.get("client_phone"),
            payload.get("staff_id"),
            payload.get("staff_name"),
            payload.get("service_id"),
            payload.get("service_name"),
            payload.get("visit_datetime_utc"),
            payload.get("branch_timezone") or "UTC",
            now,
            now,
            1 if payload.get("is_test") else 0,
            payload.get("source") or "yclients",
        ),
    )
    row = await fetchone("SELECT id FROM post_visit_feedback_events WHERE yclients_record_id=? AND source=? AND is_test=?", (payload.get("yclients_record_id"), payload.get("source") or "yclients", 1 if payload.get("is_test") else 0))
    return int(row[0]) if row else None


async def get_event(event_id: int) -> dict[str, Any] | None:
    row = await fetchone("SELECT * FROM post_visit_feedback_events WHERE id=?", (event_id,))
    return dict(row) if row else None


async def set_status(event_id: int, status: str, **fields: Any) -> None:
    sets = ["status=?", "updated_at_utc=?"]
    params: list[Any] = [status, _now()]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        params.append(v)
    params.append(event_id)
    await execute(f"UPDATE post_visit_feedback_events SET {', '.join(sets)} WHERE id=?", tuple(params))


async def find_pending_to_send(now_utc: str) -> list[dict[str, Any]]:
    rows = await fetchall(
        "SELECT * FROM post_visit_feedback_events WHERE status='pending' AND visit_datetime_utc IS NOT NULL AND visit_datetime_utc<=?",
        (now_utc,),
    )
    return [dict(r) for r in rows]


async def find_waiting_comment_by_client(client_tg_id: int) -> dict[str, Any] | None:
    row = await fetchone(
        "SELECT * FROM post_visit_feedback_events WHERE client_tg_id=? AND status='waiting_negative_comment' ORDER BY id DESC LIMIT 1",
        (client_tg_id,),
    )
    return dict(row) if row else None


async def cleanup_dev_test_events() -> None:
    await execute("DELETE FROM post_visit_feedback_events WHERE is_test=1 AND source='dev_test'")
