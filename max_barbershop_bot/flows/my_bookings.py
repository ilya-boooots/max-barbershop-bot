"""My bookings flow for viewing future YClients records in MAX."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.my_bookings import (
    MY_BOOKINGS_LOAD_ERROR_TEXT,
    MY_BOOKINGS_NO_PROFILE_TEXT,
    MyBookingsLoadError,
    MyBookingsProfileMissingError,
    MyBookingsService,
    format_bookings_screen,
)
from max_barbershop_bot.ui.buttons import MENU_MY_BOOKINGS_PAYLOAD, my_bookings_keyboard

logger = logging.getLogger(__name__)


def register_my_bookings_routes(router: Router) -> None:
    """Register callbacks for the My bookings flow."""

    router.on_callback(MENU_MY_BOOKINGS_PAYLOAD, handle_my_bookings_open)


async def handle_my_bookings_open(context: RouterContext) -> None:
    """Open the real My bookings screen instead of the placeholder."""

    await context.answer_callback("Открываем ваши записи 📅")
    await _show_my_bookings(context)


async def _show_my_bookings(context: RouterContext) -> None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    current_screen = state.get_current_screen(platform_user_id, chat_id)
    if current_screen not in {state.MY_BOOKINGS_SCREEN, state.MY_BOOKINGS_EMPTY_SCREEN, state.MY_BOOKINGS_ERROR_SCREEN}:
        state.push_screen(platform_user_id, chat_id, current_screen)

    user = _current_user(context)
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        result = await service.get_future_bookings_for_user(user, platform_user_id=platform_user_id)
    except MyBookingsProfileMissingError:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_NO_PROFILE_TEXT, keyboard=my_bookings_keyboard())
        return
    except MyBookingsLoadError as exc:
        logger.warning(
            "My bookings screen failed: operation=show_my_bookings platform_user_id=%s "
            "user_exists=%s yclients_client_id_present=%s phone_present=%s error_class=%s",
            platform_user_id,
            user is not None,
            bool(user and user.yclients_client_id),
            bool(user and user.phone),
            type(exc).__name__,
        )
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_LOAD_ERROR_TEXT, keyboard=my_bookings_keyboard())
        return

    if result.is_empty:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_EMPTY_SCREEN)
        await context.send_text(format_bookings_screen([], timezone_name=result.branch_timezone), keyboard=my_bookings_keyboard(include_booking=True))
        return

    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_SCREEN)
    await context.send_text(format_bookings_screen(result.bookings, timezone_name=result.branch_timezone), keyboard=my_bookings_keyboard())


def _current_user(context: RouterContext):
    platform_user_id = _user_id(context)
    if not platform_user_id:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
