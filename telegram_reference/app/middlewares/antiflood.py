from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.config import get_settings

logger = logging.getLogger(__name__)

THROTTLE_TEXT = "⏳ Слишком часто. Подождите пару секунд 🙂"


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        settings = get_settings()
        self._msg_seconds = settings.throttle_msg_seconds
        self._cb_seconds = settings.throttle_cb_seconds
        self._global_msg = deque()
        self._global_cb = deque()
        self._per_user_msg: dict[int, float] = {}
        self._per_user_cb: dict[int, float] = {}
        self._global_msg_limit = 35
        self._global_cb_limit = 50

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            if self._is_throttled(event.from_user.id, self._msg_seconds, self._per_user_msg, self._global_msg, self._global_msg_limit):
                logger.info("throttle_event type=message tg_id=%s handler=%s", event.from_user.id, _handler_name(data))
                await event.answer(THROTTLE_TEXT)
                return None
        if isinstance(event, CallbackQuery) and event.from_user:
            if self._is_throttled(event.from_user.id, self._cb_seconds, self._per_user_cb, self._global_cb, self._global_cb_limit):
                logger.info("throttle_event type=callback tg_id=%s handler=%s", event.from_user.id, _handler_name(data))
                try:
                    await event.answer(THROTTLE_TEXT, show_alert=False)
                except Exception:
                    logger.debug("failed to answer throttled callback", exc_info=True)
                return None
        return await handler(event, data)

    @staticmethod
    def _is_throttled(
        user_id: int,
        interval_s: float,
        per_user: dict[int, float],
        global_events: deque[float],
        global_limit: int,
    ) -> bool:
        now = time.monotonic()
        while global_events and now - global_events[0] > 1.0:
            global_events.popleft()
        if len(global_events) >= global_limit:
            return True
        last = per_user.get(user_id, 0.0)
        if now - last < interval_s:
            return True
        per_user[user_id] = now
        global_events.append(now)
        return False


def _handler_name(data: dict[str, Any]) -> str:
    handler = data.get("handler")
    if handler is None:
        return "unknown"
    return getattr(handler, "__name__", handler.__class__.__name__)
