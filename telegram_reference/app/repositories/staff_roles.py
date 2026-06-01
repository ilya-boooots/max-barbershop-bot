from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone

VALID_ROLES = {"developer", "admin", "manager"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_role(tg_id: int) -> str | None:
    row = await fetchone("SELECT role FROM staff_roles WHERE tg_id = ?", (tg_id,))
    if row is None:
        return None
    role = row["role"]
    return role if role in VALID_ROLES else None


async def set_role(tg_id: int, role: str, assigned_by: int | None) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    assigned_at = _now_iso()
    await execute(
        """
        INSERT INTO staff_roles (tg_id, role, assigned_by, assigned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET
            role = excluded.role,
            assigned_by = excluded.assigned_by,
            assigned_at = excluded.assigned_at
        """,
        (tg_id, role, assigned_by, assigned_at),
    )


async def remove_role(tg_id: int) -> None:
    await execute("DELETE FROM staff_roles WHERE tg_id = ?", (tg_id,))


async def list_staff() -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT
            sr.tg_id,
            sr.role,
            sr.assigned_by,
            sr.assigned_at,
            u.username,
            u.name,
            u.display_name,
            by_u.username AS assigned_by_username,
            by_u.name AS assigned_by_name,
            by_u.display_name AS assigned_by_display_name
        FROM staff_roles sr
        LEFT JOIN users u ON u.user_id = sr.tg_id
        LEFT JOIN users by_u ON by_u.user_id = sr.assigned_by
        ORDER BY CASE sr.role
            WHEN 'developer' THEN 1
            WHEN 'admin' THEN 2
            WHEN 'manager' THEN 3
            ELSE 4
        END, sr.assigned_at DESC
        """
    )
    return [dict(row) for row in rows]
