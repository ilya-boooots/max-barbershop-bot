from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import list_user_bookings
from app.repositories.automation_settings import get_setting
from app.repositories.broadcasts import check_working_hours
from app.repositories.lost_client_events import create_event, has_recent_sent, mark_status
from app.db.sqlite import fetchall
from app.repositories.yclients_settings import get_yclients_settings
from app.services.anti_spam import can_send_notification, record_delivery_decision

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def build_lost_client_booking_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✂️ Записаться', callback_data=f'lost_clients:book:{event_id}')]
        ]
    )


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).astimezone(timezone.utc)
    except Exception:
        return None


def _record_items(payload):
    if isinstance(payload, dict) and isinstance(payload.get('data'), list):
        return payload['data']
    if isinstance(payload, list):
        return payload
    return []


def _is_completed(item: dict) -> bool:
    attendance = item.get('attendance')
    if attendance is None:
        attendance = item.get('visit_attendance')
    if attendance is not None:
        return str(attendance).strip() == '1'
    status = str(item.get('status') or '').strip().lower()
    return status in {'visit', 'done', 'paid', 'completed', 'show'}


async def run_lost_clients_scan(bot: Bot, *, force: bool = False) -> ScanSummary:
    summary = ScanSummary()
    settings = await get_setting('lost_clients')
    anti_spam = await get_setting('anti_spam')
    if not settings.get('enabled') and not force:
        logger.info('lost_clients_scan_skipped_disabled')
        return summary
    ok, reason, *_ = await check_working_hours()
    if not ok:
        logger.info('lost_clients_scan_skipped_hours reason=%s', reason)
        return summary
    ys = await get_yclients_settings()
    if not ys or not ys.company_id:
        return summary
    cooldown_days = max(1, int(anti_spam.get('min_interval_hours', 48) / 24))
    thresholds = sorted([int(x) for x in settings.get('threshold_days', [30, 60, 90])], reverse=True)
    client, _ = await build_yclients_client()
    try:
        rows = await fetchall("SELECT user_id, yclients_client_id, notifications_enabled FROM users WHERE yclients_client_id IS NOT NULL")
        for row in rows:
            tg_id = int(row['user_id'])
            yc_id = str(row['yclients_client_id'])
            if int(row['notifications_enabled'] or 0) == 0:
                summary.skipped += 1
                continue
            try:
                records_payload = await list_user_bookings(client, company_id=ys.company_id, client_id=yc_id, count=50)
                records = _record_items(records_payload)
                now = datetime.now(timezone.utc)
                future_exists = False
                last_visit = None
                last_visit_id = None
                for rec in records:
                    dt = _parse_dt(rec.get('datetime') or rec.get('date'))
                    if not dt:
                        continue
                    if dt > now:
                        future_exists = True
                    if _is_completed(rec) and (last_visit is None or dt > last_visit):
                        last_visit = dt
                        last_visit_id = str(rec.get('id') or rec.get('record_id') or '') or None
                if not last_visit:
                    continue
                if future_exists and settings.get('exclude_has_future_booking', True):
                    await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, threshold_days=0, segment_key='lost_skip_future', last_visit_datetime_utc=last_visit.isoformat(), last_visit_id=last_visit_id, has_future_booking=True, status='skipped_has_future_booking')
                    summary.skipped += 1
                    continue
                days = (now - last_visit).days
                threshold = next((t for t in thresholds if days >= t), None)
                if not threshold:
                    continue
                if await has_recent_sent(tg_id, threshold, cooldown_days):
                    summary.skipped += 1
                    continue
                summary.candidates += 1
                event_id = await create_event(yclients_client_id=yc_id, client_tg_id=tg_id, threshold_days=threshold, segment_key=f'lost_{threshold}', last_visit_datetime_utc=last_visit.isoformat(), last_visit_id=last_visit_id, has_future_booking=False, status='pending')
                txt = settings.get(f'text_{threshold}', '')
                kb = build_lost_client_booking_keyboard(event_id)
                allowed, decision = await can_send_notification(client_tg_id=tg_id, notification_type='lost_client', category='marketing', funnel_type='lost_clients', source_event_id=str(event_id))
                if not allowed:
                    await record_delivery_decision(client_tg_id=tg_id, notification_type='lost_client', category='marketing', funnel_type='lost_clients', source_event_id=str(event_id), decision=decision)
                    await mark_status(event_id, 'skipped', error_summary=decision)
                    summary.skipped += 1
                    continue
                try:
                    await bot.send_message(tg_id, txt, reply_markup=kb)
                    await mark_status(event_id, 'sent', sent=True)
                    await record_delivery_decision(client_tg_id=tg_id, notification_type='lost_client', category='marketing', funnel_type='lost_clients', source_event_id=str(event_id), decision='allowed')
                    summary.sent += 1
                except TelegramForbiddenError:
                    await mark_status(event_id, 'blocked', error_summary='forbidden')
                    summary.errors += 1
                except TelegramBadRequest as exc:
                    await mark_status(event_id, 'failed', error_summary=str(exc)[:180])
                    summary.errors += 1
            except YClientsError:
                summary.errors += 1
                continue
            except Exception as exc:
                logger.exception('lost_clients_client_processing_failed tg_id=%s err=%s', tg_id, exc)
                summary.errors += 1
        return summary
    finally:
        await client.close()
