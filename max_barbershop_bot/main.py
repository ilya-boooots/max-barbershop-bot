"""Minimal standalone runtime for the MAX Barbershop Bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Iterable

from max_barbershop_bot.core.config import Config, ConfigError, load_config
from max_barbershop_bot.core.error_handler import ErrorDiagnostics
from max_barbershop_bot.core.events import normalize_update
from max_barbershop_bot.core.logging import add_database_log_handler, configure_logging
from max_barbershop_bot.core.router import Router
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import create_router
from max_barbershop_bot.max_api.client import MaxApiClient, MaxApiError
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.services.birthday_funnel import run_birthday_loop
from max_barbershop_bot.services.cancellation_recovery import run_cancellation_recovery_loop
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.services.reminder_lifecycle import shutdown_reminder_lifecycle, start_reminder_lifecycle

logger = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event, signals: Iterable[signal.Signals]) -> None:
    """Ask the event loop to stop gracefully when the process receives a shutdown signal."""

    loop = asyncio.get_running_loop()
    for shutdown_signal in signals:
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
        except NotImplementedError:
            # Some platforms do not support asyncio signal handlers.
            continue


STARTUP_NOTIFICATION_TEXT = "✅ Бот запущен и активен"


async def _run_dev_polling_runtime(client: MaxApiClient, config: Config) -> None:
    """Run development/test Long Polling until graceful shutdown."""

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event, (signal.SIGINT, signal.SIGTERM))

    router = create_router(config)
    sender = MaxMessageSender(client)
    diagnostics = ErrorDiagnostics.from_config(config)
    polling_task = asyncio.create_task(_poll_dev_updates(client, sender, router, stop_event, diagnostics))
    birthday_task: asyncio.Task | None = None
    cancellation_recovery_task: asyncio.Task | None = None
    settings_repository = AppSettingsRepository(config.database_path)
    notifications_enabled = settings_repository.notifications_enabled()
    setting_source = settings_repository.notification_setting_source()
    if config.reminders_enabled and notifications_enabled:
        await start_reminder_lifecycle(
            sender,
            database_path=config.database_path,
            interval_seconds=config.reminders_poll_interval_seconds,
            error_callback=lambda error: diagnostics.handle_runtime_exception(
                exception=error,
                sender=sender,
                location="booking_reminder_loop",
            ),
        )
    elif not config.reminders_enabled:
        logger.info(
            "MAX notifications lifecycle diagnostic: %s",
            {
                "notifications_enabled": notifications_enabled,
                "setting_source": setting_source,
                "startup_attempted": False,
                "start_result": "disabled_by_reminders_enabled_env",
                "interval_seconds": config.reminders_poll_interval_seconds,
            },
        )
    else:
        logger.info(
            "MAX notifications lifecycle diagnostic: %s",
            {
                "notifications_enabled": False,
                "setting_source": setting_source,
                "startup_attempted": False,
                "start_result": "disabled_by_app_setting",
                "interval_seconds": config.reminders_poll_interval_seconds,
            },
        )
    if config.cancellation_recovery_enabled:
        cancellation_recovery_task = asyncio.create_task(
            run_cancellation_recovery_loop(
                sender,
                database_path=config.database_path,
                stop_event=stop_event,
                interval_seconds=config.cancellation_recovery_poll_interval_seconds,
                error_callback=lambda error: diagnostics.handle_runtime_exception(
                    exception=error,
                    sender=sender,
                    location="cancellation_recovery_loop",
                ),
            ),
            name="cancellation-recovery",
        )
    else:
        logger.info("Cancellation recovery disabled by CANCELLATION_RECOVERY_ENABLED")
    if config.birthday_funnel_enabled:
        birthday_task = asyncio.create_task(
            run_birthday_loop(
                sender,
                database_path=config.database_path,
                stop_event=stop_event,
                interval_seconds=config.birthday_funnel_poll_interval_seconds,
                error_callback=lambda error: diagnostics.handle_runtime_exception(
                    exception=error,
                    sender=sender,
                    location="birthday_funnel_loop",
                ),
            ),
            name="birthday-funnel",
        )
    else:
        logger.info("Birthday funnel disabled by BIRTHDAY_FUNNEL_ENABLED")
    try:
        await stop_event.wait()
    finally:
        polling_task.cancel()
        tasks = [polling_task]
        await shutdown_reminder_lifecycle()
        if birthday_task is not None:
            birthday_task.cancel()
            tasks.append(birthday_task)
        if cancellation_recovery_task is not None:
            cancellation_recovery_task.cancel()
            tasks.append(cancellation_recovery_task)
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


async def _poll_dev_updates(
    client: MaxApiClient,
    sender: MaxMessageSender,
    router: Router,
    stop_event: asyncio.Event,
    diagnostics: ErrorDiagnostics,
) -> None:
    """Receive MAX updates, normalize them and dispatch to flow handlers."""

    marker: int | None = None
    while not stop_event.is_set():
        try:
            updates, marker = await client.get_updates(
                limit=100,
                timeout=30,
                marker=marker,
            )
        except asyncio.CancelledError:
            raise
        except MaxApiError as error:
            logger.warning(
                "⚠️ MAX updates polling error: status=%s code=%s",
                error.status,
                error.code,
            )
            await _sleep_until_stop(stop_event, 1.0)
            continue
        except Exception as error:
            await diagnostics.handle_runtime_exception(
                exception=error,
                sender=sender,
                location="updates_polling",
            )
            await _sleep_until_stop(stop_event, 1.0)
            continue

        for update in updates:
            try:
                event = normalize_update(update)
                await router.dispatch(event, sender)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await diagnostics.handle_runtime_exception(
                    exception=error,
                    sender=sender,
                    location="update_processing",
                )

        if not updates:
            await _sleep_until_stop(stop_event, 0.1)


async def _send_startup_notification(client: MaxApiClient, dev_max_user_id: str | None) -> None:
    """Notify developer that the MAX bot runtime started successfully."""

    if dev_max_user_id is None:
        logger.info("DEV_MAX_USER_ID is not set; startup notification skipped")
        return

    user_id = _int_from_string(dev_max_user_id)
    if user_id is None:
        logger.warning("DEV_MAX_USER_ID is invalid; startup notification skipped")
        return

    try:
        await client.send_message(user_id=user_id, text=STARTUP_NOTIFICATION_TEXT)
        logger.info("✅ Startup notification sent to developer in MAX")
    except Exception as error:
        logger.warning(
            "⚠️ Startup notification failed safely: %s: %s",
            type(error).__name__,
            error,
        )


async def _sleep_until_stop(stop_event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        pass


def _int_from_string(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


async def run() -> None:
    """Bootstrap configuration, logging and the placeholder MAX runtime."""

    config = load_config()
    configure_logging(config.log_level)
    try:
        init_database(config.database_path)
        add_database_log_handler(config.database_path)
        logger.info("✅ SQLite database initialized: %s", config.database_path)
    except Exception:
        logger.exception("❌ SQLite database initialization failed: %s", config.database_path)
        raise

    client = MaxApiClient(config)
    logger.info(
        "🚀 MAX Barbershop Bot запускается: env=%s, dev_max_user_id_set=%s",
        config.app_env,
        config.dev_max_user_id is not None,
    )
    try:
        await client.start()
        startup_check_passed = False
        try:
            bot_info = await client.get_me()
            logger.info(
                "✅ MAX API авторизация проверена: bot_id=%s, username=%s",
                bot_info.get("user_id"),
                bot_info.get("username"),
            )
            startup_check_passed = True
        except MaxApiError as error:
            logger.warning(
                "⚠️ MAX API startup-check не пройден: status=%s code=%s",
                error.status,
                error.code,
            )
        if startup_check_passed:
            await _send_startup_notification(client, config.dev_max_user_id)
        else:
            logger.info("Startup notification skipped because MAX API startup-check failed")
        await _run_dev_polling_runtime(client, config)
    finally:
        await client.close()
        logger.info("🛑 MAX Barbershop Bot остановлен")


def main() -> int:
    """Run the application from the command line."""

    try:
        asyncio.run(run())
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("🛑 Получен KeyboardInterrupt, приложение остановлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
