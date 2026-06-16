"""Birthday funnel ported from Telegram to MAX."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.birthday_funnel_events import (
    BIRTHDAY_NOTIFICATION_TYPE,
    BirthdayFunnelEventsRepository,
)
from max_barbershop_bot.repositories.users import User, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import CompanyTimeService
from max_barbershop_bot.services.notifications import PLATFORM_MAX, send_business_notification
from max_barbershop_bot.ui.buttons import MENU_BOOKING_PAYLOAD

logger = logging.getLogger(__name__)

BIRTHDAY_SEND_DAYS_BEFORE = 7
BIRTHDAY_MESSAGE_TEXT = (
    "Скоро ваш день рождения, поздравляем 🎉 😊\n\n"
    "Хотим сделать вам приятный подарок - покажите это сообщение администратору при оплате."
)
BIRTHDAY_BOOKING_BUTTON_TEXT = "✂️ Записаться"
BIRTHDAY_LOOP_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class BirthdayScanSummary:
    """Counters for one birthday funnel scan."""

    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


async def run_birthday_scan(sender: MaxMessageSender, *, database_path: str) -> BirthdayScanSummary:
    """Send due birthday messages once per user per birthday year."""

    users_repo = UsersRepository(database_path)
    events_repo = BirthdayFunnelEventsRepository(database_path)
    company_time = CompanyTimeService(YClientsSettingsRepository(database_path))
    today = company_time.today()
    branch_timezone = company_time.get_branch_timezone_name()
    summary = BirthdayScanSummary()

    for user in users_repo.list_birthday_candidates(platform=PLATFORM_MAX):
        counters = summary.__dict__.copy()
        try:
            outcome = await _process_user(
                sender,
                database_path=database_path,
                events_repo=events_repo,
                user=user,
                today=today,
                branch_timezone=branch_timezone,
            )
            counters[outcome] += 1
        except Exception as exc:  # noqa: BLE001 - background funnel must not crash bot.
            counters["errors"] += 1
            logger.warning(
                "MAX birthday funnel diagnostic: platform_user_id_present=%s birthdate_present=%s "
                "birth_year=%s due_today=%s history_existing=%s send_attempted=%s send_status=%s "
                "delivery_status=%s blocked_or_stopped=%s error_class=%s",
                bool(user.platform_user_id),
                bool(user.birthdate),
                today.year,
                None,
                None,
                False,
                None,
                None,
                None,
                type(exc).__name__,
            )
        summary = BirthdayScanSummary(**counters)
    return summary


async def run_birthday_loop(
    sender: MaxMessageSender,
    *,
    database_path: str,
    stop_event: asyncio.Event,
    interval_seconds: int = BIRTHDAY_LOOP_INTERVAL_SECONDS,
    error_callback: object | None = None,
) -> None:
    """Run birthday checks on the existing background-task model."""

    while not stop_event.is_set():
        try:
            await run_birthday_scan(sender, database_path=database_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - scheduler must continue after diagnostics.
            if callable(error_callback):
                await error_callback(exc)
            else:
                logger.warning("MAX birthday funnel diagnostic: error_class=%s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(60, int(interval_seconds)))
        except TimeoutError:
            pass


async def _process_user(
    sender: MaxMessageSender,
    *,
    database_path: str,
    events_repo: BirthdayFunnelEventsRepository,
    user: User,
    today: date,
    branch_timezone: str,
) -> str:
    birth_date = _parse_birthdate(user.birthdate)
    birth_year = today.year
    due_today = _is_due_today(birth_date, today)
    history_existing = bool(events_repo.find_by_user_year(user.platform_user_id, birth_year))
    send_attempted = False
    send_status: str | None = None
    delivery_status: str | None = None
    blocked_or_stopped = False
    error_class: str | None = None

    try:
        if not user.notifications_enabled or birth_date is None or not due_today or history_existing:
            return "skipped"
        recipient_type, recipient_id = _recipient(user)
        if recipient_id is None:
            return "skipped"
        event = events_repo.create_pending(user.platform_user_id, birth_year)
        if event is None:
            return "skipped"
        send_attempted = True
        history = await send_business_notification(
            sender,
            database_path=database_path,
            platform=PLATFORM_MAX,
            platform_user_id=user.platform_user_id,
            max_user_id=user.max_user_id,
            chat_id=user.chat_id,
            yclients_client_id=user.yclients_client_id,
            yclients_record_id=f"birthday:{birth_year}",
            notification_type=BIRTHDAY_NOTIFICATION_TYPE,
            scheduled_for=datetime.now(UTC).isoformat(),
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            text=BIRTHDAY_MESSAGE_TEXT,
            keyboard=_birthday_keyboard(),
            metadata={"birthday_year": birth_year, "branch_timezone": branch_timezone},
        )
        send_status = history.status if history is not None else "failed"
        delivery_status = send_status
        blocked_or_stopped = bool(history and (history.is_blocked or history.is_stopped))
        events_repo.mark_status(event.id, send_status, sent=send_status == "sent")
        return "sent" if send_status == "sent" else "errors"
    except Exception as exc:
        error_class = type(exc).__name__
        raise
    finally:
        logger.info(
            "MAX birthday funnel diagnostic: platform_user_id_present=%s birthdate_present=%s "
            "birth_year=%s due_today=%s history_existing=%s send_attempted=%s send_status=%s "
            "delivery_status=%s blocked_or_stopped=%s error_class=%s",
            bool(user.platform_user_id),
            bool(user.birthdate),
            birth_year,
            due_today,
            history_existing,
            send_attempted,
            send_status,
            delivery_status,
            blocked_or_stopped,
            error_class,
        )


def _parse_birthdate(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_due_today(birth_date: date | None, today: date) -> bool:
    if birth_date is None:
        return False
    day = min(birth_date.day, 28) if birth_date.month == 2 and birth_date.day == 29 else birth_date.day
    target = date(today.year, birth_date.month, day)
    return (target - today).days == BIRTHDAY_SEND_DAYS_BEFORE


def _recipient(user: User) -> tuple[str, str | None]:
    if user.max_user_id:
        return "user", user.max_user_id
    if user.chat_id:
        return "chat", user.chat_id
    return "user", None


def _birthday_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([[MaxButton(text=BIRTHDAY_BOOKING_BUTTON_TEXT, payload=MENU_BOOKING_PAYLOAD)]])
