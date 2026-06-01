import asyncio
import logging
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

from app.config import ConfigError, get_app_secret_key, get_config
from app.core.error_monitor import send_dev_alert
from app.core.crash_monitor import handle_unhandled_exception, setup_crash_monitor
from app.core.diagnostics_runtime import heartbeat_worker, on_shutdown_log, on_startup_log
from app.core.errors import register_global_error_handler
from app.core.logging import setup_logging
from app.db.sqlite import init_db
from app.handlers import router as handlers_router
from app.middlewares import AntiFloodMiddleware, CallbackSafetyMiddleware, DiagnosticsMiddleware
from app.integrations.yclients import set_shared_http_session
from app.repositories.users import ensure_protected_developer
from app.services.broadcast_sender import start_broadcast_sender, stop_broadcast_sender
from app.services.cancellation_recovery_sender import start_cancellation_recovery_sender, stop_cancellation_recovery_sender
from app.services.booking_reminders import start_booking_reminder_sender, stop_booking_reminder_sender


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    config = get_config()
    logger.info("Bootstrapping bot application")
    get_app_secret_key()
    bot: Bot | None = None
    heartbeat_task: asyncio.Task | None = None
    broadcast_sender_started = False
    cancellation_sender_started = False
    booking_reminder_started = False
    stop_event: asyncio.Event | None = None
    startup_complete = False
    dispatcher: Dispatcher | None = None
    try:
        bot = Bot(token=config.bot_token)
        # Explicitly clear commands in known scopes to prevent stale Telegram cache
        # from showing old commands (/status, /errors) after deploy.
        command_scopes = (
            BotCommandScopeDefault(),
            BotCommandScopeAllPrivateChats(),
            BotCommandScopeAllGroupChats(),
            BotCommandScopeAllChatAdministrators(),
        )
        for scope in command_scopes:
            await bot.delete_my_commands(scope=scope)
        await bot.set_my_commands(
            [
                BotCommand(command="menu", description="🏠 Главное меню"),
            ],
            scope=BotCommandScopeDefault(),
        )
        dispatcher = Dispatcher()
        http_session = aiohttp.ClientSession()
        dispatcher["http_session"] = http_session
        set_shared_http_session(dispatcher["http_session"])
        dispatcher.update.middleware(AntiFloodMiddleware())
        dispatcher.update.middleware(CallbackSafetyMiddleware())
        dispatcher.update.middleware(DiagnosticsMiddleware())
        register_global_error_handler(dispatcher)
        dispatcher.include_router(handlers_router)
        await init_db()
        await ensure_protected_developer()
        await setup_crash_monitor(bot)
        await on_startup_log(bot)
        startup_complete = True
        stop_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(heartbeat_worker(stop_event, bot))
        start_broadcast_sender(bot)
        broadcast_sender_started = True
        start_cancellation_recovery_sender(bot)
        cancellation_sender_started = True
        start_booking_reminder_sender(bot)
        booking_reminder_started = True
        await dispatcher.start_polling(bot)
    except Exception as exc:
        if startup_complete and bot is not None:
            logger.exception("Fatal runtime error in polling loop")
            await handle_unhandled_exception(bot, exc, location="main_polling")
        elif bot is not None:
            logger.exception("Fatal startup error before polling")
            await send_dev_alert(
                bot,
                (
                    "🚨 Критическая ошибка запуска\n"
                    f"🧠 Тип: {type(exc).__name__}\n"
                    f"💬 Кратко: {str(exc)[:200] or '—'}\n"
                    "📍 Где: main.startup"
                ),
            )
        raise
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        if stop_event is not None:
            stop_event.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if broadcast_sender_started:
            try:
                await stop_broadcast_sender()
            except Exception:
                logger.exception("Failed to stop broadcast sender")
        if cancellation_sender_started:
            try:
                await stop_cancellation_recovery_sender()
            except Exception:
                logger.exception("Failed to stop cancellation recovery sender")
        if booking_reminder_started:
            try:
                await stop_booking_reminder_sender()
            except Exception:
                logger.exception("Failed to stop booking reminder sender")
        if startup_complete:
            try:
                await on_shutdown_log()
            except Exception:
                logger.exception("Failed to write shutdown diagnostics")
        logger.info("Bot stopped")
        if bot is not None:
            await bot.session.close()
        http_session = dispatcher.get("http_session") if dispatcher is not None else None
        if http_session is not None and not http_session.closed:
            await http_session.close()
            logger.info("HTTP session closed successfully")


if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    try:
        setup_logging()
        logger = logging.getLogger(__name__)
        asyncio.run(main())
    except ConfigError as exc:
        logger.critical("Configuration validation failed: %s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:
        logger.exception("Fatal startup error")
        raise SystemExit(1) from exc
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
