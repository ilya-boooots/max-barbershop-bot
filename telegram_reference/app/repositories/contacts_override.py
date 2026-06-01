from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


@dataclass(frozen=True)
class ContactsOverride:
    company_id: str
    address: str | None
    phone: str | None
    schedule: str | None
    updated_at: str
    updated_by_tg_id: int | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


async def get_contacts_override(company_id: str) -> ContactsOverride | None:
    row = await fetchone(
        """
        SELECT company_id, address, phone, schedule, updated_at, updated_by_tg_id
        FROM contacts_override
        WHERE company_id = ?
        """,
        (company_id,),
    )
    if row is None:
        return None
    return ContactsOverride(
        company_id=row["company_id"],
        address=row["address"],
        phone=row["phone"],
        schedule=row["schedule"],
        updated_at=row["updated_at"],
        updated_by_tg_id=row["updated_by_tg_id"],
    )


async def upsert_contacts_override(
    *,
    company_id: str,
    address: str | None = None,
    phone: str | None = None,
    schedule: str | None = None,
    updated_by_tg_id: int,
) -> None:
    current = await get_contacts_override(company_id)
    next_address = _clean(address) if address is not None else (current.address if current else None)
    next_phone = _clean(phone) if phone is not None else (current.phone if current else None)
    next_schedule = _clean(schedule) if schedule is not None else (current.schedule if current else None)

    await execute(
        """
        INSERT INTO contacts_override (company_id, address, phone, schedule, updated_at, updated_by_tg_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id) DO UPDATE SET
            address=excluded.address,
            phone=excluded.phone,
            schedule=excluded.schedule,
            updated_at=excluded.updated_at,
            updated_by_tg_id=excluded.updated_by_tg_id
        """,
        (
            company_id,
            next_address,
            next_phone,
            next_schedule,
            datetime.now(timezone.utc).isoformat(),
            updated_by_tg_id,
        ),
    )


async def clear_contacts_override(company_id: str) -> None:
    await execute("DELETE FROM contacts_override WHERE company_id = ?", (company_id,))
