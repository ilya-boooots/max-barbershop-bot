from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update

from app.core.error_monitor import maybe_alert_error, record_error_event, send_dev_alert, short_where
from app.core.logging import format_log_context
from app.core.navigation import NAV_STACK_KEY
from app.repositories.diagnostics import log_bot_event

logger = logging.getLogger(__name__)

from app.ui.texts import GENERIC_ERROR

_USER_FRIENDLY_MESSAGE = GENERIC_ERROR


def _get_update_location(update: Optional[Update]) -> str:
    if update is None:
        return "unknown"
    event_type = getattr(update, "event_type", None)
    if event_type:
        return str(event_type)
    return type(update).__name__


def _extract_message(update: Optional[Update]) -> Optional[Message]:
    if update is None:
        return None
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None


def _extract_callback(update: Optional[Update]) -> Optional[CallbackQuery]:
    if update is None:
        return None
    return update.callback_query


def _extract_context(update: Optional[Update]) -> dict[str, str | int | None]:
    message = _extract_message(update)
    callback = _extract_callback(update)
    user = None
    if message and message.from_user:
        user = message.from_user
    elif callback and callback.from_user:
        user = callback.from_user
    return {
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "callback_data": callback.data[:120] if callback and callback.data else None,
        "message_text": (message.text[:120] if message and message.text else None),
        "chat_id": message.chat.id if message and message.chat else None,
        "action": callback.data[:120] if callback and callback.data else _get_update_location(update),
    }


def _render_traceback(exception: BaseException) -> str:
    return "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)).strip()


async def _notify_user(bot: Bot, update: Optional[Update]) -> bool:
    message = _extract_message(update)
    if message:
        await message.answer(_USER_FRIENDLY_MESSAGE)
        return True
    callback = _extract_callback(update)
    if callback and callback.from_user:
        await bot.send_message(callback.from_user.id, _USER_FRIENDLY_MESSAGE)
        return True
    return False


def _short(value: Any, limit: int = 160) -> str:
    text = str(value or "—")
    return text if len(text) <= limit else text[:limit] + "…"


def _format_nav_data(fsm_data: dict[str, Any]) -> str:
    nav_stack = fsm_data.get(NAV_STACK_KEY)
    if not isinstance(nav_stack, list) or not nav_stack:
        return "—"
    tail = nav_stack[-3:]
    return _short(tail, 500)


async def _send_generic_error_diagnostic(
    *,
    bot: Bot,
    update: Optional[Update],
    exception: BaseException,
    location: str,
    where: str,
    state: FSMContext | None,
) -> None:
    update_context = _extract_context(update)
    fsm_state = None
    fsm_data: dict[str, Any] = {}
    if state is not None:
        try:
            fsm_state = await state.get_state()
            raw_data = await state.get_data()
            if isinstance(raw_data, dict):
                fsm_data = raw_data
        except Exception:
            logger.exception("Failed to read FSM data for generic error diagnostic")

    user_id = update_context.get("user_id") or "—"
    username = update_context.get("username")
    username_text = f"@{username}" if username else "—"
    callback_data = _short(update_context.get("callback_data"), 180)
    message_text = _short(update_context.get("message_text"), 180)
    action = _short(update_context.get("action") or location, 180)
    tb_tail = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)[-5:]).strip()
    if len(tb_tail) > 1200:
        tb_tail = tb_tail[-1200:]
    current_screen = _format_nav_data(fsm_data)
    fsm_keys = ", ".join(sorted(str(key) for key in fsm_data.keys())[:30]) or "—"
    timestamp = datetime.now(timezone.utc).isoformat()

    text = (
        "🚨 Bot error\n"
        f"User saw: {_USER_FRIENDLY_MESSAGE}\n"
        "note: User saw generic error\n"
        f"timestamp_utc: {timestamp}\n"
        f"user_id: {user_id}\n"
        f"username: {username_text}\n"
        f"action: {action}\n"
        f"handler/location: {location}\n"
        f"where: {where}\n"
        f"callback_data: {callback_data}\n"
        f"message_text: {message_text}\n"
        f"state: {fsm_state or '—'}\n"
        f"current_screen/nav: {current_screen}\n"
        f"fsm_keys: {fsm_keys}\n"
        f"exception: {type(exception).__name__}: {_short(exception, 240)}\n"
        "traceback_last_5_lines:\n"
        f"{tb_tail or '—'}"
    )
    await send_dev_alert(bot, text)


def register_global_error_handler(dispatcher: Dispatcher) -> None:
    @dispatcher.errors()
    async def _global_error_handler(event: ErrorEvent, bot: Bot, state: FSMContext | None = None) -> bool:
        try:
            update = event.update
            location = _get_update_location(update)
            exception = event.exception
            exception_type = type(exception).__name__
            exception_message = str(exception) or "Unknown error"
            traceback_text = _render_traceback(exception)
            update_context = _extract_context(update)
            where = short_where(exception)
            context = format_log_context(update_type=location, handler="global_error", **update_context)
            logger.exception(
                "Unhandled error during update processing%s%s",
                " | " if context else "",
                context,
                exc_info=exception,
            )
            await log_bot_event(
                level="ERROR",
                source="exception",
                message=f"{exception_type}: {exception_message}",
                details={"location": location, "where": where, "traceback": traceback_text},
            )
            entry = await record_error_event(
                exception=exception,
                where=where,
                handler_name=location,
                context=update_context,
            )
            await maybe_alert_error(
                bot=bot,
                exception=exception,
                where=where,
                action=str(update_context.get("action") or location),
                user_id=update_context.get("user_id") if isinstance(update_context.get("user_id"), int) else None,
                fingerprint=entry.fingerprint,
                repeat_count=entry.count,
            )
            user_notified = False
            try:
                user_notified = await _notify_user(bot, update)
            except Exception:
                logger.exception("Failed to notify user about a critical error.")
            if user_notified:
                try:
                    await _send_generic_error_diagnostic(
                        bot=bot,
                        update=update,
                        exception=exception,
                        location=location,
                        where=where,
                        state=state,
                    )
                except Exception:
                    logger.exception("Failed to send generic error diagnostic to developer.")
            return True
        except Exception:
            logger.exception("Global error handler failed.")
            return True
