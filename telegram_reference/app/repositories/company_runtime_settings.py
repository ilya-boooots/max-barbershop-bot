from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


@dataclass(frozen=True)
class CompanyRuntimeSettings:
    company_id: str
    city: str | None
    timezone: str | None
    source: str | None
    updated_at: str


async def get_company_runtime_settings(company_id: str) -> CompanyRuntimeSettings | None:
    row = await fetchone(
        """
        SELECT company_id, city, timezone, source, updated_at
        FROM company_runtime_settings
        WHERE company_id = ?
        """,
        (company_id,),
    )
    if not row:
        return None
    return CompanyRuntimeSettings(
        company_id=str(row["company_id"]),
        city=row["city"],
        timezone=row["timezone"],
        source=row["source"],
        updated_at=row["updated_at"],
    )


async def upsert_company_runtime_settings(
    *,
    company_id: str,
    city: str | None,
    timezone_name: str | None,
    source: str,
) -> None:
    await execute(
        """
        INSERT INTO company_runtime_settings (company_id, city, timezone, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(company_id) DO UPDATE SET
            city=excluded.city,
            timezone=excluded.timezone,
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        (company_id, city, timezone_name, source, datetime.now(timezone.utc).isoformat()),
    )
