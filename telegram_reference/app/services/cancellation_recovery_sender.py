from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.integrations.yclients.service import get_yclients_credentials
from app.services.cancellation_recovery import process_pending_events

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None
_stop = asyncio.Event()


def start_cancellation_recovery_sender(bot: Bot) -> None:
    global _task
    if _task and not _task.done():
        return
    _stop.clear()
    _task = asyncio.create_task(_run(bot), name="cancellation-recovery-sender")


async def stop_cancellation_recovery_sender() -> None:
    global _task
    if not _task:
        return
    _stop.set()
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


async def _run(bot: Bot) -> None:
    while not _stop.is_set():
        try:
            creds, _ = await get_yclients_credentials()
            sent = await process_pending_events(bot, str(creds.company_id))
            if sent:
                logger.info("cancellation_recovery_sent_count=%s", sent)
        except Exception:
            logger.exception("cancellation_recovery_sender_loop_failed")
        await asyncio.sleep(30)
