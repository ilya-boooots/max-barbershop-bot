"""MAX handlers for booking reminder confirmation callbacks."""

from __future__ import annotations

import logging
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.integrations.yclients.endpoints import cancel_booking, confirm_booking
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, load_active_yclients_settings

logger = logging.getLogger(__name__)

_CONFIRM_YES_PREFIX = "brc:y:"
_CONFIRM_NO_PREFIX = "brc:n:"
_CONFIRM_CANCEL_PREFIX = "brc:c:"
_CONFIRM_RESCHEDULE_PREFIX = "brc:r:"
_STALE_TEXT = "⚠️ Не удалось найти запись из этого уведомления. Откройте «Мои записи»."


def register_booking_reminder_routes(router: Router) -> None:
    """Register Telegram-parity reminder confirmation callbacks."""

    router.on_callback_prefix(_CONFIRM_YES_PREFIX, handle_confirm_yes)
    router.on_callback_prefix(_CONFIRM_NO_PREFIX, handle_confirm_no)
    router.on_callback_prefix(_CONFIRM_CANCEL_PREFIX, handle_cancel_from_reminder)
    router.on_callback_prefix(_CONFIRM_RESCHEDULE_PREFIX, handle_reschedule_from_reminder)


async def handle_confirm_yes(context: RouterContext) -> None:
    record_id = _record_id(context.event.callback_payload, _CONFIRM_YES_PREFIX)
    if not record_id:
        await _send_stale(context)
        return
    await _answer(context)
    if record_id.startswith("dev-test-"):
        await context.send_text("✅ Спасибо за ответ. Ваша запись подтверждена!")
        return
    try:
        settings = load_active_yclients_settings(YClientsSettingsRepository(_database_path()), operation="booking_reminder_confirm_yes")
        async with build_yclients_client_from_active_settings(settings) as client:
            await confirm_booking(client, company_id=settings.company_id, record_id=record_id)
        await context.send_text("✅ Спасибо за ответ. Ваша запись подтверждена!")
    except Exception:
        logger.warning("booking_reminder_confirm_yes_failed yclients_record_id=%s", record_id, exc_info=True)
        await context.send_text("⚠️ Не удалось подтвердить запись. Попробуйте позже.")


async def handle_confirm_no(context: RouterContext) -> None:
    record_id = _record_id(context.event.callback_payload, _CONFIRM_NO_PREFIX)
    if not record_id:
        await _send_stale(context)
        return
    await _answer(context)
    await context.send_text("Поняли. Что хотите сделать?", keyboard=_confirm_no_keyboard(record_id))


async def handle_cancel_from_reminder(context: RouterContext) -> None:
    record_id = _record_id(context.event.callback_payload, _CONFIRM_CANCEL_PREFIX)
    if not record_id:
        await _send_stale(context)
        return
    await _answer(context)
    if record_id.startswith("dev-test-"):
        await context.send_text("✅ Запись отменена.")
        return
    try:
        settings = load_active_yclients_settings(YClientsSettingsRepository(_database_path()), operation="booking_reminder_cancel")
        async with build_yclients_client_from_active_settings(settings) as client:
            await cancel_booking(client, company_id=settings.company_id, record_id=record_id)
        await context.send_text("✅ Запись отменена.")
    except Exception:
        logger.warning("booking_reminder_cancel_failed yclients_record_id=%s", record_id, exc_info=True)
        await context.send_text("⚠️ Не удалось отменить запись. Попробуйте позже.")


async def handle_reschedule_from_reminder(context: RouterContext) -> None:
    record_id = _record_id(context.event.callback_payload, _CONFIRM_RESCHEDULE_PREFIX)
    if not record_id:
        await _send_stale(context)
        return
    await _answer(context)
    await context.send_text("Откройте «Мои записи» и выберите перенос этой записи.")


def _confirm_no_keyboard(record_id: str) -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="❌ Отменить запись", payload=f"{_CONFIRM_CANCEL_PREFIX}{record_id}")],
            [MaxButton(text="🔁 Перенести запись", payload=f"{_CONFIRM_RESCHEDULE_PREFIX}{record_id}")],
            [MaxButton(text="⬅️ Назад", payload="my_bookings:open")],
            [MaxButton(text="🏠 Главное меню", payload="nav:home")],
        ]
    )


def _record_id(payload: str | None, prefix: str) -> str | None:
    if not isinstance(payload, str) or not payload.startswith(prefix):
        return None
    raw = payload.removeprefix(prefix).strip()
    return raw or None


async def _send_stale(context: RouterContext) -> None:
    await _answer(context)
    await context.send_text(_STALE_TEXT, keyboard=MaxInlineKeyboard.from_rows([[MaxButton(text="🏠 Главное меню", payload="nav:home")]]))


async def _answer(context: RouterContext) -> None:
    if context.event.callback_id:
        await context.answer_callback()


def _database_path() -> str:
    from os import getenv

    return getenv("MAX_BOT_DATABASE_PATH") or getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH
