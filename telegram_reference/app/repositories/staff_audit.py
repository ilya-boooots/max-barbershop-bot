from __future__ import annotations

from typing import Any

from app.db.sqlite import execute, fetchall


async def add_audit(
    target_tg_id: int,
    old_role: str,
    new_role: str,
    changed_by_tg_id: int,
    changed_at_iso: str,
) -> None:
    await execute(
        """
        INSERT INTO staff_role_audit (
            target_tg_id,
            old_role,
            new_role,
            changed_by_tg_id,
            changed_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (target_tg_id, old_role, new_role, changed_by_tg_id, changed_at_iso),
    )


async def get_audit_last(target_tg_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT target_tg_id,
               old_role,
               new_role,
               changed_by_tg_id,
               changed_at
        FROM staff_role_audit
        WHERE target_tg_id = ?
        ORDER BY changed_at DESC
        LIMIT ?
        """,
        (target_tg_id, limit),
    )
    return [dict(row) for row in rows]
