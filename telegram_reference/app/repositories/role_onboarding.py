from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_role_onboarding(telegram_id: int, role: str) -> dict | None:
    row = await fetchone(
        """
        SELECT telegram_id, role, status, current_step, started_at_utc, completed_at_utc, skipped_at_utc, updated_at_utc
        FROM role_onboarding
        WHERE telegram_id = ? AND role = ?
        """,
        (telegram_id, role),
    )
    return dict(row) if row else None


async def upsert_onboarding_progress(telegram_id: int, role: str, step: int) -> None:
    now = _now_iso()
    await execute(
        """
        INSERT INTO role_onboarding (telegram_id, role, status, current_step, started_at_utc, updated_at_utc)
        VALUES (?, ?, 'in_progress', ?, ?, ?)
        ON CONFLICT(telegram_id, role) DO UPDATE SET
            status = CASE WHEN role_onboarding.status IN ('completed', 'skipped') THEN role_onboarding.status ELSE 'in_progress' END,
            current_step = CASE WHEN role_onboarding.status IN ('completed', 'skipped') THEN role_onboarding.current_step ELSE excluded.current_step END,
            started_at_utc = COALESCE(role_onboarding.started_at_utc, excluded.started_at_utc),
            updated_at_utc = excluded.updated_at_utc
        """,
        (telegram_id, role, step, now, now),
    )


async def upsert_onboarding_completed(telegram_id: int, role: str) -> None:
    now = _now_iso()
    await execute(
        """
        INSERT INTO role_onboarding (telegram_id, role, status, current_step, started_at_utc, completed_at_utc, updated_at_utc)
        VALUES (?, ?, 'completed', 10, ?, ?, ?)
        ON CONFLICT(telegram_id, role) DO UPDATE SET
            status = 'completed',
            current_step = 10,
            completed_at_utc = excluded.completed_at_utc,
            updated_at_utc = excluded.updated_at_utc
        """,
        (telegram_id, role, now, now, now),
    )


async def upsert_onboarding_skipped(telegram_id: int, role: str) -> None:
    now = _now_iso()
    await execute(
        """
        INSERT INTO role_onboarding (telegram_id, role, status, current_step, started_at_utc, skipped_at_utc, updated_at_utc)
        VALUES (?, ?, 'skipped', 1, ?, ?, ?)
        ON CONFLICT(telegram_id, role) DO UPDATE SET
            status = 'skipped',
            skipped_at_utc = excluded.skipped_at_utc,
            updated_at_utc = excluded.updated_at_utc
        """,
        (telegram_id, role, now, now, now),
    )
