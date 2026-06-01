from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from app.core.error_monitor import maybe_alert_error, record_error_event, short_where
from app.repositories.diagnostics import log_bot_event

logger = logging.getLogger(__name__)


async def setup_crash_monitor(bot: Bot) -> None:
    loop = asyncio.get_running_loop()

    def _loop_exception_handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        current_loop.create_task(handle_loop_exception(bot, context))

    loop.set_exception_handler(_loop_exception_handler)


async def handle_unhandled_exception(bot: Bot, exc: BaseException, location: str = "main") -> None:
    short_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    await _process_crash(bot, location=location, exception=exc, traceback_text=short_traceback)



async def handle_loop_exception(bot: Bot, context: dict[str, Any]) -> None:
    exception = context.get("exception")
    if isinstance(exception, BaseException):
        traceback_text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        await _process_crash(bot, location="event_loop", exception=exception, traceback_text=traceback_text)
        return

    message = str(context.get("message") or "Unknown loop exception")
    fallback_exception = RuntimeError(message)
    await _process_crash(bot, location="event_loop", exception=fallback_exception, traceback_text=message)


async def _process_crash(bot: Bot, location: str, exception: BaseException, traceback_text: str) -> None:
    error_type = type(exception).__name__
    timestamp = datetime.now(timezone.utc).isoformat()
    where = short_where(exception)

    try:
        await log_bot_event(
            level="CRITICAL",
            source="crash_monitor",
            message=f"{error_type}: {exception}",
            details={
                "location": location,
                "where": where,
                "timestamp": timestamp,
                "traceback": traceback_text[-2000:],
            },
        )
    except Exception:
        logger.exception("Failed to persist crash event to database")

    try:
        entry = await record_error_event(
            exception=exception,
            where=where,
            handler_name=location,
            context={"action": location, "user_id": None},
        )
        await maybe_alert_error(
            bot=bot,
            exception=exception,
            where=where,
            action=location,
            user_id=None,
            fingerprint=entry.fingerprint,
            repeat_count=entry.count,
            startup=True,
        )
    except Exception:
        logger.exception("Failed to notify developer about crash")
