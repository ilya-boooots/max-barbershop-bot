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
        INSERT OR IGNORE INTO cancellation_recovery_events (
            yclients_record_id, yclients_client_id, client_tg_id,
            staff_id, staff_name, service_id, service_name,
            cancelled_booking_datetime_utc, cancellation_detected_at_utc,
            scheduled_send_at_utc, branch_timezone, status,
            created_at_utc, updated_at_utc, is_test, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            payload.get("yclients_record_id"),
            payload.get("yclients_client_id"),
            payload.get("client_tg_id"),
            payload.get("staff_id"),
            payload.get("staff_name"),
            payload.get("service_id"),
            payload.get("service_name"),
            payload.get("cancelled_booking_datetime_utc"),
            payload.get("cancellation_detected_at_utc") or now,
            payload.get("scheduled_send_at_utc"),
            payload.get("branch_timezone") or "UTC",
            now,
            now,
            1 if payload.get("is_test") else 0,
            payload.get("source") or "yclients",
        ),
    )
    row = await fetchone(
        "SELECT id FROM cancellation_recovery_events WHERE yclients_record_id=? AND source=? AND is_test=?",
        (payload.get("yclients_record_id"), payload.get("source") or "yclients", 1 if payload.get("is_test") else 0),
    )
    return int(row[0]) if row else None


async def find_pending_to_send(now_utc: str) -> list[dict[str, Any]]:
    rows = await fetchall(
        "SELECT * FROM cancellation_recovery_events WHERE status='pending' AND scheduled_send_at_utc IS NOT NULL AND scheduled_send_at_utc<=?",
        (now_utc,),
    )
    return [dict(r) for r in rows]


async def get_event(event_id: int) -> dict[str, Any] | None:
    row = await fetchone("SELECT * FROM cancellation_recovery_events WHERE id=?", (event_id,))
    return dict(row) if row else None


async def set_status(event_id: int, status: str, **fields: Any) -> None:
    sets = ["status=?", "updated_at_utc=?"]
    params: list[Any] = [status, _now()]
    for k, v in fields.items():
        sets.append(f"{k}=?")
        params.append(v)
    params.append(event_id)
    await execute(f"UPDATE cancellation_recovery_events SET {', '.join(sets)} WHERE id=?", tuple(params))
