from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app.config import get_protected_dev_tg_id
from app.repositories.diagnostics import log_bot_event

logger = logging.getLogger(__name__)

started_at_utc: datetime | None = None
last_heartbeat_utc: datetime | None = None
last_restart_utc: datetime | None = None


async def on_startup_log(bot: Bot) -> None:
    global started_at_utc, last_restart_utc
    started_at_utc = datetime.now(timezone.utc)
    last_restart_utc = started_at_utc
    await log_bot_event(level="INFO", source="startup", message="Бот запущен")
    try:
        await bot.send_message(get_protected_dev_tg_id(), "Бот запущен и активен")
    except Exception:
        logger.exception("Failed to send startup notification to developer")


async def on_shutdown_log() -> None:
    await log_bot_event(level="INFO", source="shutdown", message="Бот остановлен")


async def heartbeat_worker(stop_event: asyncio.Event, bot: Bot) -> None:
    del bot
    global last_heartbeat_utc
    while not stop_event.is_set():
        await asyncio.sleep(600)
        last_heartbeat_utc = datetime.now(timezone.utc)
        uptime = int((last_heartbeat_utc - (started_at_utc or last_heartbeat_utc)).total_seconds())
        await log_bot_event(
            level="INFO",
            source="healthcheck",
            message="Бот активен",
            details={
                "uptime_sec": uptime,
                "server_time_utc": last_heartbeat_utc.isoformat(),
            },
        )


def get_uptime_seconds() -> int:
    if not started_at_utc:
        return 0
    return int((datetime.now(timezone.utc) - started_at_utc).total_seconds())
