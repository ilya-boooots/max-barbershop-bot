"""Birthday funnel ported from Telegram to MAX."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.birthday_funnel_events import (
    BIRTHDAY_NOTIFICATION_TYPE,
    BirthdayFunnelEventsRepository,
)
from max_barbershop_bot.repositories.users import User, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import CompanyTimeService
from max_barbershop_bot.services.notifications import PLATFORM_MAX, send_business_notification

logger = logging.getLogger(__name__)

BIRTHDAY_SEND_DAYS_BEFORE = 7
BIRTHDAY_BUTTON_CLAIM = "birthday_funnel:claim"
BIRTHDAY_BUTTON_BOOK = "birthday_funnel:book"
BIRTHDAY_MESSAGE_TEXT = (
    "Скоро ваш день рождения, поздравляем 🎉 😊\n\n"
    "Хотим сделать вам приятный подарок - покажите это сообщение администратору при оплате."
)
BIRTHDAY_WARNING = "У КЛИЕНТА ДЕНЬ РОЖДЕНИЕ - НУЖНО СДЕЛАТЬ СКИДКУ"
BIRTHDAY_BOOKING_BUTTON_TEXT = "✂️ Записаться"
BIRTHDAY_LOOP_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class BirthdayScanSummary:
    """Counters for one birthday funnel scan."""

    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


async def run_birthday_scan(sender: MaxMessageSender, *, database_path: str, force: bool = False, source: str = "local_db", is_test: bool = False) -> BirthdayScanSummary:
    """Send due birthday messages once per user per birthday year."""

    settings = _birthday_settings(database_path)
    if not settings.get("enabled", True) and not force:
        logger.info("birthday_scan_skipped_disabled")
        return BirthdayScanSummary()

    users_repo = UsersRepository(database_path)
    events_repo = BirthdayFunnelEventsRepository(database_path)
    company_time = CompanyTimeService(YClientsSettingsRepository(database_path))
    today = company_time.today()
    branch_timezone = company_time.get_branch_timezone_name()
    days_before = int(settings.get("send_days_before") or BIRTHDAY_SEND_DAYS_BEFORE)
    message_text = str(settings.get("message_text") or BIRTHDAY_MESSAGE_TEXT)
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
                days_before=days_before,
                source=source,
                is_test=is_test,
                message_text=message_text,
            )
            counters[outcome] += 1
        except Exception as exc:  # noqa: BLE001 - background funnel must not crash bot.
            counters["errors"] += 1
            logger.warning(
                "MAX birthday funnel diagnostic: platform_user_id_present=%s birthdate_present=%s "
                "birth_year=%s due_today=%s history_existing=%s send_attempted=%s send_status=%s "
                "delivery_status=%s blocked_or_stopped=%s error_class=%s",
                bool(user.platform_user_id), bool(user.birthdate), today.year, None, None, False, None, None, None, type(exc).__name__,
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
    days_before: int,
    source: str,
    is_test: bool,
    message_text: str = BIRTHDAY_MESSAGE_TEXT,
) -> str:
    birth_date = _parse_birth_date(user.birthdate)
    birth_year = today.year
    due_today = _is_due_today(birth_date, today, days_before=days_before)
    history_existing = bool(events_repo.find_by_client_year(user.platform_user_id, birth_year, is_test=is_test))
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
        event = events_repo.create_event(
            platform_user_id=user.platform_user_id,
            yclients_client_id=user.yclients_client_id,
            client_tg_id=user.platform_user_id,
            birth_date=birth_date.isoformat(),
            birthday_year=birth_year,
            scheduled_send_at_utc=datetime.now(UTC).isoformat(),
            status="pending",
            branch_timezone=branch_timezone,
            source=source,
            is_test=is_test,
        )
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
            text=message_text,
            keyboard=build_birthday_booking_keyboard(event.id),
            metadata={"birthday_year": birth_year, "branch_timezone": branch_timezone, "source": source, "is_test": is_test},
        )
        send_status = history.status if history is not None else "failed"
        if history and (history.is_blocked or history.is_stopped):
            send_status = "blocked"
        elif send_status not in {"sent", "blocked", "skipped"}:
            send_status = "failed"
        delivery_status = send_status
        blocked_or_stopped = bool(history and (history.is_blocked or history.is_stopped))
        events_repo.mark_status(event.id, send_status, sent=send_status == "sent", error_summary=None if send_status == "sent" else send_status)
        if send_status == "sent":
            return "sent"
        if send_status == "skipped":
            return "skipped"
        return "errors"
    except Exception as exc:
        error_class = type(exc).__name__
        if send_attempted and 'event' in locals() and event is not None:
            events_repo.mark_status(event.id, "failed", error_summary=str(exc)[:180])
        raise
    finally:
        logger.info(
            "MAX birthday funnel diagnostic: platform_user_id_present=%s birthdate_present=%s "
            "birth_year=%s due_today=%s history_existing=%s send_attempted=%s send_status=%s "
            "delivery_status=%s blocked_or_stopped=%s error_class=%s",
            bool(user.platform_user_id), bool(user.birthdate), birth_year, due_today, history_existing, send_attempted, send_status, delivery_status, blocked_or_stopped, error_class,
        )


def build_birthday_booking_keyboard(event_id: int) -> MaxInlineKeyboard:
    """Build Telegram-equivalent birthday booking CTA keyboard for MAX."""

    return MaxInlineKeyboard.from_rows([[MaxButton(text=BIRTHDAY_BOOKING_BUTTON_TEXT, payload=f"{BIRTHDAY_BUTTON_BOOK}:{event_id}")]])


def _parse_birth_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


_parse_birthdate = _parse_birth_date


def _is_due_today(birth_date: date | None, today: date, *, days_before: int = BIRTHDAY_SEND_DAYS_BEFORE) -> bool:
    if birth_date is None:
        return False
    day = min(birth_date.day, 28) if birth_date.month == 2 and birth_date.day == 29 else birth_date.day
    target = date(today.year, birth_date.month, day)
    return (target - today).days == days_before


def apply_birthday_warning(base_comment: str, *, booking_source: str | None, birthday_discount_context: bool = False) -> str:
    """Append birthday discount warning exactly once for birthday-funnel bookings."""

    if booking_source != "birthday_funnel" or not birthday_discount_context:
        return base_comment
    if BIRTHDAY_WARNING in base_comment:
        return base_comment
    return f"{base_comment}\n\n{BIRTHDAY_WARNING}"


def _birthday_settings(database_path: str) -> dict[str, object]:
    settings = AppSettingsRepository(database_path).get_automation_setting("birthday")
    return {
        "enabled": bool(settings.get("enabled", True)),
        "send_days_before": int(settings.get("send_days_before") or BIRTHDAY_SEND_DAYS_BEFORE),
        "message_text": str(settings.get("message_text") or BIRTHDAY_MESSAGE_TEXT),
        "gift_text": str(settings.get("gift_text") or ""),
    }


def _recipient(user: User) -> tuple[str, str | None]:
    if user.max_user_id:
        return "user", user.max_user_id
    if user.chat_id:
        return "chat", user.chat_id
    return "user", None
