from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import get_db_path
from app.db.sqlite import fetchall, fetchone


_OPERATION_TYPES = {
    "visit_accrual",
    "writeoff_yclients",
    "referral_bonus_inviter",
    "referral_bonus_invited",
    "manual_adjustment",
    "welcome_bonus",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_operation(
    *,
    user_tg_id: int,
    operation_type: str,
    points_delta: int,
    reason: str,
    source: str,
    source_event_id: str | None = None,
    yclients_client_id: str | None = None,
    branch_timezone: str | None = None,
) -> int:
    normalized_type = operation_type if operation_type in _OPERATION_TYPES else "manual_adjustment"
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("BEGIN")
        cursor = await db.execute(
            """
            UPDATE users
            SET loyalty_balance = loyalty_balance + ?,
                bonus_balance = bonus_balance + ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (points_delta, points_delta, _now_iso(), user_tg_id),
        )
        if cursor.rowcount == 0:
            await db.rollback()
            raise ValueError("User not found for loyalty operation")

        balance_cursor = await db.execute(
            "SELECT loyalty_balance, yclients_client_id FROM users WHERE user_id = ?",
            (user_tg_id,),
        )
        balance_row = await balance_cursor.fetchone()
        await balance_cursor.close()
        resulting_balance = int(balance_row[0]) if balance_row else 0
        resolved_client_id = (
            yclients_client_id
            if yclients_client_id is not None
            else (str(balance_row[1]) if balance_row and balance_row[1] is not None else None)
        )

        created_at = _now_iso()
        insert_cursor = await db.execute(
            """
            INSERT INTO loyalty_operations (
                user_tg_id,
                yclients_client_id,
                operation_type,
                points_delta,
                reason,
                source,
                source_event_id,
                created_at_utc,
                branch_timezone,
                resulting_balance
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_tg_id,
                resolved_client_id,
                normalized_type,
                points_delta,
                reason,
                source,
                source_event_id,
                created_at,
                branch_timezone,
                resulting_balance,
            ),
        )
        operation_id = int(insert_cursor.lastrowid)
        await db.commit()
        return operation_id


async def operation_exists_by_source_event(*, source: str, source_event_id: str) -> bool:
    row = await fetchone(
        """
        SELECT 1
        FROM loyalty_operations
        WHERE source = ?
          AND source_event_id = ?
        LIMIT 1
        """,
        (source, source_event_id),
    )
    return bool(row)


async def get_user_operations(user_tg_id: int, *, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT id,
               user_tg_id,
               yclients_client_id,
               operation_type,
               points_delta,
               reason,
               source,
               source_event_id,
               created_at_utc,
               branch_timezone,
               resulting_balance
        FROM loyalty_operations
        WHERE user_tg_id = ?
        ORDER BY datetime(created_at_utc) DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user_tg_id, limit, offset),
    )
    return [dict(row) for row in rows]


async def count_user_operations(user_tg_id: int) -> int:
    row = await fetchone(
        "SELECT COUNT(1) AS total FROM loyalty_operations WHERE user_tg_id = ?",
        (user_tg_id,),
    )
    return int(row["total"]) if row else 0


async def get_last_user_operation(user_tg_id: int) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT id,
               operation_type,
               points_delta,
               reason,
               source,
               created_at_utc,
               resulting_balance
        FROM loyalty_operations
        WHERE user_tg_id = ?
        ORDER BY datetime(created_at_utc) DESC, id DESC
        LIMIT 1
        """,
        (user_tg_id,),
    )
    return dict(row) if row else None
