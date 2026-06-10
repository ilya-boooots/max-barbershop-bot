"""Navigation helpers for MAX bot screens."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.registration import is_registered
from max_barbershop_bot.ui.screens import main_menu_screen, placeholder_screen, settings_menu_screen, staff_menu_screen


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


async def show_home(context: RouterContext) -> None:
    """Show the main menu and reset navigation, keeping unfinished registration required."""

    if not _is_current_user_registered(context):
        from max_barbershop_bot.flows.registration import start_registration

        await start_registration(context)
        return

    state.reset_to_home(_user_id(context), _chat_id(context))
    await render_screen(context, state.MAIN_MENU_SCREEN)


async def go_back(context: RouterContext) -> None:
    """Show the previous screen or the main menu when the stack is empty."""

    previous_screen = state.pop_previous_screen(_user_id(context), _chat_id(context))
    await render_screen(context, previous_screen or state.MAIN_MENU_SCREEN)


async def open_screen(context: RouterContext, screen_id: str) -> None:
    """Open a screen and remember the current screen for Back navigation."""

    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen != screen_id:
        state.push_screen(_user_id(context), _chat_id(context), current_screen)
    state.set_current_screen(_user_id(context), _chat_id(context), screen_id)
    await render_screen(context, screen_id)


async def render_screen(context: RouterContext, screen_id: str) -> None:
    """Render a known screen id."""

    if screen_id == state.MAIN_MENU_SCREEN:
        screen = main_menu_screen(_current_role(context))
    elif screen_id == state.STAFF_MENU_SCREEN:
        screen = staff_menu_screen(_current_role(context))
    elif screen_id in {
        state.SETTINGS_MENU_SCREEN,
        state.SETTINGS_CONTACTS_SCREEN,
        state.SETTINGS_NOTIFICATIONS_SCREEN,
        state.SETTINGS_DIAGNOSTICS_SCREEN,
    }:
        screen = settings_menu_screen(_current_role(context))
    else:
        screen = placeholder_screen()

    await context.send_text(screen.text, keyboard=screen.keyboard)


def _current_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"

    database_path = _database_path()
    users = UsersRepository(database_path)
    user = users.find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)
    if user is None:
        return "user"
    return StaffRolesRepository(database_path).get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _is_current_user_registered(context: RouterContext) -> bool:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return False
    user = UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)
    return is_registered(user)


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
