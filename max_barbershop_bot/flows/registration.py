"""Registration flow handlers for the MAX bot."""

from __future__ import annotations

import logging

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.users import User, UsersRepository
from max_barbershop_bot.services.registration import (
    contains_contact_attachment,
    extract_contact_phone,
    is_registered,
    mask_phone,
    normalize_phone,
    save_registration_profile,
    validate_birthdate,
    validate_name,
)
from max_barbershop_bot.ui.buttons import (
    REGISTRATION_BACK_PAYLOAD,
    REGISTRATION_HOME_PAYLOAD,
    REGISTRATION_NAME_NO_PAYLOAD,
    REGISTRATION_NAME_YES_PAYLOAD,
)
from max_barbershop_bot.ui.screens import (
    registration_birthdate_screen,
    registration_name_confirm_screen,
    registration_name_screen,
    registration_phone_screen,
)
from max_barbershop_bot.ui.texts import (
    REGISTRATION_BIRTHDATE_INVALID_TEXT,
    REGISTRATION_BIRTHDATE_TEXT,
    REGISTRATION_COMPLETE_TEXT,
    REGISTRATION_CONTACT_PHONE_MISSING_TEXT,
    REGISTRATION_NAME_INVALID_TEXT,
    REGISTRATION_PHONE_INVALID_TEXT,
    REGISTRATION_REQUIRED_TEXT,
    REGISTRATION_STARTED_TEXT,
)

logger = logging.getLogger(__name__)

_PHONE_KEY = "registration_phone"
_NAME_KEY = "registration_name"
_BIRTHDATE_KEY = "registration_birthdate"
_SUGGESTED_NAME_KEY = "registration_suggested_name"


def register_registration_routes(router: Router) -> None:
    """Register callbacks and screen-scoped text/contact handlers for registration."""

    router.on_callback(REGISTRATION_NAME_YES_PAYLOAD, handle_name_confirmed)
    router.on_callback(REGISTRATION_NAME_NO_PAYLOAD, handle_name_declined)
    router.on_callback(REGISTRATION_BACK_PAYLOAD, handle_registration_back)
    router.on_callback(REGISTRATION_HOME_PAYLOAD, handle_registration_home)
    router.on_screen_text(state.REGISTRATION_NAME_SCREEN, handle_name_input)
    router.on_screen_text(state.REGISTRATION_PHONE_SCREEN, handle_phone_input)
    router.on_screen_text(state.REGISTRATION_BIRTHDATE_SCREEN, handle_birthdate_input)


async def start_registration(context: RouterContext) -> None:
    """Continue a partial profile from the first missing Telegram-required field."""

    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.clear_state_data(platform_user_id, chat_id)

    user = _find_current_user(platform_user_id)
    if user is not None:
        if user.first_name:
            state.set_state_data_value(platform_user_id, chat_id, _NAME_KEY, user.first_name)
        if user.phone:
            state.set_state_data_value(platform_user_id, chat_id, _PHONE_KEY, user.phone)
        if user.birthdate:
            state.set_state_data_value(platform_user_id, chat_id, _BIRTHDATE_KEY, user.birthdate)

    if user is not None and is_registered(user):
        await _complete_registration(context, show_final_messages=False)
        return
    if user is not None and user.first_name and not user.phone:
        await _show_phone(context)
        return
    if user is not None and user.first_name and user.phone and not user.birthdate:
        await _show_birthdate(context)
        return
    await _show_name_confirm(context)


async def handle_name_confirmed(context: RouterContext) -> None:
    """Save suggested MAX profile name and continue to phone."""

    await context.answer_callback("✅ Да")
    name = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _SUGGESTED_NAME_KEY)
    name = validate_name(str(name or "")) or _suggested_name(context) or "Пользователь"
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _NAME_KEY, name)
    await _continue_after_name(context)


async def handle_name_declined(context: RouterContext) -> None:
    """Ask for manual name input."""

    await context.answer_callback("❌ Нет")
    await _show_manual_name(context)


async def handle_name_input(context: RouterContext) -> None:
    """Validate manual name and move to phone."""

    if context.event.text is None:
        await context.send_text("⛔️ Пожалуйста, отправьте имя текстом.")
        return
    name = validate_name(context.event.text)
    if name is None:
        await context.send_text(REGISTRATION_NAME_INVALID_TEXT)
        return

    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _NAME_KEY, name)
    await _continue_after_name(context)


async def handle_phone_input(context: RouterContext) -> None:
    """Validate a manual/contact phone and move to birthdate."""

    phone = extract_contact_phone(context.event.attachments)
    if phone is None and contains_contact_attachment(context.event.attachments):
        await context.send_text(REGISTRATION_CONTACT_PHONE_MISSING_TEXT)
        return

    phone = phone or normalize_phone(context.event.text)
    if phone is None:
        await context.send_text(REGISTRATION_PHONE_INVALID_TEXT)
        return

    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _PHONE_KEY, phone)
    logger.info("Registration phone accepted: user=%s phone=%s", context.event.platform_user_id, mask_phone(phone))
    await _continue_after_phone(context)


async def handle_birthdate_input(context: RouterContext) -> None:
    """Validate birthdate, persist profile and open role-aware main menu."""

    if context.event.text is None:
        await context.send_text("⛔️ Пожалуйста, отправьте дату рождения текстом в формате дд.мм.гггг.")
        return
    validation = validate_birthdate(context.event.text)
    if not validation.is_valid or validation.birthdate is None:
        await context.send_text(REGISTRATION_BIRTHDATE_INVALID_TEXT)
        await context.send_text(REGISTRATION_BIRTHDATE_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return

    chat_id = context.event.chat_id
    state.set_state_data_value(platform_user_id, chat_id, _BIRTHDATE_KEY, validation.birthdate)
    name = state.get_state_data_value(platform_user_id, chat_id, _NAME_KEY)
    phone = state.get_state_data_value(platform_user_id, chat_id, _PHONE_KEY)
    user = _find_current_user(platform_user_id)
    if not isinstance(name, str) and user is not None:
        name = user.first_name
    if not isinstance(phone, str) and user is not None:
        phone = user.phone
    if not isinstance(name, str) or not isinstance(phone, str):
        await context.send_text(REGISTRATION_REQUIRED_TEXT)
        await start_registration(context)
        return

    await _persist_from_state_and_complete(context)


async def handle_registration_back(context: RouterContext) -> None:
    """Navigate backward inside the registration flow without falling into main menu."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen == state.REGISTRATION_NAME_SCREEN:
        await _show_name_confirm(context)
        return
    if current_screen == state.REGISTRATION_PHONE_SCREEN:
        await _show_name_confirm(context)
        return
    if current_screen == state.REGISTRATION_BIRTHDATE_SCREEN:
        await _show_phone(context)
        return
    await _show_name_confirm(context)


async def handle_registration_home(context: RouterContext) -> None:
    """Keep unregistered users inside registration when they press Home."""

    await context.answer_callback(REGISTRATION_REQUIRED_TEXT)
    await context.send_text(REGISTRATION_REQUIRED_TEXT)
    await start_registration(context)


async def _continue_after_name(context: RouterContext) -> None:
    phone = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _PHONE_KEY)
    birthdate = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _BIRTHDATE_KEY)
    if not isinstance(phone, str):
        await _show_phone(context)
        return
    if not isinstance(birthdate, str):
        await _show_birthdate(context)
        return
    await _persist_from_state_and_complete(context)


async def _continue_after_phone(context: RouterContext) -> None:
    birthdate = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _BIRTHDATE_KEY)
    if not isinstance(birthdate, str):
        await _show_birthdate(context)
        return
    await _persist_from_state_and_complete(context)


async def _persist_from_state_and_complete(context: RouterContext) -> None:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return
    chat_id = context.event.chat_id
    name = state.get_state_data_value(platform_user_id, chat_id, _NAME_KEY)
    phone = state.get_state_data_value(platform_user_id, chat_id, _PHONE_KEY)
    birthdate = state.get_state_data_value(platform_user_id, chat_id, _BIRTHDATE_KEY)
    if not isinstance(name, str) or not isinstance(phone, str) or not isinstance(birthdate, str):
        await context.send_text(REGISTRATION_REQUIRED_TEXT)
        await start_registration(context)
        return
    repository = UsersRepository(_database_path())
    save_registration_profile(
        repository,
        platform_user_id=platform_user_id,
        phone=phone,
        first_name=name,
        birthdate=birthdate,
    )
    await _complete_registration(context)


async def _show_name_confirm(context: RouterContext) -> None:
    name = _suggested_name(context) or "Пользователь"
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SUGGESTED_NAME_KEY, name)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_NAME_CONFIRM_SCREEN)
    screen = registration_name_confirm_screen(name)
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_manual_name(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_NAME_SCREEN)
    screen = registration_name_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_phone(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_PHONE_SCREEN)
    screen = registration_phone_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_birthdate(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_BIRTHDATE_SCREEN)
    screen = registration_birthdate_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _complete_registration(context: RouterContext, *, show_final_messages: bool = True) -> None:
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    if show_final_messages:
        await context.send_text(REGISTRATION_STARTED_TEXT)
        await context.send_text(REGISTRATION_COMPLETE_TEXT)
    from max_barbershop_bot.flows.menu import show_main_menu

    await show_main_menu(context)


def _suggested_name(context: RouterContext) -> str | None:
    profile_name = " ".join(
        part.strip()
        for part in (context.event.first_name or "", context.event.last_name or "")
        if part and part.strip()
    )
    if profile_name:
        return validate_name(profile_name) or profile_name
    return None


def _find_current_user(platform_user_id: str | None) -> User | None:
    if platform_user_id is None:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id)


def _database_path() -> str:
    from os import getenv

    from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH

    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
