from __future__ import annotations

import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from app.core.config import get_settings
from app.integrations.yclients.endpoints import get_loyalty_info, list_client_visits
from app.integrations.yclients.service import build_yclients_client
from app.repositories.loyalty_operations import create_operation, operation_exists_by_source_event
from app.repositories.referrals import (
    create_referral_attribution,
    get_referral_attribution,
    get_referral_code,
    get_user_by_referral_code,
    list_invited_by_user,
    mark_referral_rewarded,
    save_referral_code,
)
from app.repositories.users import get_user
from app.utils.datetime import resolve_branch_timezone

REFERRAL_INVITER_BONUS = 200
REFERRAL_INVITED_WELCOME_BONUS = 100
REFERRAL_INVITED_WELCOME_ENABLED = True

_COMPLETED_STATUSES = {"done", "completed", "visit", "paid"}


@dataclass(frozen=True)
class QualifyingVisit:
    record_id: str
    happened_at: datetime


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "items", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _extract_status(item: dict[str, Any]) -> str:
    return _s(item.get("status") or item.get("record_status") or item.get("state")).lower()


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = _s(value).replace("₽", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _extract_paid_amount(item: dict[str, Any]) -> float:
    for key in ("paid_amount", "paid_sum", "amount_paid", "sum_paid", "total_paid", "payment_total", "paid"):
        val = _to_float(item.get(key))
        if val > 0:
            return val
    payments = item.get("payments")
    if isinstance(payments, list):
        total = 0.0
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            total += max(0.0, _to_float(payment.get("amount") or payment.get("sum") or payment.get("paid_amount")))
        if total > 0:
            return total
    return 0.0


def _is_paid(item: dict[str, Any]) -> bool:
    if any(bool(item.get(key)) for key in ("paid", "is_paid", "paid_full", "fully_paid")):
        return True
    payment_state = _s(item.get("payment_status") or item.get("payment_state") or item.get("paid_status")).lower()
    if payment_state in {"paid", "fully_paid", "closed"}:
        return True
    return _extract_status(item) in _COMPLETED_STATUSES and _extract_paid_amount(item) > 0


def _parse_dt(item: dict[str, Any]) -> datetime | None:
    for key in ("datetime", "date", "start"):
        raw = _s(item.get(key))
        if not raw:
            continue
        normalized = raw.replace(" ", "T").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _extract_record_id(item: dict[str, Any]) -> str:
    return _s(item.get("id") or item.get("record_id") or item.get("booking_id") or item.get("visit_id"))


def _generate_referral_code(user_id: int) -> str:
    rand = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"U{user_id % 10000:04d}-{rand}"


async def ensure_referral_code(user_id: int) -> str:
    existing = await get_referral_code(user_id)
    if existing:
        return existing
    code = _generate_referral_code(user_id)
    await save_referral_code(user_id, code)
    return code


async def get_referral_link(user_id: int, bot_username: str | None) -> str:
    code = await ensure_referral_code(user_id)
    username = (bot_username or "").strip("@") or "your_bot"
    return f"https://t.me/{username}?start=ref_{code}"


async def apply_start_referral(*, invited_tg_id: int, start_param: str | None) -> str:
    if not start_param or not start_param.startswith("ref_"):
        return "ignored"
    referral_code = start_param.removeprefix("ref_").strip().upper()
    inviter_tg_id = await get_user_by_referral_code(referral_code)
    if inviter_tg_id is None:
        return "invalid_code"
    if inviter_tg_id == invited_tg_id:
        return "self_referral"
    created = await create_referral_attribution(
        invited_tg_id=invited_tg_id,
        inviter_tg_id=inviter_tg_id,
        referral_code=referral_code,
    )
    return "attributed" if created else "already_attributed"


async def _find_first_qualifying_visit(*, company_id: str, client_id: str) -> tuple[QualifyingVisit | None, bool]:
    client, _ = await build_yclients_client()
    try:
        payload = await list_client_visits(client, company_id=company_id, client_id=client_id, page=1, count=100)
        rows = _extract_rows(payload)
        qualified: list[QualifyingVisit] = []
        for row in rows:
            if _extract_status(row) not in _COMPLETED_STATUSES:
                continue
            if not _is_paid(row):
                continue
            dt = _parse_dt(row)
            record_id = _extract_record_id(row)
            if not dt or not record_id:
                continue
            qualified.append(QualifyingVisit(record_id=record_id, happened_at=dt))
        qualified.sort(key=lambda x: x.happened_at)
        if not qualified:
            return None, False
        return qualified[0], len(qualified) > 1
    finally:
        await client.close()


async def sync_referral_reward_if_eligible(*, invited_tg_id: int, bot: Bot | None = None) -> str:
    attribution = await get_referral_attribution(invited_tg_id)
    if not attribution:
        return "no_attribution"
    if attribution.get("status") in {"rewarded", "blocked_existing_paid"}:
        return str(attribution.get("status"))

    invited_user = await get_user(invited_tg_id)
    if not invited_user:
        return "invited_not_found"
    client_id = _s(invited_user.get("yclients_client_id"))
    if not client_id:
        return "awaiting_yclients_mapping"

    settings = get_settings()
    company_id = _s(settings.yclients_company_id)
    if not company_id:
        return "awaiting_company_id"

    first_qualifying_visit, has_more = await _find_first_qualifying_visit(company_id=company_id, client_id=client_id)
    if not first_qualifying_visit:
        return "awaiting_first_paid_visit"

    # Do not reject solely because the visit timestamp is earlier than the
    # local attribution timestamp. In production the attribution can be saved
    # only when the client returns to the bot after an already completed
    # YClients visit, and clock/timezone/import delays made valid referrals
    # permanently blocked as ``blocked_existing_paid``. Idempotency below still
    # guarantees that the same YClients visit grants the referral reward once.

    source_event_id = f"referral:{invited_tg_id}:{first_qualifying_visit.record_id}"
    if await operation_exists_by_source_event(source="referral", source_event_id=source_event_id):
        return "already_rewarded"

    inviter_tg_id = int(attribution["inviter_tg_id"])
    branch_tz = await resolve_branch_timezone()
    await create_operation(
        user_tg_id=inviter_tg_id,
        operation_type="referral_bonus_inviter",
        points_delta=REFERRAL_INVITER_BONUS,
        reason="Бонус за приглашённого друга после первого оплаченного визита",
        source="referral",
        source_event_id=source_event_id,
        branch_timezone=branch_tz,
    )

    if REFERRAL_INVITED_WELCOME_ENABLED:
        await create_operation(
            user_tg_id=invited_tg_id,
            operation_type="referral_bonus_invited",
            points_delta=REFERRAL_INVITED_WELCOME_BONUS,
            reason="Приветственный бонус за первый визит по приглашению",
            source="referral",
            source_event_id=f"welcome:{source_event_id}",
            branch_timezone=branch_tz,
        )

    await mark_referral_rewarded(
        invited_tg_id=invited_tg_id,
        yclients_client_id=client_id,
        qualifying_record_id=first_qualifying_visit.record_id,
        qualifying_visit_at_utc=first_qualifying_visit.happened_at.isoformat(),
    )

    if bot:
        try:
            await bot.send_message(inviter_tg_id, f"🎉 Ваш друг завершил первый оплаченный визит! Начислено +{REFERRAL_INVITER_BONUS} баллов.")
            if REFERRAL_INVITED_WELCOME_ENABLED:
                await bot.send_message(invited_tg_id, f"🎁 Добро пожаловать! Вам начислено +{REFERRAL_INVITED_WELCOME_BONUS} баллов.")
        except Exception:
            pass

    return "rewarded_many" if has_more else "rewarded"


async def get_referral_stats(user_tg_id: int) -> tuple[int, int]:
    rows = await list_invited_by_user(user_tg_id)
    invited_total = len(rows)
    rewarded_total = len([r for r in rows if r.get("status") == "rewarded"])
    return invited_total, rewarded_total * REFERRAL_INVITER_BONUS


async def fetch_yclients_loyalty_balance(*, company_id: str, client_id: str) -> int | None:
    client, _ = await build_yclients_client()
    try:
        payload = await get_loyalty_info(client, company_id=company_id, client_id=client_id)
    except Exception:
        await client.close()
        return None
    await client.close()
    rows = _extract_rows(payload)
    candidate = rows[0] if rows else (payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload if isinstance(payload, dict) else None)
    if not isinstance(candidate, dict):
        return None
    for key in ("points", "balance", "bonus", "bonus_balance"):
        value = candidate.get(key)
        if value is None:
            continue
        try:
            return int(float(str(value).replace(",", ".")))
        except Exception:
            continue
    return None
