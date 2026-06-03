"""Minimal standalone runtime for the MAX Barbershop Bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Iterable

from max_barbershop_bot.core.config import ConfigError, load_config
from max_barbershop_bot.core.events import normalize_update
from max_barbershop_bot.core.logging import configure_logging
from max_barbershop_bot.core.router import Router
from max_barbershop_bot.flows import create_router
from max_barbershop_bot.max_api.client import MaxApiClient, MaxApiError
from max_barbershop_bot.max_api.sender import MaxMessageSender

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


async def _run_dev_polling_runtime(client: MaxApiClient) -> None:
    """Run development/test Long Polling until graceful shutdown."""

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event, (signal.SIGINT, signal.SIGTERM))

    router = create_router()
    sender = MaxMessageSender(client)
    polling_task = asyncio.create_task(_poll_dev_updates(client, sender, router, stop_event))
    try:
        await stop_event.wait()
    finally:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass


async def _poll_dev_updates(
    client: MaxApiClient,
    sender: MaxMessageSender,
    router: Router,
    stop_event: asyncio.Event,
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

        for update in updates:
            try:
                event = normalize_update(update)
                await router.dispatch(event, sender)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("⚠️ MAX update processing failed safely")

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
        await _run_dev_polling_runtime(client)
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
