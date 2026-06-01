from __future__ import annotations

import time
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone


def _now_unix() -> int:
    return int(time.time())


async def create_transaction(
    user_tg_id: int,
    type: str,
    amount: int,
    check_sum: int | None,
    created_by_tg_id: int,
    reason: str | None = None,
) -> None:
    await execute(
        """
        INSERT INTO transactions (
            user_tg_id,
            type,
            amount,
            check_sum,
            reason,
            created_by_tg_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_tg_id, type, amount, check_sum, reason, created_by_tg_id, _now_unix()),
    )


async def get_last_transactions(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT id,
               user_tg_id,
               type,
               amount,
               check_sum,
               created_by_tg_id,
               created_at
        FROM transactions
        WHERE user_tg_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(row) for row in rows]


async def get_user_transactions(
    user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT id,
               user_tg_id,
               type,
               amount,
               check_sum,
               reason,
               created_at
        FROM transactions
        WHERE user_tg_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    return [dict(row) for row in rows]


async def count_user_transactions(user_id: int) -> int:
    row = await fetchone(
        "SELECT COUNT(1) AS total FROM transactions WHERE user_tg_id = ?",
        (user_id,),
    )
    if not row:
        return 0
    return int(row["total"])


async def get_total_spent_bonuses(user_id: int) -> int:
    row = await fetchone(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM transactions
        WHERE user_tg_id = ?
          AND type = 'spend'
        """,
        (user_id,),
    )
    if not row:
        return 0
    return int(row["total"])


async def get_total_purchase_sum(user_id: int) -> int:
    row = await fetchone(
        """
        SELECT COALESCE(SUM(check_sum), 0) AS total
        FROM transactions
        WHERE user_tg_id = ?
          AND type = 'accrual'
          AND check_sum IS NOT NULL
        """,
        (user_id,),
    )
    if not row:
        return 0
    return int(row["total"])


async def has_registration_bonus(user_id: int) -> bool:
    rows = await fetchall(
        """
        SELECT 1
        FROM transactions
        WHERE user_tg_id = ?
          AND type = 'registration_bonus'
        LIMIT 1
        """,
        (user_id,),
    )
    return bool(rows)


async def get_staff_action_logs(
    staff_tg_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT transactions.id,
               transactions.user_tg_id,
               transactions.type,
               transactions.amount,
               transactions.check_sum,
               transactions.reason,
               transactions.created_at,
               users.name AS client_name
        FROM transactions
        LEFT JOIN users ON users.user_id = transactions.user_tg_id
        WHERE transactions.created_by_tg_id = ?
        ORDER BY transactions.created_at DESC
        LIMIT ? OFFSET ?
        """,
        (staff_tg_id, limit, offset),
    )
    return [dict(row) for row in rows]


async def count_staff_action_logs(staff_tg_id: int) -> int:
    rows = await fetchall(
        "SELECT COUNT(1) AS total FROM transactions WHERE created_by_tg_id = ?",
        (staff_tg_id,),
    )
    if not rows:
        return 0
    return int(rows[0]["total"])
