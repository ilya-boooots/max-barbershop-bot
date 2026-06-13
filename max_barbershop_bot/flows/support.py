"""Support flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.support_settings import (
    SupportSettings,
    SupportSettingsRepository,
    build_support_url,
    display_support_username,
    effective_support_settings,
)
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.ui.buttons import MENU_SUPPORT_PAYLOAD, navigation_keyboard, support_screen_keyboard

SUPPORT_LINK_FALLBACK_TEXT = "Если кнопка не открывается, скопируйте контакт из сообщения."
SUPPORT_MISSING_USERNAME_TEXT = "Контакт поддержки пока не настроен 🙏\n\nПожалуйста, вернитесь позже."


def register_support_routes(router: Router) -> None:
    """Register support callbacks."""

    router.on_callback(MENU_SUPPORT_PAYLOAD, handle_support)


async def handle_support(context: RouterContext) -> None:
    """Open the support screen from the main menu."""

    await context.answer_callback("Открываем поддержку 🆘")
    _open_support_state(context)
    settings = _resolve_support_settings()
    await context.send_text(render_support_message(settings), keyboard=_support_keyboard(settings))


def render_support_message(settings: SupportSettings) -> str:
    """Render Telegram-style support screen text with MAX fallback hints."""

    description = (settings.support_description or "").strip()
    username = display_support_username(settings.support_username)
    support_url = build_support_url(settings.support_username)
    if not username or not support_url:
        body = description or SUPPORT_MISSING_USERNAME_TEXT
        return f"🆘 Поддержка\n\n{body}"

    body = description or SUPPORT_MISSING_USERNAME_TEXT
    return f"🆘 Поддержка\n\n{body}\n\nКонтакт: {username}\nСсылка: {support_url}\n\n{SUPPORT_LINK_FALLBACK_TEXT}"


def _support_keyboard(settings: SupportSettings) -> MaxInlineKeyboard:
    support_url = build_support_url(settings.support_username)
    if support_url:
        return support_screen_keyboard(support_url=support_url)
    return navigation_keyboard()


def _open_support_state(context: RouterContext) -> None:
    max_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    current_screen = state.get_current_screen(max_user_id, chat_id)
    if current_screen != state.SUPPORT_SCREEN:
        state.push_screen(max_user_id, chat_id, current_screen)
    state.set_current_screen(max_user_id, chat_id, state.SUPPORT_SCREEN)


def _resolve_support_settings() -> SupportSettings:
    repository = SupportSettingsRepository(_database_path())
    return effective_support_settings(repository.get_active())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
