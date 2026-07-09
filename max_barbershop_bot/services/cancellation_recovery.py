"""Cancellation recovery funnel ported from Telegram business behavior."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
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
from max_barbershop_bot.ui.buttons import cancellation_recovery_keyboard
from max_barbershop_bot.ui.texts import CANCELLATION_RECOVERY_TEXT

logger = logging.getLogger(__name__)

CANCELLATION_RECOVERY_NOTIFICATION_TYPE = "cancellation_recovery"
CANCELLATION_RECOVERY_DELAY_HOURS = 2
CANCELLATION_RECOVERY_BOOKING_PAYLOAD = "cancel_recovery:"


def recovery_keyboard(event_id: int) -> MaxInlineKeyboard:
    """Build MAX CTA matching Telegram cancellation recovery buttons."""

    return cancellation_recovery_keyboard(event_id)


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
        scheduled_send_at_utc=scheduled_at,
        scheduled_at=scheduled_at,
        cancellation_detected_at_utc=now.astimezone(UTC).isoformat(),
        cancelled_booking_datetime_utc=now.astimezone(UTC).isoformat(),
        source="my_bookings_cancel",
        is_test=False,
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
    for event in repository.find_pending_to_send(now_iso, limit=limit):
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
    if not _clean(event.platform_user_id):
        repository.set_status(event.id, "failed", error_summary="no_telegram_mapping")
        return False
    history_existing = get_notification_history(
        database_path,
        platform=event.platform,
        platform_user_id=event.platform_user_id,
        yclients_record_id=event.yclients_record_id,
        notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
    )
    user = UsersRepository(database_path).find_by_platform_user_id(event.platform_user_id, platform=event.platform) if event.platform_user_id else None
    if user is not None and not user.notifications_enabled and not event.is_test:
        _skip(database_path, repository, event, "failed", "disabled")
        return False
    if history_existing and (history_existing.is_blocked or history_existing.is_stopped):
        _skip(database_path, repository, event, "skipped", "blocked_or_stopped")
        return False
    try:
        has_future = await _has_future_booking(database_path, user, event=event, platform_user_id=event.platform_user_id)
    except Exception as exc:  # noqa: BLE001 - Telegram marks failed when future-booking check fails.
        repository.set_status(event.id, "failed", error_summary="future_booking_check_failed")
        logger.warning("MAX cancellation recovery diagnostic: future_booking_check_failed error_class=%s", type(exc).__name__)
        return False
    if has_future:
        _skip(database_path, repository, event, "skipped_has_new_booking", "has_future_booking")
        return False
    recipient_id = _clean((user.max_user_id if user else None) or event.max_user_id or event.platform_user_id)
    chat_id = _clean((user.chat_id if user else None) or event.chat_id)
    if not recipient_id and not chat_id:
        repository.set_status(event.id, "failed", error_summary="no_telegram_mapping")
        return False
    recipient_type = "chat" if chat_id and not recipient_id else "user"
    recipient = chat_id if recipient_type == "chat" else recipient_id
    try:
        history = await send_business_notification(
            sender,
            database_path=database_path,
            platform=event.platform,
            platform_user_id=event.platform_user_id,
            max_user_id=(user.max_user_id if user else None) or event.max_user_id,
            chat_id=(user.chat_id if user else None) or event.chat_id,
            yclients_record_id=event.yclients_record_id,
            yclients_client_id=(user.yclients_client_id if user else None) or event.yclients_client_id,
            notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
            scheduled_for=event.effective_scheduled_send_at_utc,
            text=CANCELLATION_RECOVERY_TEXT,
            keyboard=recovery_keyboard(event.id),
            recipient_type=recipient_type,
            recipient_id=recipient,
            metadata={"source": event.source or "my_bookings_cancel", "is_test": event.is_test},
        )
    except Exception as exc:  # noqa: BLE001 - mark event, keep raw payloads out of logs.
        repository.set_status(event.id, "failed", error_summary=type(exc).__name__)
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
        repository.set_status(event.id, "sent", sent_at_utc=now_iso)
        sent = True
    else:
        repository.set_status(event.id, status, error_summary=status, skipped_reason=status)
        sent = False
    logger.info(
        "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s scheduled_at=%s due_now=%s history_existing=%s has_future_booking=%s send_attempted=%s send_status=%s blocked_or_stopped=%s",
        True,
        True,
        event.effective_scheduled_send_at_utc,
        True,
        bool(history_existing),
        bool(has_future),
        True,
        status,
        bool(history and (history.is_blocked or history.is_stopped)),
    )
    return sent


def _skip(database_path: str, repository: CancellationRecoveryEventsRepository, event: CancellationRecoveryEvent, status: str, reason: str) -> None:
    repository.set_status(event.id, status, error_summary=reason, skipped_reason=reason)
    mark_notification_history_skipped(
        database_path,
        platform=event.platform,
        platform_user_id=event.platform_user_id,
        yclients_record_id=event.yclients_record_id,
        notification_type=CANCELLATION_RECOVERY_NOTIFICATION_TYPE,
        scheduled_for=event.effective_scheduled_send_at_utc,
        reason=reason,
        metadata={"source": event.source or "my_bookings_cancel", "is_test": event.is_test},
    )
    logger.info(
        "MAX cancellation recovery diagnostic: platform_user_id_present=%s yclients_record_id_present=%s scheduled_at=%s due_now=%s send_attempted=%s skipped_reason=%s blocked_or_stopped=%s",
        True,
        True,
        event.effective_scheduled_send_at_utc,
        True,
        False,
        reason,
        reason == "blocked_or_stopped",
    )


async def _has_future_booking(database_path: str, user: object | None, *, event: CancellationRecoveryEvent, platform_user_id: str) -> bool:
    if not _clean(event.yclients_client_id) and user is None:
        return False
    try:
        result = await MyBookingsService(YClientsSettingsRepository(database_path)).get_future_bookings_for_user(user, platform_user_id=platform_user_id)
    except MyBookingsProfileMissingError:
        return False
    except MyBookingsLoadError as exc:
        raise RuntimeError("future_booking_check_failed") from exc
    return bool(result.bookings)


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
