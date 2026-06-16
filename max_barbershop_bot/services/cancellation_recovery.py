"""Cancellation recovery funnel ported from Telegram business behavior."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.cancellation_recovery_events import (
    CancellationRecoveryEvent,
    CancellationRecoveryEventsRepository,
)
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.my_bookings import MyBookingsLoadError, MyBookingsProfileMissingError, MyBookingsService
from max_barbershop_bot.services.notifications import (
    get_notification_history,
    mark_notification_history_skipped,
    send_business_notification,
)
from max_barbershop_bot.ui.buttons import MENU_BOOKING_PAYLOAD

logger = logging.getLogger(__name__)

CANCELLATION_RECOVERY_NOTIFICATION_TYPE = "cancellation_recovery"
CANCELLATION_RECOVERY_DELAY_HOURS = 2
CANCELLATION_RECOVERY_TEXT = "Видим, что вы отменили запись 😔\n\nМожем подобрать другое удобное время."
CANCELLATION_RECOVERY_BOOKING_PAYLOAD = "recovery:book"


def recovery_keyboard() -> MaxInlineKeyboard:
    """Build MAX CTA matching Telegram recovery intent with a short static payload."""

    return MaxInlineKeyboard.from_rows(
        [[MaxButton(text="✂️ Подобрать новое время", payload=CANCELLATION_RECOVERY_BOOKING_PAYLOAD)]]
    )


def create_cancellation_recovery_event(
    *,
    database_path: str,
    platform_user_id: str | None,
    yclients_record_id: str | None,
    user: object | None,
    cancelled_at: datetime | None = None,
) -> CancellationRecoveryEvent | None:
    """Create a delayed event after a successful MAX cancellation."""

    platform_user_id = _clean(platform_user_id)
    yclients_record_id = _clean(yclients_record_id)
    if not platform_user_id or not yclients_record_id:
        logger.info(
            "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s skipped_reason=%s",
            bool(platform_user_id),
            bool(yclients_record_id),
            "missing_identity",
        )
        return None
    now = cancelled_at or datetime.now(UTC)
    scheduled_at = (now.astimezone(UTC) + timedelta(hours=CANCELLATION_RECOVERY_DELAY_HOURS)).isoformat()
    event = CancellationRecoveryEventsRepository(database_path).create_event(
        platform=PLATFORM_MAX,
        platform_user_id=platform_user_id,
        yclients_record_id=yclients_record_id,
        yclients_client_id=_clean(getattr(user, "yclients_client_id", None)),
        max_user_id=_clean(getattr(user, "max_user_id", None)),
        chat_id=_clean(getattr(user, "chat_id", None)),
        scheduled_at=scheduled_at,
    )
    logger.info(
        "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s scheduled_at=%s",
        True,
        True,
        scheduled_at,
    )
    return event


async def process_due_cancellation_recovery_events(sender: MaxMessageSender, *, database_path: str, limit: int = 50) -> int:
    """Send due recovery events once, respecting preferences and duplicate history."""

    repository = CancellationRecoveryEventsRepository(database_path)
    now_iso = datetime.now(UTC).isoformat()
    sent = 0
    for event in repository.find_due(now_iso, limit=limit):
        if await _process_event(sender, database_path=database_path, repository=repository, event=event, now_iso=now_iso):
            sent += 1
    return sent


async def run_cancellation_recovery_loop(
    sender: MaxMessageSender,
    *,
    database_path: str,
    stop_event: asyncio.Event,
    interval_seconds: int,
    error_callback: object | None = None,
) -> None:
    """Poll due cancellation recovery events until shutdown."""

    while not stop_event.is_set():
        try:
            await process_due_cancellation_recovery_events(sender, database_path=database_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background loop must survive one bad event batch.
            if callable(error_callback):
                await error_callback(exc)
            else:
                logger.warning("MAX cancellation recovery diagnostic: error_class=%s", type(exc).__name__, exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(30, interval_seconds))
        except TimeoutError:
            continue


async def _process_event(
    sender: MaxMessageSender,
    *,
    database_path: str,
    repository: CancellationRecoveryEventsRepository,
    event: CancellationRecoveryEvent,
    now_iso: str,
) -> bool:
    history_existing = get_notification_history(
        database_path,
        platform=event.platform,
        platform_user_id=event.platform_user_id,
        yclients_record_id=event.yclients_record_id,
        notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
    )
    user = UsersRepository(database_path).find_by_platform_user_id(event.platform_user_id, platform=event.platform)
    if user is None:
        _skip(database_path, repository, event, "user_not_found")
        return False
    if not user.notifications_enabled:
        _skip(database_path, repository, event, "notifications_disabled")
        return False
    if history_existing and (history_existing.is_blocked or history_existing.is_stopped):
        _skip(database_path, repository, event, "blocked_or_stopped")
        return False
    has_future = await _has_future_booking(database_path, user, platform_user_id=event.platform_user_id)
    if has_future:
        _skip(database_path, repository, event, "has_future_booking")
        return False
    recipient_id = user.max_user_id or event.max_user_id or event.platform_user_id
    try:
        history = await send_business_notification(
            sender,
            database_path=database_path,
            platform=event.platform,
            platform_user_id=event.platform_user_id,
            max_user_id=user.max_user_id or event.max_user_id,
            chat_id=user.chat_id or event.chat_id,
            yclients_record_id=event.yclients_record_id,
            yclients_client_id=user.yclients_client_id or event.yclients_client_id,
            notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
            scheduled_for=event.scheduled_at,
            text=CANCELLATION_RECOVERY_TEXT,
            keyboard=recovery_keyboard(),
            recipient_type="user",
            recipient_id=recipient_id,
            metadata={"source": "my_bookings_cancel"},
        )
    except Exception as exc:  # noqa: BLE001 - mark event, keep raw payloads out of logs.
        repository.set_status(event.id, "failed", skipped_reason=type(exc).__name__)
        logger.warning(
            "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s due_now=%s history_existing=%s send_attempted=%s send_status=%s error_class=%s",
            True,
            True,
            True,
            bool(history_existing),
            True,
            "failed",
            type(exc).__name__,
        )
        return False
    status = history.status if history else "skipped"
    if status == "sent":
        repository.set_status(event.id, "sent", sent_at=now_iso)
        sent = True
    else:
        repository.set_status(event.id, status, skipped_reason=status)
        sent = False
    logger.info(
        "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s scheduled_at=%s due_now=%s history_existing=%s has_future_booking=%s send_attempted=%s send_status=%s blocked_or_stopped=%s",
        True,
        True,
        event.scheduled_at,
        True,
        bool(history_existing),
        bool(has_future),
        True,
        status,
        bool(history and (history.is_blocked or history.is_stopped)),
    )
    return sent


def _skip(database_path: str, repository: CancellationRecoveryEventsRepository, event: CancellationRecoveryEvent, reason: str) -> None:
    repository.set_status(event.id, "skipped", skipped_reason=reason)
    mark_notification_history_skipped(
        database_path,
        platform=event.platform,
        platform_user_id=event.platform_user_id,
        yclients_record_id=event.yclients_record_id,
        notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
        scheduled_for=event.scheduled_at,
        reason=reason,
        metadata={"source": "my_bookings_cancel"},
    )
    logger.info(
        "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s scheduled_at=%s due_now=%s send_attempted=%s skipped_reason=%s blocked_or_stopped=%s",
        True,
        True,
        event.scheduled_at,
        True,
        False,
        reason,
        reason == "blocked_or_stopped",
    )


async def _has_future_booking(database_path: str, user: object, *, platform_user_id: str) -> bool:
    try:
        result = await MyBookingsService(YClientsSettingsRepository(database_path)).get_future_bookings_for_user(user, platform_user_id=platform_user_id)
    except (MyBookingsProfileMissingError, MyBookingsLoadError):
        return False
    return bool(result.bookings)


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
