from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


@dataclass(frozen=True)
class SupportSettings:
    company_id: str
    support_description: str
    support_username: str
    updated_at: str
    updated_by_tg_id: int | None


DEFAULT_SUPPORT_DESCRIPTION = "Если у вас возникли вопросы, напишите нам — с удовольствием поможем! 🙂"
DEFAULT_SUPPORT_USERNAME = "flowbots1sup"


def normalize_support_username(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None

    if value.startswith("https://"):
        value = value[len("https://"):]
    elif value.startswith("http://"):
        value = value[len("http://"):]

    lowered = value.lower()
    if lowered.startswith("t.me/"):
        value = value[5:]

    value = value.lstrip("@").strip()
    if not value:
        return None

    if any(ch.isspace() for ch in value):
        return None

    if "/" in value or "?" in value or "#" in value:
        return None

    if not (5 <= len(value) <= 32):
        return None

    if not all(ch.isalnum() or ch == "_" for ch in value):
        return None

    return value


async def get_support_settings(company_id: str) -> SupportSettings | None:
    row = await fetchone(
        """
        SELECT company_id, support_description, support_username, updated_at, updated_by_tg_id
        FROM support_settings
        WHERE company_id = ?
        """,
        (company_id,),
    )
    if row is None:
        return None

    description = (row["support_description"] or "").strip() or DEFAULT_SUPPORT_DESCRIPTION
    username = normalize_support_username(row["support_username"] or "") or DEFAULT_SUPPORT_USERNAME
    return SupportSettings(
        company_id=row["company_id"],
        support_description=description,
        support_username=username,
        updated_at=row["updated_at"],
        updated_by_tg_id=row["updated_by_tg_id"],
    )


async def upsert_support_settings(
    *,
    company_id: str,
    support_description: str | None = None,
    support_username: str | None = None,
    updated_by_tg_id: int,
) -> None:
    current = await get_support_settings(company_id)
    next_description = (support_description or "").strip() if support_description is not None else (
        current.support_description if current else DEFAULT_SUPPORT_DESCRIPTION
    )
    if not next_description:
        next_description = DEFAULT_SUPPORT_DESCRIPTION

    if support_username is not None:
        normalized_username = normalize_support_username(support_username)
        if not normalized_username:
            raise ValueError("Invalid support username")
        next_username = normalized_username
    else:
        next_username = current.support_username if current else DEFAULT_SUPPORT_USERNAME

    await execute(
        """
        INSERT INTO support_settings (
            company_id,
            support_description,
            support_username,
            updated_at,
            updated_by_tg_id
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(company_id) DO UPDATE SET
            support_description=excluded.support_description,
            support_username=excluded.support_username,
            updated_at=excluded.updated_at,
            updated_by_tg_id=excluded.updated_by_tg_id
        """,
        (
            company_id,
            next_description,
            next_username,
            datetime.now(timezone.utc).isoformat(),
            updated_by_tg_id,
        ),
    )


async def reset_support_settings(company_id: str, *, updated_by_tg_id: int) -> None:
    await upsert_support_settings(
        company_id=company_id,
        support_description=DEFAULT_SUPPORT_DESCRIPTION,
        support_username=DEFAULT_SUPPORT_USERNAME,
        updated_by_tg_id=updated_by_tg_id,
    )
