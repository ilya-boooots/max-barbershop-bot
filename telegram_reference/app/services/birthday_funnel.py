from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.sqlite import fetchall
from app.repositories.automation_settings import get_setting
from app.repositories.birthday_funnel_events import create_event, find_by_client_year, mark_status
from app.repositories.broadcasts import check_working_hours

from app.services.anti_spam import can_send_notification, record_delivery_decision

logger = logging.getLogger(__name__)

BIRTHDAY_BUTTON_CLAIM = "birthday_funnel:claim"
BIRTHDAY_BUTTON_BOOK = "birthday_funnel:book"
BIRTHDAY_MESSAGE_TEXT = (
    "Скоро ваш день рождения, поздравляем 🎉 😊\n\n"
    "Хотим сделать вам приятный подарок - покажите это сообщение администратору при оплате."
)
BIRTHDAY_WARNING = "У КЛИЕНТА ДЕНЬ РОЖДЕНИЕ - НУЖНО СДЕЛАТЬ СКИДКУ"


def build_birthday_booking_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✂️ Записаться", callback_data=f"{BIRTHDAY_BUTTON_BOOK}:{event_id}")]]
    )


@dataclass
class BirthdayScanSummary:
    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def _parse_birth_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


async def run_birthday_scan(bot: Bot, *, force: bool = False) -> BirthdayScanSummary:
    summary = BirthdayScanSummary()
    settings = await get_setting("birthday")
    if not settings.get("enabled") and not force:
        logger.info("birthday_scan_skipped_disabled")
        return summary
    ok, reason, *_ = await check_working_hours()
    if not ok and not force:
        logger.info("birthday_scan_skipped_hours reason=%s", reason)
        return summary
    days_before = int(settings.get("send_days_before", 7) or 7)
    message_text = BIRTHDAY_MESSAGE_TEXT

    rows = await fetchall("SELECT user_id, yclients_client_id, birth_date, notifications_enabled FROM users WHERE user_id IS NOT NULL")
    for row in rows:
        tg_id = int(row["user_id"])
        if int(row["notifications_enabled"] or 0) == 0:
            summary.skipped += 1
            continue
        birth_date = _parse_birth_date(row.get("birth_date"))
        if birth_date is None:
            summary.skipped += 1
            continue
        today = datetime.now(timezone.utc).date()
        target = date(today.year, birth_date.month, min(birth_date.day, 28) if birth_date.month == 2 and birth_date.day == 29 else birth_date.day)
        if (target - today).days != days_before:
            continue
        if await find_by_client_year(tg_id, today.year):
            summary.skipped += 1
            continue
        summary.candidates += 1
        event_id = await create_event(
            yclients_client_id=str(row.get("yclients_client_id") or "") or None,
            client_tg_id=tg_id,
            birth_date=birth_date.isoformat(),
            birthday_year=today.year,
            scheduled_send_at_utc=datetime.now(timezone.utc).isoformat(),
            status="pending",
            source="local_db",
        )
        allowed, decision = await can_send_notification(client_tg_id=tg_id, notification_type='birthday', category='marketing', funnel_type='birthday', source_event_id=str(event_id))
        if not allowed:
            await record_delivery_decision(client_tg_id=tg_id, notification_type='birthday', category='marketing', funnel_type='birthday', source_event_id=str(event_id), decision=decision)
            await mark_status(event_id, 'skipped', error_summary=decision)
            summary.skipped += 1
            continue
        try:
            kb = build_birthday_booking_keyboard(event_id)
            await bot.send_message(tg_id, message_text, reply_markup=kb)
            await mark_status(event_id, "sent", sent=True)
            logger.info(
                "birthday_notification_sent user_tg_id=%s birthday_event_id=%s is_test=%s source=%s",
                tg_id,
                event_id,
                False,
                "local_db",
            )
            await record_delivery_decision(client_tg_id=tg_id, notification_type='birthday', category='marketing', funnel_type='birthday', source_event_id=str(event_id), decision='allowed')
            summary.sent += 1
        except TelegramForbiddenError:
            await mark_status(event_id, "blocked", error_summary="forbidden")
            summary.errors += 1
        except TelegramBadRequest as exc:
            await mark_status(event_id, "failed", error_summary=str(exc)[:180])
            summary.errors += 1
    return summary


def apply_birthday_warning(
    base_comment: str,
    *,
    booking_source: str | None,
    birthday_discount_context: bool = False,
) -> str:
    if booking_source != "birthday_funnel" or not birthday_discount_context:
        return base_comment
    if BIRTHDAY_WARNING in base_comment:
        return base_comment
    return f"{base_comment}\n\n{BIRTHDAY_WARNING}"
