from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


@dataclass(frozen=True)
class YClientsSettings:
    company_id: str | None
    partner_token: str | None
    user_token: str | None
    base_url: str | None
    updated_at: str | None
    updated_by_tg_id: int | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


async def get_yclients_settings() -> YClientsSettings | None:
    row = await fetchone(
        """
        SELECT company_id, partner_token, user_token, base_url, updated_at, updated_by_tg_id
        FROM yclients_settings
        WHERE id = 1
        """
    )
    if row is None:
        return None
    return YClientsSettings(
        company_id=row["company_id"],
        partner_token=row["partner_token"],
        user_token=row["user_token"],
        base_url=row["base_url"],
        updated_at=row["updated_at"],
        updated_by_tg_id=row["updated_by_tg_id"],
    )


async def upsert_yclients_settings(
    *,
    company_id: str,
    partner_token: str,
    user_token: str | None,
    base_url: str | None,
    updated_by_tg_id: int,
) -> None:
    await execute(
        """
        INSERT INTO yclients_settings (id, company_id, partner_token, user_token, base_url, updated_at, updated_by_tg_id)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            company_id=excluded.company_id,
            partner_token=excluded.partner_token,
            user_token=excluded.user_token,
            base_url=excluded.base_url,
            updated_at=excluded.updated_at,
            updated_by_tg_id=excluded.updated_by_tg_id
        """,
        (
            _clean(company_id),
            _clean(partner_token),
            _clean(user_token),
            _clean(base_url),
            datetime.now(timezone.utc).isoformat(),
            updated_by_tg_id,
        ),
    )


async def reset_yclients_settings(*, updated_by_tg_id: int) -> None:
    await execute(
        """
        INSERT INTO yclients_settings (id, company_id, partner_token, user_token, base_url, updated_at, updated_by_tg_id)
        VALUES (1, NULL, NULL, NULL, NULL, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            company_id=NULL,
            partner_token=NULL,
            user_token=NULL,
            base_url=NULL,
            updated_at=excluded.updated_at,
            updated_by_tg_id=excluded.updated_by_tg_id
        """,
        (datetime.now(timezone.utc).isoformat(), updated_by_tg_id),
    )
