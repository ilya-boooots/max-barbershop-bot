"""Registration flow handlers for the MAX bot."""

from __future__ import annotations

import logging

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.users import UsersRepository
from max_barbershop_bot.services.registration import (
    contains_contact_attachment,
    extract_contact_phone,
    mask_phone,
    normalize_phone,
    save_registration_profile,
    validate_name,
)
from max_barbershop_bot.ui.buttons import (
    REGISTRATION_BACK_PAYLOAD,
    REGISTRATION_CONSENT_ACCEPT_PAYLOAD,
    REGISTRATION_CONSENT_DECLINE_PAYLOAD,
    REGISTRATION_HOME_PAYLOAD,
)
from max_barbershop_bot.ui.screens import (
    registration_consent_screen,
    registration_name_screen,
    registration_phone_screen,
)
from max_barbershop_bot.ui.texts import (
    REGISTRATION_COMPLETE_TEXT,
    REGISTRATION_CONTACT_PHONE_MISSING_TEXT,
    REGISTRATION_DECLINED_TEXT,
    REGISTRATION_NAME_INVALID_TEXT,
    REGISTRATION_PHONE_INVALID_TEXT,
    REGISTRATION_REQUIRED_TEXT,
)

logger = logging.getLogger(__name__)

_CONSENT_KEY = "registration_consent_accepted"
_PHONE_KEY = "registration_phone"


def register_registration_routes(router: Router) -> None:
    """Register callbacks and screen-scoped text handlers for registration."""

    router.on_callback(REGISTRATION_CONSENT_ACCEPT_PAYLOAD, handle_consent_accept)
    router.on_callback(REGISTRATION_CONSENT_DECLINE_PAYLOAD, handle_consent_decline)
    router.on_callback(REGISTRATION_BACK_PAYLOAD, handle_registration_back)
    router.on_callback(REGISTRATION_HOME_PAYLOAD, handle_registration_home)
    router.on_screen_text(state.REGISTRATION_PHONE_SCREEN, handle_phone_input)
    router.on_screen_text(state.REGISTRATION_NAME_SCREEN, handle_name_input)


async def start_registration(context: RouterContext) -> None:
    """Reset temporary data and show the first registration screen."""

    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.clear_state_data(platform_user_id, chat_id)
    state.set_current_screen(platform_user_id, chat_id, state.REGISTRATION_CONSENT_SCREEN)
    screen = registration_consent_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_consent_accept(context: RouterContext) -> None:
    """Store consent in memory and ask the user for a phone number."""

    await context.answer_callback("Продолжаем регистрацию ✅")
    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.set_state_data_value(platform_user_id, chat_id, _CONSENT_KEY, True)
    state.push_screen(platform_user_id, chat_id, state.REGISTRATION_CONSENT_SCREEN)
    state.set_current_screen(platform_user_id, chat_id, state.REGISTRATION_PHONE_SCREEN)
    screen = registration_phone_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_consent_decline(context: RouterContext) -> None:
    """Stop registration when the user declines consent."""

    await context.answer_callback("Регистрация остановлена 🙏")
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(REGISTRATION_DECLINED_TEXT)


async def handle_phone_input(context: RouterContext) -> None:
    """Validate a manual/contact phone and move to the name step."""

    phone = extract_contact_phone(context.event.attachments)
    if phone is None and contains_contact_attachment(context.event.attachments):
        await context.send_text(REGISTRATION_CONTACT_PHONE_MISSING_TEXT)
        return

    phone = phone or normalize_phone(context.event.text)
    if phone is None:
        await context.send_text(REGISTRATION_PHONE_INVALID_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.set_state_data_value(platform_user_id, chat_id, _PHONE_KEY, phone)
    state.push_screen(platform_user_id, chat_id, state.REGISTRATION_PHONE_SCREEN)
    state.set_current_screen(platform_user_id, chat_id, state.REGISTRATION_NAME_SCREEN)
    logger.info("Registration phone accepted: user=%s phone=%s", platform_user_id, mask_phone(phone))
    screen = registration_name_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_name_input(context: RouterContext) -> None:
    """Validate name, save profile and open the main menu."""

    name = validate_name(context.event.text)
    if name is None:
        await context.send_text(REGISTRATION_NAME_INVALID_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return

    phone = state.get_state_data_value(platform_user_id, context.event.chat_id, _PHONE_KEY)
    if not isinstance(phone, str):
        await context.send_text(REGISTRATION_REQUIRED_TEXT)
        await _show_phone(context)
        return

    repository = UsersRepository(_database_path())
    save_registration_profile(
        repository,
        platform_user_id=platform_user_id,
        phone=phone,
        first_name=name,
    )
    state.reset_to_home(platform_user_id, context.event.chat_id)
    await context.send_text(REGISTRATION_COMPLETE_TEXT)
    from max_barbershop_bot.flows.menu import show_main_menu

    await show_main_menu(context)


async def handle_registration_back(context: RouterContext) -> None:
    """Navigate backward inside the registration flow without falling into main menu."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen == state.REGISTRATION_NAME_SCREEN:
        await _show_phone(context)
        return
    if current_screen == state.REGISTRATION_PHONE_SCREEN:
        await _show_consent(context)
        return
    await _show_consent(context)


async def handle_registration_home(context: RouterContext) -> None:
    """Keep unregistered users inside registration when they press Home."""

    await context.answer_callback(REGISTRATION_REQUIRED_TEXT)
    await context.send_text(REGISTRATION_REQUIRED_TEXT)
    await _show_consent(context)


async def _show_consent(context: RouterContext) -> None:
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.REGISTRATION_CONSENT_SCREEN,
    )
    screen = registration_consent_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_phone(context: RouterContext) -> None:
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.REGISTRATION_PHONE_SCREEN,
    )
    screen = registration_phone_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


def _database_path() -> str:
    from os import getenv

    from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH

    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
