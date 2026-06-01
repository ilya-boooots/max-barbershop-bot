from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.core.navigation import peek_screen
from app.repositories.diagnostics import log_user_event
from app.repositories.users import get_user, touch_user_activity, upsert_telegram_user

TRACKED_BUTTONS = {
    "📊 Информация по счету",
    "💳 Баланс счёта",
    "💳 Виртуальная карта",
    "📝 Оставить отзыв",
    "⭐️ Электронное меню",
    "📍 Контакты",
    "🧾 Операции",
    "🔍 Найти клиента / 📷 Сканировать QR",
    "📊 Отчёты",
    "👥 Персонал",
    "💬 Сообщения",
    "🛠️ Разработка: Диагностика",
}

logger = logging.getLogger(__name__)


class DiagnosticsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            if isinstance(event, CallbackQuery):
                await self._log_callback(event, data)
            elif isinstance(event, Message):
                await self._log_message(event, data)
        except Exception:
            logger.exception("Diagnostics middleware failed")
        return await handler(event, data)

    async def _log_callback(self, event: CallbackQuery, data: dict[str, Any]) -> None:
        await upsert_telegram_user(
            tg_id=event.from_user.id,
            username=event.from_user.username,
            name=event.from_user.full_name,
        )
        await touch_user_activity(event.from_user.id)
        db_user = await get_user(event.from_user.id)
        username = event.from_user.username or (db_user.get("display_name") if db_user else None)
        phone = db_user.get("phone") if db_user else None
        screen = await self._extract_screen(data)
        await log_user_event(
            user_id=event.from_user.id,
            username=username,
            phone=phone,
            event_type="callback",
            event_name=event.data or "unknown",
            screen=screen,
        )

    async def _log_message(self, event: Message, data: dict[str, Any]) -> None:
        if not event.from_user:
            return
        await upsert_telegram_user(
            tg_id=event.from_user.id,
            username=event.from_user.username,
            name=event.from_user.full_name,
        )
        await touch_user_activity(event.from_user.id)
        if not event.text:
            return
        text = event.text.strip()
        event_type = "message"
        event_name = ""
        if text.startswith("/start"):
            event_type = "command"
            event_name = "/start"
        elif text.startswith("/menu"):
            event_type = "command"
            event_name = "/menu"
        elif text in TRACKED_BUTTONS:
            event_name = text
        else:
            return
        db_user = await get_user(event.from_user.id)
        username = event.from_user.username or (db_user.get("display_name") if db_user else None)
        phone = db_user.get("phone") if db_user else None
        screen = await self._extract_screen(data)
        await log_user_event(
            user_id=event.from_user.id,
            username=username,
            phone=phone,
            event_type=event_type,
            event_name=event_name[:64],
            screen=screen,
        )

    async def _extract_screen(self, data: dict[str, Any]) -> str | None:
        state = data.get("state")
        if not isinstance(state, FSMContext):
            return None
        current = await peek_screen(state)
        if not current:
            return None
        return current[0]
