"""Booking confirmation and reminder notifications for MAX users."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import DEFAULT_BRANCH_TIMEZONE, YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)
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
    BOOKING_REMINDER_6H: timedelta(hours=6),
    BOOKING_REMINDER_2H: timedelta(hours=2),
}
_NOTIFICATION_TYPE_LABELS = {
    BOOKING_CONFIRMATION_IMMEDIATE: "подтверждение записи",
    BOOKING_REMINDER_48H: "напоминание за 48 часов",
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
    yclients_client_id: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None
    scheduled_for: datetime | None = None


@dataclass(frozen=True)
class DueReminder:
    """One due reminder candidate verified against YClients."""

    context: BookingNotificationContext
    record: dict[str, Any]


def build_reminder_schedule(booking_datetime: datetime, timezone_name: str) -> dict[str, datetime]:
    """Return 48h/6h/2h scheduled moments in the branch timezone."""

    branch_timezone = _zoneinfo(timezone_name)
    local_dt = _ensure_timezone(booking_datetime, branch_timezone)
    return {
        notification_type: local_dt - offset
        for notification_type, offset in REMINDER_OFFSETS.items()
    }


def render_booking_notification_text(context: BookingNotificationContext, timezone_name: str) -> str:
    """Render Russian booking notification text for MAX."""

    branch_timezone = _zoneinfo(timezone_name)
    dt_local = _ensure_timezone(context.booking_datetime, branch_timezone)
    date_text = dt_local.strftime("%d.%m.%Y")
    time_text = dt_local.strftime("%H:%M")
    service_name = context.service_name or "услуга"
    master_name = context.master_name or "ваш мастер"

    if context.notification_type == BOOKING_CONFIRMATION_IMMEDIATE:
        return (
            "✅ Готово! Вы записаны 💈\n\n"
            f"Услуга: {service_name}\n"
            f"Мастер: {master_name}\n"
            f"Дата: {date_text}\n"
            f"Время: {time_text}"
        )
    if context.notification_type == BOOKING_REMINDER_48H:
        return (
            f"Напоминаем о записи через 48 часов ⏰\n\n"
            f"Услуга: {service_name}\n"
            f"Мастер: {master_name}\n"
            f"Дата: {date_text}\n"
            f"Время: {time_text}"
        )
    if context.notification_type == BOOKING_REMINDER_6H:
        return (
            "Напоминаем о записи сегодня ⏰\n\n"
            f"Услуга: {service_name}\n"
            f"Мастер: {master_name}\n"
            f"Время: {time_text}"
        )
    if context.notification_type == BOOKING_REMINDER_2H:
        return (
            "До вашей записи осталось 2 часа ⏰\n\n"
            f"Услуга: {service_name}\n"
            f"Мастер: {master_name}\n"
            f"Время: {time_text}"
        )
    raise ValueError(f"Неизвестный тип уведомления: {context.notification_type}")


async def send_booking_notification(
    sender: MaxMessageSender,
    *,
    database_path: str,
    context: BookingNotificationContext,
    timezone_name: str,
    keyboard: MaxInlineKeyboard | None = None,
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

    text = render_booking_notification_text(context, timezone_name)
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
            metadata={"label": _NOTIFICATION_TYPE_LABELS.get(context.notification_type)},
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
    )


async def get_due_reminders(
    *,
    database_path: str,
    now: datetime | None = None,
    timezone_name: str | None = None,
    limit: int = 200,
) -> list[DueReminder]:
    """Find due reminders from local attribution and verify each record in YClients."""

    settings = load_active_yclients_settings(
        YClientsSettingsRepository(database_path),
        operation="get_due_reminders",
    )
    if not has_required_yclients_credentials(settings):
        logger.info("booking_reminders_skipped_yclients_not_configured")
        return []

    branch_timezone_name = timezone_name or settings.branch_timezone or DEFAULT_BRANCH_TIMEZONE
    branch_timezone = _zoneinfo(branch_timezone_name)
    now_local = _ensure_timezone(now or datetime.now(UTC), branch_timezone)
    due: list[DueReminder] = []
    attributions = PlatformAttributionRepository(database_path).list_with_yclients_record_ids(limit=limit)
    if not attributions:
        return due

    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=settings.company_id)
        for attribution in attributions:
            if not attribution.yclients_record_id:
                continue
            user = UsersRepository(database_path).find_by_platform_user_id(attribution.platform_user_id, platform=attribution.platform)
            if user is None or not user.notifications_enabled:
                continue
            try:
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
                continue
            booking_datetime = _record_datetime(record, branch_timezone_name)
            if booking_datetime is None:
                continue
            booking_datetime = _ensure_timezone(booking_datetime, branch_timezone)
            if booking_datetime <= now_local:
                continue
            schedule = build_reminder_schedule(booking_datetime, branch_timezone_name)
            for notification_type, scheduled_for in schedule.items():
                if not (scheduled_for <= now_local < booking_datetime):
                    continue
                if get_notification_history(
                    database_path,
                    platform=PLATFORM_MAX,
                    platform_user_id=attribution.platform_user_id,
                    yclients_record_id=attribution.yclients_record_id,
                    notification_type=notification_type,
                ):
                    continue
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
                            scheduled_for=scheduled_for,
                        ),
                        record=record,
                    )
                )
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
    branch_timezone_name = timezone_name or (settings.branch_timezone if settings else DEFAULT_BRANCH_TIMEZONE)
    sent_or_recorded = 0
    for reminder in await get_due_reminders(database_path=database_path, now=now, timezone_name=branch_timezone_name):
        await send_booking_notification(
            sender,
            database_path=database_path,
            context=reminder.context,
            timezone_name=branch_timezone_name,
        )
        sent_or_recorded += 1
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

    interval = max(30, int(interval_seconds))
    logger.info("booking_reminder_loop_started interval_seconds=%s", interval)
    while not stop_event.is_set():
        try:
            count = await send_due_reminders(sender, database_path=database_path)
            if count:
                logger.info("booking_reminder_loop_processed count=%s", count)
        except asyncio.CancelledError:
            raise
        except Exception as error:
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
    logger.info("booking_reminder_loop_stopped")


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
    if _clean(record.get("deleted")).lower() in {"1", "true", "yes"}:
        return False
    attendance = _clean(record.get("attendance") or record.get("visit_attendance"))
    if attendance in {"-1", "1"}:
        return False
    status = _clean(record.get("status") or record.get("record_status") or record.get("state")).lower()
    if status in {"deleted", "cancelled", "canceled", "cancel", "отменена", "отменено"}:
        return False
    return True


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
    try:
        return ZoneInfo(timezone_name or DEFAULT_BRANCH_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("booking_reminders_invalid_timezone timezone=%s", timezone_name)
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""
