from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.integrations.yclients.endpoints import list_user_bookings
from app.integrations.yclients.service import build_yclients_client
from app.repositories.automation_settings import get_setting
from app.repositories.cancellation_recovery_events import create_event, find_pending_to_send, set_status
from app.repositories.users import find_user_by_phone

from app.services.anti_spam import can_send_notification, record_delivery_decision

logger = logging.getLogger(__name__)


def _s(v: object) -> str:
    return str(v or "").strip()


async def create_cancellation_event_from_row(*, row: dict, source: str = "bot_cancel", is_test: bool = False, force_tg_id: int | None = None) -> int | None:
    settings = await get_setting("cancellation_return")
    should_track_for_segments = source == "my_bookings_cancel"
    if not settings.get("enabled") and not is_test and not should_track_for_segments:
        logger.info("cancellation_recovery_skipped_disabled source=%s record_id=%s", source, _s(row.get("id")))
        return None
    delay_hours = int(settings.get("delay_hours") or 2)
    now = datetime.now(timezone.utc)
    user = None
    if force_tg_id is not None:
        user = {"user_id": force_tg_id}
    else:
        phone = _s((row.get("client") or {}).get("phone") or row.get("phone"))
        user = await find_user_by_phone(phone) if phone else None
    event_id = await create_event(
        {
            "yclients_record_id": _s(row.get("id") or row.get("record_id") or row.get("booking_id")),
            "yclients_client_id": _s((row.get("client") or {}).get("id") or row.get("client_id")),
            "client_tg_id": int(user["user_id"]) if user else None,
            "staff_id": _s((row.get("staff") or {}).get("id") or row.get("staff_id")),
            "staff_name": _s((row.get("staff") or {}).get("name") or row.get("staff_name")),
            "service_id": _s((row.get("services") or [{}])[0].get("id") if isinstance(row.get("services"), list) and row.get("services") else row.get("service_id")),
            "service_name": _s((row.get("services") or [{}])[0].get("title") if isinstance(row.get("services"), list) and row.get("services") else row.get("service_name")),
            "cancelled_booking_datetime_utc": _s(row.get("datetime") or row.get("date")) or now.isoformat(),
            "cancellation_detected_at_utc": now.isoformat(),
            "scheduled_send_at_utc": (now + timedelta(minutes=2 if is_test else 60 * delay_hours)).isoformat(),
            "source": source,
            "is_test": is_test,
        }
    )
    logger.info("cancellation_recovery_event_created record_id=%s event_id=%s tg_id=%s source=%s is_test=%s", _s(row.get("id")), event_id, user.get("user_id") if user else None, source, is_test)
    return event_id


async def process_pending_events(bot: Bot, company_id: str) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    events = await find_pending_to_send(now_iso)
    settings = await get_setting("cancellation_return")
    sent = 0
    for event in events:
        event_id = int(event["id"])
        tg_id = int(event.get("client_tg_id") or 0)
        if not tg_id:
            await set_status(event_id, "failed", error_summary="no_telegram_mapping")
            continue
        if not settings.get("enabled") and not int(event.get("is_test") or 0):
            await set_status(event_id, "failed", error_summary="disabled")
            continue
        has_future = await _has_future_booking(company_id=company_id, yclients_client_id=_s(event.get("yclients_client_id")))
        if has_future is None:
            await set_status(event_id, "failed", error_summary="future_booking_check_failed")
            continue
        if has_future:
            await set_status(event_id, "skipped_has_new_booking")
            continue
        try:
            allowed, decision = await can_send_notification(client_tg_id=tg_id, notification_type='cancellation_recovery', category='marketing', funnel_type='cancellation_recovery', source_event_id=str(event_id), is_test=bool(int(event.get('is_test') or 0)))
            if not allowed:
                await record_delivery_decision(client_tg_id=tg_id, notification_type='cancellation_recovery', category='marketing', funnel_type='cancellation_recovery', source_event_id=str(event_id), decision=decision, is_test=bool(int(event.get('is_test') or 0)))
                await set_status(event_id, 'skipped', error_summary=decision)
                continue
            await bot.send_message(tg_id, settings.get("message_text") or "Видим, что вы отменили запись 😔\n\nМожем подобрать другое удобное время.", reply_markup=_recovery_kb(event_id))
            await set_status(event_id, "sent", sent_at_utc=now_iso)
            await record_delivery_decision(client_tg_id=tg_id, notification_type='cancellation_recovery', category='marketing', funnel_type='cancellation_recovery', source_event_id=str(event_id), decision='allowed', is_test=bool(int(event.get('is_test') or 0)))
            sent += 1
        except TelegramForbiddenError:
            await set_status(event_id, "blocked", error_summary="bot_blocked")
        except Exception as exc:
            await set_status(event_id, "failed", error_summary=str(exc)[:200])
    return sent


async def _has_future_booking(*, company_id: str, yclients_client_id: str) -> bool | None:
    if not yclients_client_id:
        return False
    try:
        client, _ = await build_yclients_client()
        try:
            now = datetime.now(timezone.utc)
            payload = await list_user_bookings(client, company_id=company_id, start_date=now.date().isoformat(), end_date=(now + timedelta(days=120)).date().isoformat(), page=1, count=200)
        finally:
            await client.close()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return False
        for row in rows:
            cid = _s((row.get("client") or {}).get("id") or row.get("client_id"))
            status = _s(row.get("status") or row.get("record_status") or row.get("state")).lower()
            dt_raw = _s(row.get("datetime") or row.get("date"))
            if cid != yclients_client_id or not dt_raw:
                continue
            if status in {"cancelled", "canceled", "delete", "deleted", "отменена"}:
                continue
            dt = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
            if dt > now:
                return True
        return False
    except Exception:
        logger.exception("cancellation_recovery_future_booking_check_failed client_id=%s", yclients_client_id)
        return None


def _recovery_kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✂️ Подобрать новое время", callback_data=f"cancel_recovery:rebook:{event_id}")],
        [InlineKeyboardButton(text="📅 Выбрать другую дату", callback_data=f"cancel_recovery:date:{event_id}")],
        [InlineKeyboardButton(text="Позже", callback_data=f"cancel_recovery:later:{event_id}")],
    ])
