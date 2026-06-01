from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import get_settings
from app.core.safe_telegram import safe_send
from app.core.auth import get_dev_user_id
from app.db.bookings_repo import (
    get_booking_by_id,
    get_booking_stats_for_user,
    get_pending_for_reminders,
    mark_hostess_notified,
    touch_reminder,
)
from app.repositories.diagnostics import log_bot_event, log_user_event
from app.repositories.users import get_hostess_ids, get_user

GMT_PLUS_4 = timezone(timedelta(hours=4))
LAST_BOOKING_STATUS_MAP = {
    "pending": "ожидает",
    "approved": "подтверждена",
    "cancelled": "отменена",
}


def _hostess_booking_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"hostess:approve:{booking_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"hostess:cancel:{booking_id}"),
            ]
        ]
    )


def _minutes_waiting(created_ts_utc: str | None) -> int:
    if not created_ts_utc:
        return 0
    try:
        created = datetime.fromisoformat(created_ts_utc)
    except ValueError:
        return 0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    return max(0, int(delta.total_seconds() // 60))


def _booking_phone(booking: dict) -> str:
    phone = str(booking.get("phone") or "").strip()
    return phone


async def _resolve_booking_phone(booking: dict) -> str:
    booking_phone = _booking_phone(booking)
    if booking_phone:
        return booking_phone

    user_id = booking.get("user_id")
    if user_id is None:
        return "не указан"

    user = await get_user(int(user_id))
    user_phone = str((user or {}).get("phone") or "").strip()
    if user_phone:
        return user_phone
    return "не указан"

def _format_last_booking(last_booking: dict | None) -> str:
    if not last_booking:
        return "—"

    raw_ts = last_booking.get("created_ts_utc") or last_booking.get("created_ts_local")
    if not raw_ts:
        return "—"

    try:
        dt = datetime.fromisoformat(raw_ts)
    except ValueError:
        return "—"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_local = dt.astimezone(GMT_PLUS_4)
    status = LAST_BOOKING_STATUS_MAP.get(str(last_booking.get("status") or ""), "—")
    return f"{dt_local.strftime('%d.%m.%Y %H:%M')} — {status}"


async def _get_receivers() -> list[int]:
    hostess_ids = await get_hostess_ids()
    if hostess_ids:
        return hostess_ids
    return [get_dev_user_id()]


async def notify_hostesses_new_booking(bot: Bot, booking_id: int) -> None:
    booking = await get_booking_by_id(booking_id)
    if not booking:
        return
    stats = await get_booking_stats_for_user(int(booking["user_id"]))
    text = (
        "‼️‼️‼️ Новая заявка на бронирование ‼️‼️‼️\n\n"
        f"Гость: {booking.get('name') or '—'}\n"
        f"Телефон: {await _resolve_booking_phone(booking)}\n"
        f"Дата: {booking.get('date') or '—'}\n"
        f"Время: {booking.get('time') or '—'}\n"
        f"Кол-во гостей: {booking.get('guests') or '—'}\n"
        f"Комментарий к броне: {booking.get('comment') or '—'}\n\n"
        f"Броней за всё время: {stats['total_count']}\n"
        f"Подтверждено: {stats['approved_count']}\n"
        f"Отменено: {stats['cancelled_count']}\n"
        f"Последняя бронь: {_format_last_booking(stats.get('last_booking'))}\n\n"
        "Пожалуйста, позвоните в течение 5 минут."
    )
    receivers = await _get_receivers()
    keyboard = _hostess_booking_kb(booking_id)
    for tg_id in receivers:
        try:
            result = await safe_send(bot, "send_message", chat_id=tg_id, text=text, reply_markup=keyboard)
            if result.ok or result.skipped:
                continue
            raise RuntimeError(result.error or "send_failed")
        except Exception as exc:
            await log_bot_event(
                level="ERROR",
                source="booking_notify",
                message="Не удалось отправить уведомление хостес",
                details={"booking_id": booking_id, "tg_id": tg_id, "error": str(exc)},
            )
    staff_group_id = get_settings().staff_group_id
    if staff_group_id:
        try:
            await safe_send(bot, "send_message", chat_id=staff_group_id, text=text, reply_markup=keyboard)
        except Exception:
            pass
    await mark_hostess_notified(booking_id)
    await log_bot_event(
        level="INFO",
        source="booking_notify",
        message="Уведомление о новой брони отправлено",
        details={"booking_id": booking_id, "receivers": receivers},
    )


async def booking_reminder_worker(stop_event: asyncio.Event, bot: Bot) -> None:
    while not stop_event.is_set():
        try:
            pending = await get_pending_for_reminders(datetime.now(timezone.utc))
            receivers = await _get_receivers()
            for booking in pending:
                keyboard = _hostess_booking_kb(int(booking["id"]))
                waited = _minutes_waiting(booking.get("created_ts_utc"))
                text = (
                    f"⏰ Напоминание: заявка на бронь ждёт решения уже {waited} минут.\n"
                    f"Гость: {booking.get('name') or '—'}\n"
                    f"Телефон: {await _resolve_booking_phone(booking)}\n"
                    f"Дата/время: {booking.get('date') or '—'} {booking.get('time') or '—'}\n"
                    "Нажмите ✅ Подтвердить или ❌ Отменить"
                )
                for tg_id in receivers:
                    try:
                        await safe_send(bot, "send_message", chat_id=tg_id, text=text, reply_markup=keyboard)
                    except Exception:
                        continue
                staff_group_id = get_settings().staff_group_id
                if staff_group_id:
                    try:
                        await safe_send(bot, "send_message", chat_id=staff_group_id, text=text, reply_markup=keyboard)
                    except Exception:
                        pass
                await touch_reminder(int(booking["id"]))
                await log_user_event(
                    user_id=int(booking["user_id"]),
                    username=None,
                    phone=booking.get("phone"),
                    event_type="booking",
                    event_name="hostess_reminder_sent",
                    payload={"booking_id": int(booking["id"]), "waited_minutes": waited},
                )
        except Exception as exc:
            await log_bot_event(
                level="ERROR",
                source="booking_reminder_worker",
                message="Ошибка воркера напоминаний",
                details={"error": str(exc)},
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            continue
