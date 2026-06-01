from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchall


async def upsert_telegram_attribution(
    *,
    company_id: str,
    record_id: str,
    client_id: str | None,
    created_via: str,
    original_record_id: str | None = None,
    is_active: bool = True,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    await execute(
        """
        INSERT INTO telegram_attribution (
            company_id,
            record_id,
            client_id,
            source,
            created_via,
            created_at,
            original_record_id,
            is_active
        )
        VALUES (?, ?, ?, 'telegram_bot', ?, ?, ?, ?)
        ON CONFLICT(record_id) DO UPDATE SET
            company_id=excluded.company_id,
            client_id=COALESCE(excluded.client_id, telegram_attribution.client_id),
            created_via=excluded.created_via,
            original_record_id=COALESCE(excluded.original_record_id, telegram_attribution.original_record_id),
            is_active=excluded.is_active
        """,
        (
            company_id,
            record_id,
            client_id,
            created_via,
            created_at,
            original_record_id,
            1 if is_active else 0,
        ),
    )


async def deactivate_telegram_attribution(*, record_id: str) -> None:
    await execute("UPDATE telegram_attribution SET is_active = 0 WHERE record_id = ?", (record_id,))


async def list_active_telegram_record_ids(*, company_id: str) -> list[str]:
    rows = await fetchall(
        """
        SELECT record_id
        FROM telegram_attribution
        WHERE company_id = ?
          AND source = 'telegram_bot'
          AND is_active = 1
        """,
        (company_id,),
    )
    return [str(row["record_id"]).strip() for row in rows if row["record_id"] is not None]
