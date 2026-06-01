from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.safe_telegram import safe_send
from app.integrations.yclients import build_yclients_client
from app.integrations.yclients.endpoints import get_booking_details, get_company, list_bookings_by_date_range
from app.repositories.booking_reminder_events import create_event, get_due_events, get_event, mark_status
from app.repositories.users import find_user_by_phone
from app.services.notification_delivery import is_white_notification
from app.services.company_time import DEFAULT_TIMEZONE, resolve_company_timezone
from app.services.contacts import resolve_contacts, resolve_contacts_for_company

logger = logging.getLogger(__name__)
DEVELOPER_TG_ID = 378881880
_task: asyncio.Task | None = None
_stop = asyncio.Event()


async def _send_reminder_dev_alert(
    bot: Bot,
    *,
    ev: dict[str, Any],
    action: str,
    endpoint: str,
    exc: Exception,
) -> None:
    tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-5:])[:700]
    text = "\n".join(
        [
            "🚨 Booking reminder diagnostic",
            f"🧩 action: {action}",
            "🧩 handler: app.services.booking_reminders.process_due_events",
            f"🆔 event_id: {ev.get('id') or 'n/a'}",
            f"🆔 yclients_record_id: {_s(ev.get('yclients_record_id')) or 'n/a'}",
            f"⏰ reminder_type: {_s(ev.get('reminder_type')) or 'n/a'}",
            f"➡️ endpoint/function: {endpoint}",
            f"🧯 exception: {type(exc).__name__}: {str(exc)[:180]}",
            f"🪵 traceback_last_lines:\n{tb_tail or 'n/a'}",
        ]
    )
    try:
        await bot.send_message(DEVELOPER_TG_ID, text[:1800])
    except Exception:
        logger.exception("booking_reminder_dev_alert_send_failed event_id=%s action=%s", ev.get("id"), action)


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""



def _safe_zoneinfo(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        logger.warning("booking_reminder_invalid_branch_timezone timezone=%s", timezone_name)
        return ZoneInfo(DEFAULT_TIMEZONE)


def _parse_visit_datetime(value: Any, branch_timezone: str | None) -> datetime:
    raw = _s(value)
    if not raw:
        raise ValueError("visit datetime is empty")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    tz = _safe_zoneinfo(branch_timezone)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _format_visit_datetime_for_branch(value: Any, branch_timezone: str | None) -> tuple[datetime, str, str]:
    dt_local = _parse_visit_datetime(value, branch_timezone)
    return dt_local, dt_local.strftime("%d.%m.%Y"), dt_local.strftime("%H:%M")


def _calculate_confirmation_scheduled_at_utc(visit_dt: datetime, now_dt: datetime) -> datetime | None:
    time_to_visit = visit_dt - now_dt
    if time_to_visit <= timedelta(0):
        return None
    if time_to_visit >= timedelta(days=2):
        scheduled_local = visit_dt - timedelta(days=2)
    elif time_to_visit >= timedelta(hours=6):
        scheduled_local = visit_dt - timedelta(hours=6)
    else:
        scheduled_local = now_dt
    return scheduled_local.astimezone(timezone.utc)


def _calculate_2h_scheduled_at_utc(visit_dt: datetime, now_dt: datetime) -> datetime | None:
    scheduled_local = visit_dt - timedelta(hours=2)
    if scheduled_local <= now_dt:
        return None
    return scheduled_local.astimezone(timezone.utc)

def _extract_list(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [x for x in payload["data"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


async def _resolve_tg_id(client_id: str | None, phone: str | None) -> int | None:
    if client_id:
        from app.db.sqlite import fetchone

        row = await fetchone("SELECT user_id FROM users WHERE CAST(yclients_client_id AS TEXT)=?", (client_id,))
        if row:
            return int(row["user_id"])
    if phone:
        user = await find_user_by_phone(phone)
        if user and user.get("user_id"):
            return int(user["user_id"])
    return None


async def scan_and_create_events(company_id: str) -> int:
    logger.info("booking_reminder_scan_started")
    client, _ = await build_yclients_client()
    created = 0
    try:
        now = datetime.now(timezone.utc)
        tz_context = await resolve_company_timezone(company_id)
        branch_timezone = tz_context.timezone_name
        branch_tz = _safe_zoneinfo(branch_timezone)
        now_branch = now.astimezone(branch_tz)
        payload = await list_bookings_by_date_range(client, company_id=company_id, date_from=now_branch.date().isoformat(), date_to=(now_branch + timedelta(days=14)).date().isoformat(), count=200)
        for row in _extract_list(payload):
            if _s(row.get("deleted")).lower() in {"1", "true"}:
                continue
            rid = _s(row.get("id") or row.get("record_id"))
            dt = _s(row.get("datetime") or row.get("date"))
            if not rid or not dt:
                continue
            detail = await get_booking_details(client, company_id=company_id, record_id=rid)
            d = (detail.get("data") if isinstance(detail, dict) else {}) if isinstance(detail, dict) else {}
            if not isinstance(d, dict):
                continue
            attendance = _s(d.get("attendance") or d.get("visit_attendance"))
            if attendance in {"-1", "1"}:
                continue
            visit_dt = _parse_visit_datetime(d.get("datetime") or dt, branch_timezone)
            visit_utc = visit_dt.astimezone(timezone.utc)
            cid = _s((d.get("client") or {}).get("id") or d.get("client_id")) or None
            phone = _s((d.get("client") or {}).get("phone")) or None
            tg_id = await _resolve_tg_id(cid, phone)
            base = dict(yclients_record_id=rid, yclients_client_id=cid, client_tg_id=tg_id, client_phone=phone, company_id=company_id, visit_datetime_utc=visit_utc.isoformat(), branch_timezone=branch_timezone)

            confirmation_scheduled_utc = _calculate_confirmation_scheduled_at_utc(visit_dt, now_branch)
            if confirmation_scheduled_utc is not None:
                eid = await create_event(**base, reminder_type="confirm_2d", status="pending", scheduled_at_utc=confirmation_scheduled_utc.isoformat())
                if eid:
                    created += 1

            reminder_2h_scheduled_utc = _calculate_2h_scheduled_at_utc(visit_dt, now_branch)
            if reminder_2h_scheduled_utc is not None:
                await create_event(**base, reminder_type="reminder_2h", status="pending", scheduled_at_utc=reminder_2h_scheduled_utc.isoformat())
        logger.info("booking_reminder_scan_finished created=%s", created)
    finally:
        await client.close()
    return created




def _extract_dict(payload: dict[str, Any] | list[Any] | Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def _active_record(record: dict[str, Any]) -> bool:
    if _s(record.get("deleted")).lower() in {"1", "true"}:
        return False
    attendance = _s(record.get("attendance") or record.get("visit_attendance"))
    if attendance in {"-1", "1"}:
        return False
    return True


def _first_name(fullname: str) -> str:
    clean = re.sub(r"\s+", " ", fullname).strip()
    if not clean:
        return ""
    return clean.split(" ")[0]



def _date_label_for(dt_local: datetime, now_local: datetime) -> str:
    delta_days = (dt_local.date() - now_local.date()).days
    if delta_days == 0:
        return "сегодня"
    if delta_days == 1:
        return "завтра"
    if delta_days == 2:
        return "послезавтра"
    return ""


async def _resolve_reminder_address(company_id: str) -> str:
    if company_id == "dev_test":
        contacts = await resolve_contacts()
    else:
        contacts = await resolve_contacts_for_company(company_id)
    return contacts.resolved.address


def _build_48h_text(*, client_name: str, master_name: str, service_name: str, visit_date: str, visit_time: str, date_label: str) -> str:
    greeting_name = client_name or "Здравствуйте"
    clean_master = master_name or "ваш мастер"
    clean_service = service_name or "услуга"
    date_fragment = f"{date_label} ({visit_date})" if date_label else visit_date
    return (
        f"{greeting_name}, здравствуйте! {clean_master} ждёт вас {date_fragment} "
        f"на услугу \"{clean_service}\" к {visit_time}.\n\n"
        "Подтвердите, пожалуйста, запись 👇"
    )

def _build_2h_text(*, client_name: str, service_name: str, visit_date: str, visit_time: str, master_name: str, branch_address: str | None) -> str:
    greeting_name = client_name or "Здравствуйте"
    service = service_name or "услугу"
    master = master_name or "ваш мастер"
    lines = [
        f"{greeting_name}, вы записаны на услугу «{service}», ждём вас {visit_date} к {visit_time}.",
        f"Ваш мастер: {master}",
        "",
    ]
    if branch_address:
        lines.extend([f"📍 Адрес: {branch_address}", ""])
    return "\n".join(lines)


def _reminder_2h_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📅 Мои записи", callback_data="my_bookings:open")], [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")]])


def _confirm_kb(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Да, запись в силе", callback_data=f"brc:y:{event_id}")], [InlineKeyboardButton(text="❌ Нет, отменить или перенести", callback_data=f"brc:n:{event_id}")]])


async def process_due_events(bot: Bot) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for ev in await get_due_events(now):
        action = "process_due_events"
        endpoint_function = "n/a"
        try:
            if not ev.get("client_tg_id"):
                await mark_status(int(ev["id"]), "skipped", error="not_mapped_to_telegram")
                continue
            if ev.get("reminder_type") == "confirm_2d":
                event_id = int(ev["id"])
                yclients_record_id = _s(ev.get("yclients_record_id"))
                company_id = _s(ev.get("company_id"))
                client_tg_id = int(ev["client_tg_id"])
                logger.info("booking_48h_confirmation_build_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)

                if yclients_record_id.startswith("dev-test-"):
                    branch_timezone = _s(ev.get("branch_timezone"))
                    dt_local, visit_date, _ = _format_visit_datetime_for_branch(ev.get("visit_datetime_utc"), branch_timezone)
                    visit_time = "21:00"
                    client_name = "Илья"
                    master_name = "Рената Пономарёва"
                    service_name = "МУЖСКАЯ СТРИЖКА"
                    date_label = _date_label_for(dt_local, datetime.now(_safe_zoneinfo(branch_timezone)))
                else:
                    logger.info("booking_48h_confirmation_yclients_fetch_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                    action = "booking_48h_confirmation_yclients_fetch"
                    endpoint_function = "get_booking_details"
                    client, _ = await build_yclients_client()
                    try:
                        detail_payload = await get_booking_details(client, company_id=company_id, record_id=yclients_record_id)
                        record = _extract_dict(detail_payload)
                    finally:
                        await client.close()
                    logger.info("booking_48h_confirmation_yclients_fetch_finished event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)

                    if not _active_record(record):
                        await mark_status(event_id, "skipped", error="record_not_active")
                        continue

                    raw_client = record.get("client") if isinstance(record.get("client"), dict) else {}
                    full_name = _s(raw_client.get("name") or raw_client.get("fullname") or record.get("fullname") or record.get("client_name"))
                    client_name = _first_name(full_name) or _first_name(_s(ev.get("client_name"))) or "Здравствуйте"

                    raw_staff = record.get("staff") if isinstance(record.get("staff"), dict) else {}
                    master_name = _s(raw_staff.get("name") or record.get("staff_name") or record.get("master_name"))

                    raw_services = record.get("services") if isinstance(record.get("services"), list) else []
                    service_name = ""
                    if raw_services and isinstance(raw_services[0], dict):
                        service_name = _s(raw_services[0].get("title") or raw_services[0].get("name"))
                    service_name = service_name or _s(record.get("service_name"))

                    branch_timezone = _s(record.get("timezone") or record.get("time_zone") or ev.get("branch_timezone"))
                    dt_local, visit_date, visit_time = _format_visit_datetime_for_branch(record.get("datetime") or ev.get("visit_datetime_utc"), branch_timezone)
                    now_local = datetime.now(_safe_zoneinfo(branch_timezone))
                    date_label = _date_label_for(dt_local, now_local)

                client_name_present = bool(client_name and client_name != "Здравствуйте")
                master_name_present = bool(master_name)
                service_name_present = bool(service_name)
                visit_datetime = f"{visit_date} {visit_time}"
                text = _build_48h_text(client_name=client_name, master_name=master_name, service_name=service_name, visit_date=visit_date, visit_time=visit_time, date_label=date_label)
                logger.info("booking_48h_confirmation_template_rendered event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s client_name_present=%s master_name_present=%s service_name_present=%s visit_datetime=%s", event_id, yclients_record_id, client_tg_id, company_id, client_name_present, master_name_present, service_name_present, visit_datetime)
                logger.info("booking_48h_confirmation_send_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                res = await safe_send(bot, "send_message", chat_id=client_tg_id, text=text, reply_markup=_confirm_kb(event_id), disable_notification=False)
                if res.ok:
                    await mark_status(event_id, "sent", sent=True)
                    logger.info("booking_48h_confirmation_send_finished event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                elif res.skipped:
                    await mark_status(event_id, "skipped", error=res.error or "telegram_send_skipped")
                else:
                    await mark_status(event_id, "failed", error=res.error or "telegram_send_failed")
            else:
                assert is_white_notification("booking_reminder")
                event_id = int(ev["id"])
                yclients_record_id = _s(ev.get("yclients_record_id"))
                company_id = _s(ev.get("company_id"))
                client_tg_id = int(ev["client_tg_id"])
                logger.info("booking_2h_reminder_build_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)

                if yclients_record_id.startswith("dev-test-"):
                    logger.info("booking_2h_reminder_dev_test_payload_used event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                    branch_timezone = _s(ev.get("branch_timezone"))
                    _, visit_date, _ = _format_visit_datetime_for_branch(ev.get("visit_datetime_utc"), branch_timezone)
                    branch_address = await _resolve_reminder_address(company_id)
                    text = _build_2h_text(client_name="Илья", service_name="МУЖСКАЯ СТРИЖКА", visit_date=visit_date, visit_time="21:00", master_name="Рената Пономарёва", branch_address=branch_address)
                    visit_datetime = f"{visit_date} 21:00"
                    service_name_present = True
                    master_name_present = True
                    address_present = True
                else:
                    logger.info("booking_2h_reminder_yclients_fetch_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                    action = "booking_2h_reminder_yclients_fetch"
                    endpoint_function = "get_booking_details/get_company"
                    client, _ = await build_yclients_client()
                    try:
                        detail_payload = await get_booking_details(client, company_id=company_id, record_id=yclients_record_id)
                        record = _extract_dict(detail_payload)
                        company_payload = await get_company(client, company_id=company_id)
                        company = _extract_dict(company_payload)
                    finally:
                        await client.close()
                    logger.info("booking_2h_reminder_yclients_fetch_finished event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)

                    if not _active_record(record):
                        await mark_status(event_id, "skipped", error="record_not_active")
                        continue

                    raw_client = record.get("client") if isinstance(record.get("client"), dict) else {}
                    full_name = _s(raw_client.get("name") or raw_client.get("fullname") or record.get("fullname") or record.get("client_name"))
                    client_name = _first_name(full_name) or full_name or "Здравствуйте"

                    raw_services = record.get("services") if isinstance(record.get("services"), list) else []
                    service_name = ""
                    if raw_services and isinstance(raw_services[0], dict):
                        service_name = _s(raw_services[0].get("title") or raw_services[0].get("name"))
                    service_name = service_name or _s(record.get("service_name"))

                    raw_staff = record.get("staff") if isinstance(record.get("staff"), dict) else {}
                    master_name = _s(raw_staff.get("name") or record.get("staff_name") or record.get("master_name"))

                    branch_timezone = _s(company.get("timezone") or company.get("time_zone") or company.get("tz") or company.get("timezone_name") or ev.get("branch_timezone"))
                    _, visit_date, visit_time = _format_visit_datetime_for_branch(record.get("datetime") or ev.get("visit_datetime_utc"), branch_timezone)

                    endpoint_function = "_resolve_reminder_address"
                    branch_address = await _resolve_reminder_address(company_id)
                    service_name_present = bool(service_name)
                    master_name_present = bool(master_name)
                    address_present = bool(branch_address)
                    visit_datetime = f"{visit_date} {visit_time}"
                    text = _build_2h_text(client_name=client_name, service_name=service_name, visit_date=visit_date, visit_time=visit_time, master_name=master_name, branch_address=branch_address)

                logger.info("booking_2h_reminder_template_rendered event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s service_name_present=%s master_name_present=%s address_present=%s visit_datetime=%s", event_id, yclients_record_id, client_tg_id, company_id, service_name_present, master_name_present, address_present, visit_datetime)
                logger.info("booking_2h_reminder_send_started event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                res = await safe_send(bot, "send_message", chat_id=client_tg_id, text=text, reply_markup=_reminder_2h_kb(), disable_notification=False)
                if res.ok:
                    await mark_status(event_id, "sent", sent=True)
                    logger.info("booking_2h_reminder_send_finished event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, yclients_record_id, client_tg_id, company_id)
                elif res.skipped:
                    await mark_status(event_id, "skipped", error=res.error or "telegram_send_skipped")
                else:
                    await mark_status(event_id, "failed", error=res.error or "telegram_send_failed")
        except Exception as exc:
            logger.exception("booking_reminder_processing_failed event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s reminder_type=%s action=%s endpoint_function=%s exception_type=%s exception_message=%s", ev.get("id"), ev.get("yclients_record_id"), ev.get("client_tg_id"), ev.get("company_id"), ev.get("reminder_type"), action, endpoint_function, type(exc).__name__, str(exc)[:180])
            await _send_reminder_dev_alert(bot, ev=ev, action=action, endpoint=endpoint_function, exc=exc)
            await mark_status(int(ev["id"]), "failed", error=str(exc)[:180])


def start_booking_reminder_sender(bot: Bot) -> None:
    global _task
    if _task and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_run(bot), name="booking-reminder-sender")


async def stop_booking_reminder_sender() -> None:
    global _task
    if not _task:
        return
    _stop.set(); _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


async def _run(bot: Bot) -> None:
    logger.info("booking_reminder_worker_started")
    while not _stop.is_set():
        try:
            _, company_id = await build_yclients_client()
            await scan_and_create_events(company_id)
            await process_due_events(bot)
        except Exception:
            logger.exception("booking_reminder_worker_failed")
        await asyncio.sleep(300)
