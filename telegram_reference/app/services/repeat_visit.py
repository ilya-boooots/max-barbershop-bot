from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.sqlite import fetchall
from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import list_user_bookings
from app.repositories.automation_settings import get_setting
from app.repositories.broadcasts import check_working_hours
from app.repositories.repeat_visit_events import create_event, has_event_for_visit, has_recent_sent, mark_status
from app.repositories.yclients_settings import get_yclients_settings

from app.services.anti_spam import can_send_notification, record_delivery_decision

logger = logging.getLogger(__name__)
BUTTON_CB_PREFIX = "repeat_visit:book:"
FALLBACK_TEXT = "Пора обновить стрижку? 😊\n\nОбычно к этому времени форма уже начинает теряться."


@dataclass
class RepeatVisitSummary:
    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def build_repeat_visit_booking_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✂️ Записаться", callback_data=f"{BUTTON_CB_PREFIX}{event_id}")]
        ]
    )


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _record_items(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def _is_completed(item: dict) -> bool:
    attendance = item.get("attendance")
    if attendance is None:
        attendance = item.get("visit_attendance")
    if attendance is not None:
        return str(attendance).strip() == "1"
    status = str(item.get("status") or "").strip().lower()
    return status in {"visit", "done", "paid", "completed", "show"}


def _extract_main_service(rec: dict) -> tuple[str | None, str | None]:
    services = rec.get("services")
    if isinstance(services, list) and services:
        first = services[0] if isinstance(services[0], dict) else {}
        return str(first.get("id") or "") or None, str(first.get("title") or first.get("name") or "") or None
    return None, str(rec.get("service_name") or rec.get("service") or "") or None


def select_repeat_visit_text(
    settings: dict,
    *,
    event_id: int | None = None,
    user_tg_id: int | None = None,
) -> tuple[int, str]:
    raw = settings.get("templates")
    active: list[tuple[int, str]] = []
    if isinstance(raw, list):
        active = [(idx, str(text).strip()) for idx, text in enumerate(raw, start=1) if str(text or "").strip()]

    logger.info(
        "repeat_visit_text_selection_started available_text_count=%s event_id=%s user_tg_id=%s",
        len(active),
        event_id,
        user_tg_id,
    )
    if not active:
        logger.info(
            "repeat_visit_text_default_used available_text_count=%s event_id=%s user_tg_id=%s",
            0,
            event_id,
            user_tg_id,
        )
        return 0, FALLBACK_TEXT

    selected_idx, selected_text = random.choice(active)
    logger.info(
        "repeat_visit_text_selected available_text_count=%s selected_text_index=%s event_id=%s user_tg_id=%s",
        len(active),
        selected_idx,
        event_id,
        user_tg_id,
    )
    return selected_idx, selected_text


async def run_repeat_visit_scan(bot: Bot, *, force: bool = False) -> RepeatVisitSummary:
    summary = RepeatVisitSummary()
    settings = await get_setting("repeat_visit")
    anti_spam = await get_setting("anti_spam")
    if not settings.get("enabled") and not force:
        logger.info("repeat_visit_scan_skipped_disabled")
        return summary
    ok, reason, *_ = await check_working_hours()
    if not ok and settings.get("respect_working_hours", True):
        logger.info("repeat_visit_scan_skipped_hours reason=%s", reason)
        return summary
    ys = await get_yclients_settings()
    if not ys or not ys.company_id:
        return summary
    delay_days = int(settings.get("delay_days", 30))
    cooldown = int(anti_spam.get("min_interval_hours", 48))
    client, _ = await build_yclients_client()
    try:
        users = await fetchall("SELECT user_id, yclients_client_id, notifications_enabled FROM users WHERE yclients_client_id IS NOT NULL")
        now = datetime.now(timezone.utc)
        for row in users:
            tg_id = int(row["user_id"])
            yc_id = str(row["yclients_client_id"])
            if int(row["notifications_enabled"] or 0) == 0:
                summary.skipped += 1
                continue
            try:
                records = _record_items(await list_user_bookings(client, company_id=ys.company_id, client_id=yc_id, count=50))
                last_done = None
                future_exists = False
                for rec in records:
                    dt = _parse_dt(rec.get("datetime") or rec.get("date"))
                    if not dt:
                        continue
                    if dt > now and "cancel" not in str(rec.get("status") or "").lower():
                        future_exists = True
                    if _is_completed(rec) and (last_done is None or dt > (last_done[1] or now - timedelta(days=5000))):
                        last_done = (rec, dt)
                if not last_done:
                    continue
                rec, visit_dt = last_done
                service_id, service_name = _extract_main_service(rec)
                if settings.get("exclude_has_future_booking", True) and future_exists:
                    await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, yclients_visit_id=str(rec.get("id") or "") or None, yclients_service_id=service_id, service_name=service_name, last_visit_datetime_utc=visit_dt.isoformat(), delay_days=delay_days, status="skipped_has_future_booking")
                    summary.skipped += 1
                    continue
                if (now - visit_dt).days < delay_days:
                    continue
                visit_id = str(rec.get("id") or "") or None
                if await has_event_for_visit(tg_id, visit_id, service_id):
                    await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, yclients_visit_id=visit_id, yclients_service_id=service_id, service_name=service_name, last_visit_datetime_utc=visit_dt.isoformat(), delay_days=delay_days, status="skipped_duplicate")
                    summary.skipped += 1
                    continue
                if settings.get("respect_anti_spam", True) and await has_recent_sent(tg_id, cooldown):
                    await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, yclients_visit_id=visit_id, yclients_service_id=service_id, service_name=service_name, last_visit_datetime_utc=visit_dt.isoformat(), delay_days=delay_days, status="skipped_antispam")
                    summary.skipped += 1
                    continue
                template_idx, template_text = select_repeat_visit_text(settings, user_tg_id=tg_id)
                summary.candidates += 1
                event_id = await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, yclients_visit_id=visit_id, yclients_service_id=service_id, service_name=service_name, last_visit_datetime_utc=visit_dt.isoformat(), delay_days=delay_days, status="pending", selected_template_index=template_idx, selected_template_text=template_text)
                kb = build_repeat_visit_booking_keyboard(event_id)
                allowed, decision = await can_send_notification(client_tg_id=tg_id, notification_type='repeat_visit', category='marketing', funnel_type='repeat_visit', source_event_id=str(event_id))
                if not allowed:
                    await record_delivery_decision(client_tg_id=tg_id, notification_type='repeat_visit', category='marketing', funnel_type='repeat_visit', source_event_id=str(event_id), decision=decision)
                    await mark_status(event_id, 'skipped', error_summary=decision)
                    summary.skipped += 1
                    continue
                try:
                    await bot.send_message(tg_id, template_text, reply_markup=kb)
                    await mark_status(event_id, "sent", sent=True)
                    await record_delivery_decision(client_tg_id=tg_id, notification_type='repeat_visit', category='marketing', funnel_type='repeat_visit', source_event_id=str(event_id), decision='allowed')
                    summary.sent += 1
                except TelegramForbiddenError:
                    await mark_status(event_id, "blocked", error_summary="forbidden")
                    summary.errors += 1
                except TelegramBadRequest as exc:
                    await mark_status(event_id, "failed", error_summary=str(exc)[:180])
                    summary.errors += 1
            except Exception:
                logger.exception("repeat_visit_client_processing_failed tg_id=%s", tg_id)
                summary.errors += 1
        return summary
    finally:
        await client.close()
