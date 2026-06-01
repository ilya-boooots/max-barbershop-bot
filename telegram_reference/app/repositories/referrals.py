from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_referral_code(user_tg_id: int) -> str | None:
    row = await fetchone("SELECT referral_code FROM referral_codes WHERE user_tg_id = ?", (user_tg_id,))
    return str(row["referral_code"]) if row and row["referral_code"] else None


async def save_referral_code(user_tg_id: int, referral_code: str) -> None:
    await execute(
        """
        INSERT INTO referral_codes (user_tg_id, referral_code, created_at_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(user_tg_id) DO UPDATE SET referral_code = excluded.referral_code
        """,
        (user_tg_id, referral_code, _now_iso()),
    )


async def get_user_by_referral_code(referral_code: str) -> int | None:
    row = await fetchone(
        "SELECT user_tg_id FROM referral_codes WHERE referral_code = ?",
        (referral_code.strip().upper(),),
    )
    return int(row["user_tg_id"]) if row else None


async def get_referral_attribution(invited_tg_id: int) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT invited_tg_id,
               inviter_tg_id,
               referral_code,
               attributed_at_utc,
               status,
               invited_yclients_client_id,
               qualifying_record_id,
               qualifying_visit_at_utc,
               rewarded_at_utc,
               invited_had_paid_before
        FROM referral_attributions
        WHERE invited_tg_id = ?
        """,
        (invited_tg_id,),
    )
    return dict(row) if row else None


async def create_referral_attribution(*, invited_tg_id: int, inviter_tg_id: int, referral_code: str) -> bool:
    existing = await get_referral_attribution(invited_tg_id)
    if existing:
        return False
    await execute(
        """
        INSERT INTO referral_attributions (
            invited_tg_id,
            inviter_tg_id,
            referral_code,
            attributed_at_utc,
            status,
            invited_had_paid_before
        )
        VALUES (?, ?, ?, ?, 'pending', 0)
        """,
        (invited_tg_id, inviter_tg_id, referral_code.strip().upper(), _now_iso()),
    )
    return True


async def mark_referral_blocked_existing_paid(*, invited_tg_id: int, yclients_client_id: str | None) -> None:
    await execute(
        """
        UPDATE referral_attributions
        SET status = 'blocked_existing_paid',
            invited_had_paid_before = 1,
            invited_yclients_client_id = COALESCE(?, invited_yclients_client_id)
        WHERE invited_tg_id = ?
        """,
        (yclients_client_id, invited_tg_id),
    )


async def mark_referral_rewarded(
    *,
    invited_tg_id: int,
    yclients_client_id: str | None,
    qualifying_record_id: str,
    qualifying_visit_at_utc: str,
) -> None:
    await execute(
        """
        UPDATE referral_attributions
        SET status = 'rewarded',
            invited_yclients_client_id = COALESCE(?, invited_yclients_client_id),
            qualifying_record_id = ?,
            qualifying_visit_at_utc = ?,
            rewarded_at_utc = ?
        WHERE invited_tg_id = ?
        """,
        (yclients_client_id, qualifying_record_id, qualifying_visit_at_utc, _now_iso(), invited_tg_id),
    )


async def list_invited_by_user(inviter_tg_id: int) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT invited_tg_id,
               status,
               rewarded_at_utc
        FROM referral_attributions
        WHERE inviter_tg_id = ?
        ORDER BY datetime(attributed_at_utc) DESC
        """,
        (inviter_tg_id,),
    )
    return [dict(row) for row in rows]
