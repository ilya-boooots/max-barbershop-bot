from __future__ import annotations

import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.integrations.yclients import build_yclients_client
from app.integrations.yclients.endpoints import cancel_booking, get_booking_details, update_booking
from app.repositories.booking_reminder_events import get_event, mark_status
from app.services.booking_reminders import _s

logger = logging.getLogger(__name__)
DEVELOPER_TG_ID = 378881880

router = Router()


def _extract_record_payload(raw: dict | list | None) -> dict:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict):
            return data
        return raw
    return {}


def _build_record_update_payload_from_record(record: dict, record_id: str, *, attendance: int | None = None) -> dict:
    services_raw = record.get("services") if isinstance(record.get("services"), list) else []
    services: list[dict] = []
    for item in services_raw:
        if not isinstance(item, dict):
            continue
        service_id = item.get("id") or item.get("service_id")
        if service_id is None:
            continue
        service_payload: dict = {"id": service_id}
        for k in ("first_cost", "cost", "discount"):
            if item.get(k) is not None:
                service_payload[k] = item.get(k)
        services.append(service_payload)

    staff = record.get("staff") if isinstance(record.get("staff"), dict) else {}
    payload = {
        "id": record.get("id") or record_id,
        "staff_id": record.get("staff_id") or staff.get("id"),
        "services": services,
        "client": record.get("client") if isinstance(record.get("client"), dict) else {},
        "datetime": record.get("datetime"),
        "seance_length": record.get("seance_length") or record.get("length"),
        "save_if_busy": False,
    }
    current_attendance = attendance if attendance is not None else record.get("attendance")
    if current_attendance is not None:
        payload["attendance"] = current_attendance
    for key in ("send_sms", "comment", "sms_remain_hours", "email_remain_hours", "api_id", "custom_color"):
        if record.get(key) is not None:
            payload[key] = record.get(key)
    return payload


def _build_confirmation_payload_from_record(record: dict, record_id: str) -> dict:
    return _build_record_update_payload_from_record(record, record_id, attendance=2)


def _safe_event_id(callback_data: str | None, prefix: str) -> int | None:
    if not isinstance(callback_data, str) or not callback_data.startswith(prefix) or len(callback_data.encode("utf-8")) > 64:
        return None
    raw = callback_data.removeprefix(prefix)
    return int(raw) if raw.isdigit() else None


def _safe_branch_now(branch_timezone: str | None) -> datetime:
    try:
        tz = ZoneInfo(branch_timezone or "UTC")
    except ZoneInfoNotFoundError:
        logger.warning("booking_48h_confirmation_invalid_branch_timezone timezone=%s", branch_timezone)
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def _append_once_comment(existing_comment: object, line: str, *, marker: str) -> str:
    current = str(existing_comment or "").strip()
    lines = [part.strip() for part in current.splitlines()] if current else []
    if any(part.startswith(marker) for part in lines):
        return current
    return "\n".join([*lines, line]) if lines else line


def _reminder_action_line(action: str, branch_timezone: str | None) -> str:
    now = _safe_branch_now(branch_timezone)
    return f"Клиент {action} запись из телеграм бота {now.strftime('%d.%m.%Y')} в {now.strftime('%H:%M')}"


async def _send_comment_diagnostic(cb: CallbackQuery, *, action: str, event_id: int, record_id: str, endpoint: str, exc: Exception) -> None:
    diagnostic = (
        "booking_48h_confirmation_comment_append_failed\n"
        f"action={action}\n"
        f"event_id={event_id}\n"
        f"record_id={record_id}\n"
        f"endpoint={endpoint}\n"
        f"exception={type(exc).__name__}: {str(exc)[:300]}"
    )
    try:
        await cb.bot.send_message(DEVELOPER_TG_ID, diagnostic)
    except Exception:
        logger.exception("booking_48h_confirmation_comment_diagnostic_failed event_id=%s record_id=%s", event_id, record_id)


@router.callback_query(F.data.startswith("brc:y:"))
async def confirm_yes(cb: CallbackQuery) -> None:
    event_id = _safe_event_id(cb.data, "brc:y:")
    if event_id is None:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    ev = await get_event(event_id)
    if not ev:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return

    record_id = str(ev.get("yclients_record_id") or "")
    if not record_id:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    if str(ev.get("status") or "") == "confirmed":
        if cb.message:
            await cb.message.answer("✅ Спасибо за ответ. Ваша запись подтверждена!")
        await cb.answer()
        return
    logger.info("booking_48h_confirmation_yes_clicked event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, record_id, ev.get("client_tg_id"), ev.get("company_id"))

    if record_id.startswith("dev-test-"):
        await mark_status(event_id, "confirmed", clicked=True)
        await cb.message.answer("✅ Спасибо за ответ. Ваша запись подтверждена!")
        await cb.answer()
        return

    client, default_company_id = await build_yclients_client()
    company_id = str(ev.get("company_id") or default_company_id)
    payload_keys: list[str] = []
    try:
        endpoint = f"/api/v1/record/{company_id}/{record_id}"
        logger.info("booking_48h_confirmation_record_fetch_started event_id=%s record_id=%s company_id=%s endpoint=%s method=%s", event_id, record_id, company_id, endpoint, "GET")
        details = await get_booking_details(client, company_id=company_id, record_id=record_id)
        record = _extract_record_payload(details if isinstance(details, (dict, list)) else None)
        logger.info("booking_48h_confirmation_record_fetch_finished event_id=%s record_id=%s company_id=%s endpoint=%s method=%s payload_keys=%s", event_id, record_id, company_id, endpoint, "GET", sorted(record.keys()))

        payload = _build_confirmation_payload_from_record(record, record_id)
        payload_keys = sorted(payload.keys())
        required_fields_present = bool(payload.get("staff_id")) and bool(payload.get("services")) and bool(payload.get("client")) and bool(payload.get("seance_length")) and bool(payload.get("datetime"))
        logger.info("booking_48h_confirmation_payload_built event_id=%s record_id=%s company_id=%s endpoint=%s method=%s payload_keys=%s required_fields_present=%s", event_id, record_id, company_id, endpoint, "PUT", payload_keys, "yes" if required_fields_present else "no")

        logger.info("booking_48h_confirmation_yclients_confirm_started event_id=%s record_id=%s company_id=%s endpoint=%s method=%s payload_keys=%s required_fields_present=%s", event_id, record_id, company_id, endpoint, "PUT", payload_keys, "yes" if required_fields_present else "no")
        await update_booking(client, company_id=company_id, record_id=record_id, payload=payload)
        logger.info("booking_48h_confirmation_yclients_confirm_finished event_id=%s record_id=%s company_id=%s endpoint=%s method=%s", event_id, record_id, company_id, endpoint, "PUT")

        try:
            comment_payload = _build_record_update_payload_from_record(record, record_id, attendance=2)
            comment_line = _reminder_action_line("подтвердил", ev.get("branch_timezone"))
            comment_payload["comment"] = _append_once_comment(record.get("comment"), comment_line, marker="Клиент подтвердил запись из телеграм бота")
            await update_booking(client, company_id=company_id, record_id=record_id, payload=comment_payload)
        except Exception as exc:
            logger.exception("booking_48h_confirmation_confirm_comment_append_failed event_id=%s record_id=%s company_id=%s", event_id, record_id, company_id)
            await _send_comment_diagnostic(cb, action="confirm_booking_48h_comment", event_id=event_id, record_id=record_id, endpoint=endpoint, exc=exc)

        await mark_status(event_id, "confirmed", clicked=True)
        await cb.message.answer("✅ Спасибо за ответ. Ваша запись подтверждена!")
    except Exception as exc:
        endpoint = f"/api/v1/record/{company_id}/{record_id}"
        logger.error("booking_48h_confirmation_yclients_confirm_failed event_id=%s record_id=%s company_id=%s endpoint=%s method=%s exception_type=%s exception_message=%s payload_keys=%s", event_id, record_id, company_id, endpoint, "PUT", type(exc).__name__, str(exc)[:180], payload_keys)
        await cb.message.answer("⚠️ Не удалось подтвердить запись. Попробуйте позже.")
        tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:])[-900:]
        diagnostic = (
            "booking_48h_confirmation_yclients_confirm_failed\n"
            f"action=confirm_booking_48h\n"
            f"event_id={event_id}\n"
            f"record_id={record_id}\n"
            f"endpoint={endpoint}\n"
            "method=PUT\n"
            f"payload_keys={','.join(payload_keys) if payload_keys else '-'}\n"
            f"exception={type(exc).__name__}: {str(exc)[:300]}\n"
            f"traceback_tail={tb_tail}"
        )
        try:
            await cb.bot.send_message(DEVELOPER_TG_ID, diagnostic)
        except Exception:
            logger.exception("booking_48h_confirmation_developer_diagnostic_failed event_id=%s record_id=%s", event_id, record_id)
    finally:
        await client.close()
    await cb.answer()


@router.callback_query(F.data.startswith("brc:n:"))
async def confirm_no(cb: CallbackQuery) -> None:
    event_id = _safe_event_id(cb.data, "brc:n:")
    if event_id is None:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    ev = await get_event(event_id)
    logger.info("booking_48h_confirmation_no_clicked event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, ev.get("yclients_record_id") if ev else None, ev.get("client_tg_id") if ev else None, ev.get("company_id") if ev else None)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"brc:c:{event_id}")],
        [InlineKeyboardButton(text="🔁 Перенести запись", callback_data=f"brc:r:{event_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_bookings:open")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ])
    await cb.message.answer("Поняли. Что хотите сделать?", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("brc:c:"))
async def cancel_from_reminder(cb: CallbackQuery) -> None:
    event_id = _safe_event_id(cb.data, "brc:c:")
    if event_id is None:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    ev = await get_event(event_id)
    record_id = str(ev.get("yclients_record_id") or "") if ev else ""
    if not ev or not record_id:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    if str(ev.get("status") or "") == "cancelled":
        if cb.message:
            await cb.message.answer("✅ Запись отменена.")
        await cb.answer()
        return

    logger.info("booking_48h_confirmation_cancel_clicked event_id=%s yclients_record_id=%s client_tg_id=%s company_id=%s", event_id, record_id, ev.get("client_tg_id"), ev.get("company_id"))
    if record_id.startswith("dev-test-"):
        await mark_status(event_id, "cancelled", clicked=True)
        if cb.message:
            await cb.message.answer("✅ Запись отменена.")
        await cb.answer()
        return

    client, default_company_id = await build_yclients_client()
    company_id = str(ev.get("company_id") or default_company_id)
    record: dict = {}
    endpoint = f"/api/v1/record/{company_id}/{record_id}"
    try:
        details = await get_booking_details(client, company_id=company_id, record_id=record_id)
        record = _extract_record_payload(details if isinstance(details, (dict, list)) else None)
        try:
            comment_line = _reminder_action_line("отменил", ev.get("branch_timezone"))
            payload = _build_record_update_payload_from_record(record, record_id)
            payload["comment"] = _append_once_comment(record.get("comment"), comment_line, marker="Клиент отменил запись из телеграм бота")
            await update_booking(client, company_id=company_id, record_id=record_id, payload=payload)
        except Exception as exc:
            logger.exception("booking_48h_confirmation_cancel_comment_append_failed event_id=%s record_id=%s company_id=%s", event_id, record_id, company_id)
            await _send_comment_diagnostic(cb, action="cancel_booking_48h_comment", event_id=event_id, record_id=record_id, endpoint=endpoint, exc=exc)
        await cancel_booking(client, company_id=company_id, record_id=record_id)
        await mark_status(event_id, "cancelled", clicked=True)
        if cb.message:
            await cb.message.answer("✅ Запись отменена.")
    except Exception as exc:
        logger.error("booking_48h_confirmation_cancel_failed event_id=%s record_id=%s company_id=%s endpoint=%s method=DELETE exception_type=%s exception_message=%s", event_id, record_id, company_id, endpoint, type(exc).__name__, str(exc)[:180])
        if cb.message:
            await cb.message.answer("⚠️ Не удалось отменить запись. Попробуйте позже.")
        await cb.answer()
        return
    finally:
        await client.close()

    await cb.answer()


@router.callback_query(F.data.startswith("brc:r:"))
async def reschedule_from_reminder(cb: CallbackQuery) -> None:
    event_id = _safe_event_id(cb.data, "brc:r:")
    ev = await get_event(event_id) if event_id is not None else None
    record_id = str(ev.get("yclients_record_id") or "") if ev else ""
    if not record_id:
        await cb.answer("⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи».", show_alert=True)
        return
    await cb.message.answer("Откройте «Мои записи» и выберите перенос этой записи.")
    await cb.answer()
