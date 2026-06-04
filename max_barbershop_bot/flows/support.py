"""Support flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_SUPPORT_USERNAME, normalize_support_username
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.ui.buttons import MENU_SUPPORT_PAYLOAD, navigation_keyboard
from max_barbershop_bot.ui.texts import SUPPORT_TEXT


def register_support_routes(router: Router) -> None:
    """Register support callbacks."""

    router.on_callback(MENU_SUPPORT_PAYLOAD, handle_support)


async def handle_support(context: RouterContext) -> None:
    """Open the static support screen from the main menu."""

    await context.answer_callback("Открываем поддержку 🆘")
    _open_support_state(context)
    await context.send_text(_support_text(), keyboard=navigation_keyboard())


def _open_support_state(context: RouterContext) -> None:
    max_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    current_screen = state.get_current_screen(max_user_id, chat_id)
    if current_screen != state.SUPPORT_SCREEN:
        state.push_screen(max_user_id, chat_id, current_screen)
    state.set_current_screen(max_user_id, chat_id, state.SUPPORT_SCREEN)


def _support_text() -> str:
    username = normalize_support_username(getenv("SUPPORT_USERNAME", DEFAULT_SUPPORT_USERNAME))
    return SUPPORT_TEXT.format(support_username=username)
