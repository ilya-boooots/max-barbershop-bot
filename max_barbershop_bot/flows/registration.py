"""Registration flow handlers for the MAX bot."""

from __future__ import annotations

import asyncio

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.users import User, UsersRepository
from max_barbershop_bot.services.registration import (
    is_registered,
    save_registration_profile,
    validate_birthdate,
    validate_name,
)
from max_barbershop_bot.services.user_names import get_saved_user_display_name
from max_barbershop_bot.ui.texts import (
    REGISTRATION_BIRTHDATE_INVALID_TEXT,
    REGISTRATION_BIRTHDATE_TEXT,
    REGISTRATION_COMPLETE_TEXT,
    REGISTRATION_NAME_INVALID_TEXT,
    REGISTRATION_NAME_TEXT,
    REGISTRATION_STARTED_TEXT,
)

_NAME_KEY = "registration_name"
_BIRTHDATE_KEY = "registration_birthdate"


def register_registration_routes(router: Router) -> None:
    """Register screen-scoped text handlers for PR-031 registration."""

    router.on_screen_text(state.REGISTRATION_NAME_SCREEN, handle_name_input)
    router.on_screen_text(state.REGISTRATION_BIRTHDATE_SCREEN, handle_birthdate_input)


async def start_registration(context: RouterContext, *, force_first_step: bool = False) -> None:
    """Start or resume owner-approved name + birthdate registration."""

    del force_first_step
    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.clear_state_data(platform_user_id, chat_id)

    user = _find_current_user(platform_user_id)
    if is_registered(user):
        await _complete_registration(context, show_final_messages=False)
        return

    saved_name = get_saved_user_display_name(user) if user is not None else None
    if saved_name and user is not None and not user.birthdate:
        state.set_state_data_value(platform_user_id, chat_id, _NAME_KEY, saved_name)
        await _show_birthdate(context)
        return

    await _show_name(context)


async def handle_name_input(context: RouterContext) -> None:
    """Validate manual name and move to birthdate."""

    name = validate_name(context.event.text)
    if name is None:
        await context.send_text(REGISTRATION_NAME_INVALID_TEXT)
        return

    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _NAME_KEY, name)
    await _show_birthdate(context)


async def handle_birthdate_input(context: RouterContext) -> None:
    """Validate birthdate, persist name + birthdate, then open the existing main menu."""

    validation = validate_birthdate(context.event.text)
    if not validation.is_valid or validation.birthdate is None:
        await context.send_text(REGISTRATION_BIRTHDATE_INVALID_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return

    chat_id = context.event.chat_id
    state.set_state_data_value(platform_user_id, chat_id, _BIRTHDATE_KEY, validation.birthdate)
    name = state.get_state_data_value(platform_user_id, chat_id, _NAME_KEY)
    user = _find_current_user(platform_user_id)
    if not isinstance(name, str) and user is not None:
        name = get_saved_user_display_name(user)
    if not isinstance(name, str) or validate_name(name) is None:
        await _show_name(context)
        return

    repository = UsersRepository(_database_path())
    save_registration_profile(
        repository,
        platform_user_id=platform_user_id,
        first_name=name,
        birthdate=validation.birthdate,
    )
    await _complete_registration(context)


async def _show_name(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_NAME_SCREEN)
    await context.send_text(REGISTRATION_NAME_TEXT)


async def _show_birthdate(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_BIRTHDATE_SCREEN)
    await context.send_text(REGISTRATION_BIRTHDATE_TEXT)


async def _complete_registration(context: RouterContext, *, show_final_messages: bool = True) -> None:
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    if show_final_messages:
        await context.send_text(REGISTRATION_STARTED_TEXT)
        await asyncio.sleep(2)
        await context.send_text(REGISTRATION_COMPLETE_TEXT)
    from max_barbershop_bot.flows.menu import show_main_menu

    await show_main_menu(context)


def _find_current_user(platform_user_id: str | None) -> User | None:
    if platform_user_id is None:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id)


def _database_path() -> str:
    from os import getenv

    from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH

    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
