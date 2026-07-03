"""Booking confirmation and reminder notifications for MAX users."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.contacts import ContactsService
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)
from max_barbershop_bot.services.feedback import send_due_feedback_requests
from max_barbershop_bot.services.repeat_visit import process_due_repeat_visit_events
from max_barbershop_bot.services.notifications import (
    NotificationHistoryRecord,
    BOOKING_CONFIRMATION_IMMEDIATE,
    BOOKING_REMINDER_2H,
    BOOKING_REMINDER_6H,
    BOOKING_REMINDER_48H,
    get_notification_history,
    mark_notification_history_skipped,
    send_business_notification,
)

logger = logging.getLogger(__name__)

REMINDER_OFFSETS = {
    BOOKING_REMINDER_48H: timedelta(hours=48),
    BOOKING_REMINDER_2H: timedelta(hours=2),
}
_NOTIFICATION_TYPE_LABELS = {
    BOOKING_CONFIRMATION_IMMEDIATE: "подтверждение записи",
    BOOKING_REMINDER_48H: "подтверждение записи за 2 дня",
    BOOKING_REMINDER_6H: "напоминание за 6 часов",
    BOOKING_REMINDER_2H: "напоминание за 2 часа",
}


@dataclass(frozen=True)
class BookingNotificationContext:
    """Normalized booking data needed to render a MAX notification."""

    platform_user_id: str
    yclients_record_id: str
    notification_type: str
    booking_datetime: datetime
    service_name: str
    master_name: str
    client_name: str = ""
    branch_address: str | None = None
    yclients_client_id: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None
    scheduled_for: datetime | None = None


@dataclass(frozen=True)
class DueReminder:
    """One due reminder candidate verified against YClients."""

    context: BookingNotificationContext
    record: dict[str, Any]


@dataclass(frozen=True)
class ReminderLoopStatus:
    """In-memory diagnostics for the running reminder loop."""

    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_class: str | None = None
    is_running: bool = False


_reminder_loop_status = ReminderLoopStatus()


def get_reminder_loop_status() -> ReminderLoopStatus:
    """Return the current in-memory reminder loop status."""

    return _reminder_loop_status


def build_reminder_schedule(booking_datetime: datetime, timezone_name: str, *, now: datetime | None = None) -> dict[str, datetime]:
    """Return Telegram-aligned scheduled moments in the branch timezone."""

    branch_timezone = _zoneinfo(timezone_name)
    local_dt = _ensure_timezone(booking_datetime, branch_timezone)
    now_local = _ensure_timezone(now or datetime.now(UTC), branch_timezone)
    schedule = {}
    confirmation_due = _calculate_telegram_confirmation_due(local_dt, now_local)
    if confirmation_due is not None:
        schedule[BOOKING_REMINDER_48H] = confirmation_due
    reminder_2h_due = _calculate_telegram_2h_due(local_dt, now_local)
    if reminder_2h_due is not None:
        schedule[BOOKING_REMINDER_2H] = reminder_2h_due
    return schedule


def render_booking_notification_text(context: BookingNotificationContext, timezone_name: str) -> str:
    """Render Russian booking notification text for MAX."""

    branch_timezone = _zoneinfo(timezone_name)
    dt_local = _ensure_timezone(context.booking_datetime, branch_timezone)
    date_text = dt_local.strftime("%d.%m.%Y")
    time_text = dt_local.strftime("%H:%M")
    service_name = context.service_name or "услуга"
    master_name = context.master_name or "ваш мастер"
    client_name = _first_name(context.client_name) or "Здравствуйте"
    date_label = _date_label_for(dt_local, datetime.now(branch_timezone))

    if context.notification_type == BOOKING_CONFIRMATION_IMMEDIATE:
        return (
            "✅ Готово! Вы записаны 💈\n\n"
            f"Услуга: {service_name}\n"
            f"Мастер: {master_name}\n"
            f"Дата: {date_text}\n"
            f"Время: {time_text}"
        )
    if context.notification_type == BOOKING_REMINDER_48H:
        date_fragment = f"{date_label} ({date_text})" if date_label else date_text
        return (
            f"{client_name}, здравствуйте! {master_name} ждёт вас {date_fragment} "
            f"на услугу \"{service_name}\" к {time_text}.\n\n"
            "Подтвердите, пожалуйста, запись 👇"
        )
    if context.notification_type == BOOKING_REMINDER_2H:
        lines = [
            f"{client_name}, вы записаны на услугу «{service_name}», ждём вас {date_text} к {time_text}.",
            f"Ваш мастер: {master_name}",
            "",
        ]
        if context.branch_address:
            lines.extend([f"📍 Адрес: {context.branch_address}", ""])
        return "\n".join(lines)
    raise ValueError(f"Неизвестный тип уведомления: {context.notification_type}")


async def send_booking_notification(
    sender: MaxMessageSender,
    *,
    database_path: str,
    context: BookingNotificationContext,
    timezone_name: str,
    keyboard: MaxInlineKeyboard | None = None,
    text_override: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    respect_global_settings: bool = True,
) -> NotificationHistoryRecord | None:
    """Send and record one booking notification without raising transport errors."""

    recipient_type, recipient_id = _recipient(context)
    if recipient_id is None:
        logger.info(
            "booking_notification_skipped_no_recipient platform_user_id=%s yclients_record_id=%s notification_type=%s",
            context.platform_user_id,
            context.yclients_record_id,
            context.notification_type,
        )
        return mark_notification_history_skipped(
            database_path,
            platform=PLATFORM_MAX,
            platform_user_id=context.platform_user_id,
            yclients_record_id=context.yclients_record_id,
            notification_type=context.notification_type,
            scheduled_for=_iso(context.scheduled_for),
            reason="recipient_not_found",
        )

    text = text_override or render_booking_notification_text(context, timezone_name)
    try:
        return await send_business_notification(
            sender,
            database_path=database_path,
            platform=PLATFORM_MAX,
            platform_user_id=context.platform_user_id,
            max_user_id=context.max_user_id,
            chat_id=context.chat_id,
            yclients_record_id=context.yclients_record_id,
            yclients_client_id=context.yclients_client_id,
            notification_type=context.notification_type,
            scheduled_for=_iso(context.scheduled_for),
            text=text,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            keyboard=keyboard,
            attachments=attachments,
            metadata={"label": _NOTIFICATION_TYPE_LABELS.get(context.notification_type)},
            respect_global_settings=respect_global_settings,
        )
    except Exception:
        logger.warning(
            "booking_notification_failed_safely platform_user_id=%s yclients_record_id=%s notification_type=%s",
            context.platform_user_id,
            context.yclients_record_id,
            context.notification_type,
            exc_info=True,
        )
        return None


async def send_immediate_confirmation(
    sender: MaxMessageSender,
    *,
    database_path: str,
    platform_user_id: str,
    yclients_record_id: str,
    booking_datetime: datetime,
    service_name: str,
    master_name: str,
    timezone_name: str,
    yclients_client_id: str | None = None,
    max_user_id: str | None = None,
    chat_id: str | None = None,
    keyboard: MaxInlineKeyboard | None = None,
    text_override: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    respect_global_settings: bool = True,
) -> NotificationHistoryRecord | None:
    """Send the booking success confirmation through the notification service."""

    context = BookingNotificationContext(
        platform_user_id=platform_user_id,
        max_user_id=max_user_id,
        chat_id=chat_id,
        yclients_record_id=yclients_record_id,
        yclients_client_id=yclients_client_id,
        notification_type=BOOKING_CONFIRMATION_IMMEDIATE,
        booking_datetime=booking_datetime,
        service_name=service_name,
        master_name=master_name,
        scheduled_for=datetime.now(_zoneinfo(timezone_name)),
    )
    return await send_booking_notification(
        sender,
        database_path=database_path,
        context=context,
        timezone_name=timezone_name,
        keyboard=keyboard,
        text_override=text_override,
        attachments=attachments,
        respect_global_settings=respect_global_settings,
    )


async def get_due_reminders(
    *,
    database_path: str,
    now: datetime | None = None,
    timezone_name: str | None = None,
    limit: int = 200,
) -> list[DueReminder]:
    """Find due reminders from local attribution and verify each record in YClients."""

    if not AppSettingsRepository(database_path).notifications_enabled():
        _log_reminder_diagnostic(reminder_type=BOOKING_REMINDER_48H, loop_enabled=False, skipped_notifications_disabled_count=1)
        return []

    settings = load_active_yclients_settings(
        YClientsSettingsRepository(database_path),
        operation="get_due_reminders",
    )
    if not has_required_yclients_credentials(settings):
        logger.info("booking_reminders_skipped_yclients_not_configured")
        return []

    branch_timezone_name = normalize_branch_timezone(timezone_name or settings.branch_timezone, flow="reminders", operation="get_due_reminders")
    branch_timezone = _zoneinfo(branch_timezone_name)
    now_local = _ensure_timezone(now or datetime.now(UTC), branch_timezone)
    loop_started_at = datetime.now(UTC)
    window_start = now_local
    window_end = now_local + timedelta(hours=48)
    yclients_records_checked = 0
    due_candidates_count = 0
    skipped_cancelled_count = 0
    skipped_deleted_count = 0
    skipped_past_count = 0
    skipped_duplicate_count = 0
    skipped_no_platform_mapping_count = 0
    skipped_notifications_disabled_count = 0
    skipped_blocked_count = 0
    due: list[DueReminder] = []
    attributions = PlatformAttributionRepository(database_path).list_with_yclients_record_ids(limit=limit)
    if not attributions:
        _log_reminder_diagnostic(reminder_type=BOOKING_REMINDER_48H, loop_enabled=True, loop_interval_seconds=None, branch_timezone=branch_timezone_name, now_branch_time=now_local.isoformat(), due_window_start=window_start.isoformat(), due_window_end=window_end.isoformat(), yclients_records_checked=0, due_candidates_count=0, skipped_cancelled_count=0, skipped_deleted_count=0, skipped_past_count=0, skipped_duplicate_count=0, skipped_no_platform_mapping_count=0, skipped_notifications_disabled_count=0, skipped_blocked_count=0, sent_count=0, failed_count=0, duration_ms=0)
        return due

    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=settings.company_id)
        for attribution in attributions:
            if not attribution.yclients_record_id:
                continue
            user = UsersRepository(database_path).find_by_platform_user_id(attribution.platform_user_id, platform=attribution.platform)
            if user is None:
                skipped_no_platform_mapping_count += 1
            if user is not None and not user.notifications_enabled:
                skipped_notifications_disabled_count += 1
            if user is None or not user.notifications_enabled:
                continue
            try:
                yclients_records_checked += 1
                payload = await service.get_booking_details(
                    company_id=settings.company_id,
                    yclients_record_id=attribution.yclients_record_id,
                )
            except Exception:
                logger.warning(
                    "booking_reminder_record_fetch_failed platform_user_id=%s yclients_record_id=%s",
                    attribution.platform_user_id,
                    attribution.yclients_record_id,
                    exc_info=True,
                )
                continue
            record = _extract_record(payload)
            if not _record_is_active(record):
                if _record_is_deleted(record):
                    skipped_deleted_count += 1
                else:
                    skipped_cancelled_count += 1
                _record_skipped_reminders(
                    database_path,
                    attribution=attribution,
                    timezone_name=branch_timezone_name,
                    reason="record_not_active",
                )
                continue
            booking_datetime = _record_datetime(record, branch_timezone_name)
            if booking_datetime is None:
                continue
            booking_datetime = _ensure_timezone(booking_datetime, branch_timezone)
            if booking_datetime <= now_local:
                skipped_past_count += 1
                _record_skipped_reminders(
                    database_path,
                    attribution=attribution,
                    timezone_name=branch_timezone_name,
                    reason="booking_in_past",
                )
                continue
            schedule = build_reminder_schedule(booking_datetime, branch_timezone_name, now=now_local)
            branch_address = await _resolve_branch_address(database_path)
            for notification_type, scheduled_for in schedule.items():
                if not (scheduled_for <= now_local < booking_datetime):
                    continue
                existing_history = get_notification_history(
                    database_path,
                    platform=PLATFORM_MAX,
                    platform_user_id=attribution.platform_user_id,
                    yclients_record_id=attribution.yclients_record_id,
                    notification_type=notification_type,
                )
                if existing_history:
                    skipped_duplicate_count += 1
                    if existing_history.is_blocked or existing_history.is_stopped:
                        skipped_blocked_count += 1
                    continue
                due_candidates_count += 1
                due.append(
                    DueReminder(
                        context=BookingNotificationContext(
                            platform_user_id=attribution.platform_user_id,
                            max_user_id=user.max_user_id,
                            chat_id=user.chat_id,
                            yclients_record_id=attribution.yclients_record_id,
                            yclients_client_id=attribution.yclients_client_id or user.yclients_client_id,
                            notification_type=notification_type,
                            booking_datetime=booking_datetime,
                            service_name=_record_service_name(record),
                            master_name=_record_master_name(record),
                            client_name=_record_client_name(record),
                            branch_address=branch_address,
                            scheduled_for=scheduled_for,
                        ),
                        record=record,
                    )
                )
    _log_reminder_diagnostic(reminder_type=BOOKING_REMINDER_48H, loop_enabled=True, loop_interval_seconds=None, branch_timezone=branch_timezone_name, now_branch_time=now_local.isoformat(), due_window_start=window_start.isoformat(), due_window_end=window_end.isoformat(), yclients_records_checked=yclients_records_checked, due_candidates_count=due_candidates_count, skipped_cancelled_count=skipped_cancelled_count, skipped_deleted_count=skipped_deleted_count, skipped_past_count=skipped_past_count, skipped_duplicate_count=skipped_duplicate_count, skipped_no_platform_mapping_count=skipped_no_platform_mapping_count, skipped_notifications_disabled_count=skipped_notifications_disabled_count, skipped_blocked_count=skipped_blocked_count, sent_count=0, failed_count=0, duration_ms=int((datetime.now(UTC) - loop_started_at).total_seconds() * 1000))
    return due


async def send_due_reminders(
    sender: MaxMessageSender,
    *,
    database_path: str,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> int:
    """Send all currently due booking reminders safely."""

    settings = load_active_yclients_settings(
        YClientsSettingsRepository(database_path),
        operation="send_due_reminders_timezone",
    )
    branch_timezone_name = normalize_branch_timezone(timezone_name or (settings.branch_timezone if settings else None), flow="reminders", operation="send_due_reminders")
    sent_or_recorded = 0
    failed_count = 0
    loop_started_at = datetime.now(UTC)
    for reminder in await get_due_reminders(database_path=database_path, now=now, timezone_name=branch_timezone_name):
        result = await send_booking_notification(
            sender,
            database_path=database_path,
            context=reminder.context,
            timezone_name=branch_timezone_name,
            keyboard=booking_reminder_keyboard(reminder.context),
        )
        if result is None or result.status in {"failed", "blocked", "stopped", "rate_limited", "delivery_error"}:
            failed_count += 1
        elif result.status == "sent":
            sent_or_recorded += 1
        else:
            sent_or_recorded += 1
    _log_reminder_diagnostic(
        reminder_type="booking_confirmation_2d+booking_reminder_2h",
        loop_enabled=True,
        loop_interval_seconds=None,
        branch_timezone=branch_timezone_name,
        sent_count=sent_or_recorded,
        failed_count=failed_count,
        duration_ms=int((datetime.now(UTC) - loop_started_at).total_seconds() * 1000),
    )
    return sent_or_recorded


async def run_reminder_loop(
    sender: MaxMessageSender,
    *,
    database_path: str,
    stop_event: asyncio.Event,
    interval_seconds: int,
    error_callback: Callable[[Exception], Awaitable[object]] | None = None,
) -> None:
    """Run a small reminder loop alongside polling."""

    global _reminder_loop_status
    interval = max(30, int(interval_seconds))
    _reminder_loop_status = ReminderLoopStatus(last_started_at=datetime.now(UTC), is_running=True)
    logger.info("booking_reminder_loop_started interval_seconds=%s", interval)
    try:
        while not stop_event.is_set():
            try:
                count = await send_due_reminders(sender, database_path=database_path)
                feedback_count = await send_due_feedback_requests(sender, database_path=database_path)
                repeat_visit_count = await process_due_repeat_visit_events(sender, database_path=database_path)
                _reminder_loop_status = ReminderLoopStatus(
                    last_started_at=_reminder_loop_status.last_started_at,
                    last_success_at=datetime.now(UTC),
                    last_error_at=_reminder_loop_status.last_error_at,
                    last_error_class=_reminder_loop_status.last_error_class,
                    is_running=True,
                )
                if count or feedback_count or repeat_visit_count:
                    logger.info("booking_reminder_loop_processed count=%s feedback_count=%s repeat_visit_count=%s", count, feedback_count, repeat_visit_count)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _reminder_loop_status = ReminderLoopStatus(
                    last_started_at=_reminder_loop_status.last_started_at,
                    last_success_at=_reminder_loop_status.last_success_at,
                    last_error_at=datetime.now(UTC),
                    last_error_class=type(error).__name__,
                    is_running=True,
                )
                if error_callback is None:
                    logger.exception("booking_reminder_loop_failed_safely")
                else:
                    try:
                        await error_callback(error)
                    except Exception:
                        logger.exception("booking_reminder_loop_diagnostics_failed_safely")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except TimeoutError:
                continue
    finally:
        _reminder_loop_status = ReminderLoopStatus(
            last_started_at=_reminder_loop_status.last_started_at,
            last_success_at=_reminder_loop_status.last_success_at,
            last_error_at=_reminder_loop_status.last_error_at,
            last_error_class=_reminder_loop_status.last_error_class,
            is_running=False,
        )
    logger.info("booking_reminder_loop_stopped")


def booking_reminder_keyboard(context: BookingNotificationContext) -> MaxInlineKeyboard | None:
    if context.notification_type == BOOKING_REMINDER_48H:
        return MaxInlineKeyboard.from_rows([
            [MaxButton(text="✅ Да, запись в силе", payload=f"brc:y:{context.yclients_record_id}")],
            [MaxButton(text="❌ Нет, отменить или перенести", payload=f"brc:n:{context.yclients_record_id}")],
        ])
    if context.notification_type == BOOKING_REMINDER_2H:
        return MaxInlineKeyboard.from_rows([
            [MaxButton(text="📅 Мои записи", payload="my_bookings:open")],
            [MaxButton(text="🏠 Главное меню", payload="nav:home")],
        ])
    return None


def _recipient(context: BookingNotificationContext) -> tuple[str, str | None]:
    if context.chat_id:
        return "chat", context.chat_id
    if context.max_user_id:
        return "user", context.max_user_id
    if context.platform_user_id:
        return "user", context.platform_user_id
    return "user", None


def _extract_record(payload: dict[str, Any] | list[Any] | Any) -> dict[str, Any]:
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


def _record_is_active(record: dict[str, Any]) -> bool:
    if _record_is_deleted(record):
        return False
    attendance = _clean(record.get("attendance") or record.get("visit_attendance"))
    if attendance in {"-1", "1"}:
        return False
    status = _clean(record.get("status") or record.get("record_status") or record.get("state")).lower()
    if status in {"deleted", "cancelled", "canceled", "cancel", "отменена", "отменено"}:
        return False
    return True


def _record_is_deleted(record: dict[str, Any]) -> bool:
    return _clean(record.get("deleted") or record.get("is_deleted")).lower() in {"1", "true", "yes"}


def _record_datetime(record: dict[str, Any], timezone_name: str) -> datetime | None:
    return _parse_datetime(record.get("datetime") or record.get("date"), timezone_name)


def _record_service_name(record: dict[str, Any]) -> str:
    services = record.get("services")
    if isinstance(services, list) and services and isinstance(services[0], dict):
        value = services[0].get("title") or services[0].get("name")
        if _clean(value):
            return _clean(value)
    return _clean(record.get("service_name") or record.get("service") or record.get("title")) or "услуга"


def _record_master_name(record: dict[str, Any]) -> str:
    staff = record.get("staff")
    if isinstance(staff, dict) and _clean(staff.get("name")):
        return _clean(staff.get("name"))
    return _clean(record.get("staff_name") or record.get("master_name") or record.get("master")) or "ваш мастер"


def _parse_datetime(value: Any, timezone_name: str) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    branch_timezone = _zoneinfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=branch_timezone)
    return parsed.astimezone(branch_timezone)


def _ensure_timezone(value: datetime, timezone_value: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone_value)
    return value.astimezone(timezone_value)


def _zoneinfo(timezone_name: str | None) -> ZoneInfo:
    return zoneinfo_or_default(timezone_name, flow="reminders", operation="_zoneinfo")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _calculate_telegram_confirmation_due(booking_datetime: datetime, now: datetime) -> datetime | None:
    time_to_visit = booking_datetime - now
    if time_to_visit <= timedelta(0):
        return None
    if time_to_visit >= timedelta(hours=48):
        return booking_datetime - timedelta(hours=48)
    if time_to_visit >= timedelta(hours=6):
        return booking_datetime - timedelta(hours=6)
    return now


def _calculate_telegram_2h_due(booking_datetime: datetime, now: datetime) -> datetime | None:
    if booking_datetime <= now:
        return None
    scheduled_local = booking_datetime - timedelta(hours=2)
    return scheduled_local


def _date_label_for(dt_local: datetime, now_local: datetime) -> str:
    delta_days = (dt_local.date() - now_local.date()).days
    if delta_days == 0:
        return "сегодня"
    if delta_days == 1:
        return "завтра"
    if delta_days == 2:
        return "послезавтра"
    return ""


def _first_name(fullname: str) -> str:
    return " ".join(_clean(fullname).split()).split(" ")[0] if _clean(fullname) else ""


def _record_client_name(record: dict[str, Any]) -> str:
    client = record.get("client")
    if isinstance(client, dict):
        name = _clean(client.get("name") or client.get("fullname"))
        if name:
            return name
    return _clean(record.get("fullname") or record.get("client_name"))


async def _resolve_branch_address(database_path: str) -> str | None:
    try:
        contacts = await ContactsService(YClientsSettingsRepository(database_path)).get_contacts()
    except Exception:
        logger.warning("MAX booking reminder diagnostic: skipped_reason=branch_address_unavailable error_class=unexpected", exc_info=True)
        return None
    return _clean(contacts.address) or None


def _record_skipped_reminders(database_path: str, *, attribution: Any, timezone_name: str, reason: str) -> None:
    schedule = {BOOKING_REMINDER_48H: None, BOOKING_REMINDER_2H: None}
    for notification_type in schedule:
        if get_notification_history(
            database_path,
            platform=PLATFORM_MAX,
            platform_user_id=attribution.platform_user_id,
            yclients_record_id=attribution.yclients_record_id,
            notification_type=notification_type,
        ):
            continue
        mark_notification_history_skipped(
            database_path,
            platform=PLATFORM_MAX,
            platform_user_id=attribution.platform_user_id,
            yclients_record_id=attribution.yclients_record_id,
            notification_type=notification_type,
            scheduled_for=None,
            reason=reason,
            metadata={"company_timezone": timezone_name},
        )
        _log_reminder_diagnostic(
            notification_type=notification_type,
            platform_user_id=attribution.platform_user_id,
            yclients_record_id=attribution.yclients_record_id,
            company_timezone=timezone_name,
            yclients_record_active=False,
            canceled_or_rescheduled=reason == "record_not_active",
            skipped_reason=reason,
        )


def _log_reminder_diagnostic(**fields: Any) -> None:
    allowed = {
        "reminder_type", "loop_enabled", "loop_interval_seconds", "branch_timezone",
        "now_branch_time", "due_window_start", "due_window_end", "yclients_records_checked",
        "due_candidates_count", "skipped_cancelled_count", "skipped_deleted_count",
        "skipped_past_count", "skipped_duplicate_count", "skipped_no_platform_mapping_count",
        "skipped_notifications_disabled_count", "skipped_blocked_count", "sent_count",
        "failed_count", "duration_ms",
    }
    safe_fields = {key: value for key, value in fields.items() if key in allowed}
    logger.info("MAX reminders diagnostic: %s", safe_fields)


# Backward-compatible alias for existing lightweight tests.
_keyboard_for_reminder = booking_reminder_keyboard
