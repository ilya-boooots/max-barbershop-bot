from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchall


async def create_loyalty_action(
    *,
    staff_tg_id: int,
    yclients_visit_or_record_id: str,
    yclients_client_id: str | None,
    action_type: str,
    value: str,
    status: str,
    error_short: str | None = None,
) -> None:
    await execute(
        """
        INSERT INTO loyalty_actions (
            staff_tg_id,
            yclients_visit_or_record_id,
            yclients_client_id,
            action_type,
            value,
            status,
            created_at,
            error_short
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            staff_tg_id,
            yclients_visit_or_record_id,
            yclients_client_id,
            action_type,
            value,
            status,
            datetime.now(timezone.utc).isoformat(),
            error_short,
        ),
    )


async def list_recent_loyalty_actions(limit: int = 20) -> list[dict]:
    rows = await fetchall(
        """
        SELECT
            id,
            staff_tg_id,
            yclients_visit_or_record_id,
            yclients_client_id,
            action_type,
            value,
            status,
            created_at,
            error_short
        FROM loyalty_actions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]
