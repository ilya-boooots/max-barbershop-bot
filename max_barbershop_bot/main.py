"""Minimal standalone runtime for the MAX Barbershop Bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Iterable

from max_barbershop_bot.core.config import ConfigError, load_config
from max_barbershop_bot.core.logging import configure_logging

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


async def _run_placeholder_runtime() -> None:
    """Keep the application alive until a graceful shutdown is requested."""

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event, (signal.SIGINT, signal.SIGTERM))
    await stop_event.wait()


async def run() -> None:
    """Bootstrap configuration, logging and the placeholder MAX runtime."""

    config = load_config()
    configure_logging(config.log_level)

    logger.info("🚀 MAX Barbershop Bot запускается: env=%s, dev_tg_id=%s", config.app_env, config.dev_tg_id)
    try:
        await _run_placeholder_runtime()
    finally:
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
