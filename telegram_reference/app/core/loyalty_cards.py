from __future__ import annotations

import secrets
import time
from typing import Any

from app.core.security import CARD_NUMBER_PATTERN
from app.db.sqlite import fetchone
from app.repositories.users import (
    card_number_exists,
    set_card_issue_timestamp,
    set_card_number,
)

CODE_TTL_SECONDS = 600

STATUS_MESSAGES: dict[str, str] = {
    "NOT_FOUND": "❓ Код не найден. Попросите клиента обновить карту и показать новый QR/код.",
    "EXPIRED": "⌛ Код истёк. Попросите клиента обновить карту лояльности и показать новый QR/код.",
    "USED": "✅ Этот код уже использован. Попросите клиента обновить карту лояльности и показать новый QR/код.",
}


def generate_card_number() -> str:
    return f"{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"


async def ensure_card_number(user_id: int, existing_card_number: str | None = None) -> str:
    if existing_card_number and CARD_NUMBER_PATTERN.fullmatch(existing_card_number):
        return existing_card_number
    for _ in range(50):
        candidate = generate_card_number()
        if await card_number_exists(candidate):
            continue
        await set_card_number(user_id, candidate)
        return candidate
    raise RuntimeError("Failed to generate unique card number")


async def issue_loyalty_code(user_id: int, existing_card_number: str | None = None) -> str:
    card_number = await ensure_card_number(user_id, existing_card_number)
    await set_card_issue_timestamp(user_id)
    return card_number


async def validate_loyalty_code(code: str) -> tuple[str, dict[str, Any] | None]:
    normalized = code.strip()
    row = await fetchone(
        """
        SELECT user_id,
               name,
               phone,
               loyalty_balance,
               card_number,
               card_created_at,
               card_used_at
        FROM users
        WHERE card_number = ?
        """,
        (normalized,),
    )
    if not row:
        return "NOT_FOUND", None
    user_row = dict(row)
    card_created_at = user_row.get("card_created_at")
    if card_created_at is None:
        return "NOT_FOUND", user_row
    now_ts = int(time.time())
    if now_ts > int(card_created_at) + CODE_TTL_SECONDS:
        return "EXPIRED", user_row
    card_used_at = user_row.get("card_used_at")
    if card_used_at is not None and int(card_used_at) >= int(card_created_at):
        return "USED", user_row
    return "OK", user_row


def loyalty_code_status_message(status: str) -> str:
    return STATUS_MESSAGES.get(
        status,
        "⌛ Код недействителен. Попросите клиента обновить карту лояльности и показать новый QR/код.",
    )
