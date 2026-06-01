from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_master_photo(
    company_id: str,
    staff_id: str,
    staff_name: str,
    file_id: str,
    updated_by_tg_id: int | None,
) -> None:
    await execute(
        """
        INSERT INTO master_photos (
            company_id,
            staff_id,
            staff_name,
            telegram_file_id,
            updated_at,
            updated_by_tg_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, staff_id) DO UPDATE SET
            staff_name = excluded.staff_name,
            telegram_file_id = excluded.telegram_file_id,
            updated_at = excluded.updated_at,
            updated_by_tg_id = excluded.updated_by_tg_id
        """,
        (company_id, staff_id, staff_name, file_id, _now_iso(), updated_by_tg_id),
    )


async def get_master_photo(company_id: str, staff_id: str) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT
            company_id,
            staff_id,
            staff_name,
            telegram_file_id,
            updated_at,
            updated_by_tg_id
        FROM master_photos
        WHERE company_id = ? AND staff_id = ?
        """,
        (company_id, staff_id),
    )
    return dict(row) if row else None


async def delete_master_photo(company_id: str, staff_id: str) -> None:
    await execute(
        "DELETE FROM master_photos WHERE company_id = ? AND staff_id = ?",
        (company_id, staff_id),
    )


async def list_master_photos(company_id: str) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT
            company_id,
            staff_id,
            staff_name,
            telegram_file_id,
            updated_at,
            updated_by_tg_id
        FROM master_photos
        WHERE company_id = ?
        ORDER BY staff_name COLLATE NOCASE
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]
