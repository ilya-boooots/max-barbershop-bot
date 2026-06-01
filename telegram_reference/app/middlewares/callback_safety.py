from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, TelegramObject

from app.core.navigation import render_main_menu

logger = logging.getLogger(__name__)

STALE_ACTION_TEXT = "⚠️ Эта кнопка устарела. Откройте раздел заново."
STALE_STATE_TEXT = "⚠️ Данные шага устарели. Пожалуйста, начните заново."


def _is_stale_callback_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "query is too old",
            "query is invalid",
            "message to edit not found",
            "message is not modified",
            "message can't be edited",
            "there is no text in the message to edit",
            "message identifier is not specified",
        )
    )


class CallbackSafetyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        try:
            return await handler(event, data)
        except (KeyError, ValueError, IndexError, LookupError) as exc:
            state = data.get("state")
            state_name = await state.get_state() if state is not None else None
            fsm_data = await state.get_data() if state is not None else {}
            nav_stack = fsm_data.get("__nav_stack__", [])
            stack_len = len(nav_stack) if isinstance(nav_stack, list) else 0
            stack_top = nav_stack[-1] if isinstance(nav_stack, list) and nav_stack else None
            logger.warning(
                "callback_stale_or_invalid tg_id=%s data=%s state=%s fsm_keys=%s nav_len=%s nav_top=%s error=%s",
                event.from_user.id,
                event.data,
                state_name,
                sorted([str(key) for key in fsm_data.keys()]),
                stack_len,
                stack_top,
                type(exc).__name__,
            )
            if event.message:
                await event.message.answer(STALE_STATE_TEXT)
                await render_main_menu(event, event.from_user.id)
            return None
        except TelegramBadRequest as exc:
            if not _is_stale_callback_error(exc):
                raise
            state = data.get("state")
            state_name = await state.get_state() if state is not None else None
            fsm_data = await state.get_data() if state is not None else {}
            nav_stack = fsm_data.get("__nav_stack__", [])
            stack_len = len(nav_stack) if isinstance(nav_stack, list) else 0
            stack_top = nav_stack[-1] if isinstance(nav_stack, list) and nav_stack else None
            logger.warning(
                "callback_stale_telegram tg_id=%s data=%s state=%s fsm_keys=%s nav_len=%s nav_top=%s error=%s",
                event.from_user.id,
                event.data,
                state_name,
                sorted([str(key) for key in fsm_data.keys()]),
                stack_len,
                stack_top,
                str(exc),
            )
            if event.message:
                await event.message.answer(STALE_ACTION_TEXT)
                await render_main_menu(event, event.from_user.id)
            return None
        finally:
            try:
                await event.answer()
            except TelegramBadRequest:
                pass
            except Exception:
                logger.debug("callback answer failed", exc_info=True)
