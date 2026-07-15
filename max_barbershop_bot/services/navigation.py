"""Navigation helpers for MAX bot screens."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import effective_role, is_protected_developer
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.support_settings import (
    SupportSettingsRepository,
    build_max_support_url,
    display_support_username,
    effective_support_settings,
)
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.registration import is_registered
from max_barbershop_bot.services.user_names import get_user_display_name, join_profile_name
from max_barbershop_bot.ui.buttons import booking_stale_keyboard, settings_support_keyboard, stale_screen_keyboard
from max_barbershop_bot.ui.screens import main_menu_screen, placeholder_screen, settings_menu_screen, staff_menu_screen
from max_barbershop_bot.ui.texts import BOOKING_STALE_CALLBACK_TEXT, STALE_SCREEN_TEXT


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



async def show_stale_callback(context: RouterContext) -> None:
    """Show a generic friendly stale callback screen."""

    await context.answer_callback("Экран устарел 🙏")
    await context.send_text(STALE_SCREEN_TEXT, keyboard=stale_screen_keyboard())


async def show_booking_stale_callback(context: RouterContext) -> None:
    """Show Telegram-style restart options for stale booking callbacks."""

    await context.answer_callback("Экран устарел 🙏")
    await context.send_text(BOOKING_STALE_CALLBACK_TEXT, keyboard=booking_stale_keyboard())


async def show_support_settings_editor(context: RouterContext) -> None:
    """Render the support settings editor from its normal and Back paths."""

    try:
        support_settings = effective_support_settings(
            SupportSettingsRepository(_database_path()).get_active()
        )
    except Exception:  # noqa: BLE001 - storage diagnostics must not leak into user-facing text.
        await context.send_text("⚠️ Не удалось загрузить настройки поддержки. Попробуйте ещё раз.")
        return
    username = display_support_username(support_settings.support_max_username) or "—"
    support_url = build_max_support_url(support_settings.support_max_username) or "—"
    text = (
        '🛠 Настройка раздела "Поддержка"\n\n'
        f"📝 Текущее описание:\n{support_settings.support_description}\n\n"
        f"👤 Текущий аккаунт: {username}\n"
        f"🔗 Ссылка: {support_url}"
    )
    state.set_current_screen(_user_id(context), _chat_id(context), state.SETTINGS_SUPPORT_SCREEN)
    await context.send_text(text, keyboard=settings_support_keyboard())


async def render_screen(context: RouterContext, screen_id: str) -> None:
    """Render a known screen id."""

    if screen_id == state.SETTINGS_SUPPORT_SCREEN:
        await show_support_settings_editor(context)
        return
    if screen_id == state.MAIN_MENU_SCREEN:
        user = _current_user(context)
        screen = main_menu_screen(
            _current_role(context, user),
            display_name=get_user_display_name(
                user,
                join_profile_name(context.event.first_name, context.event.last_name),
            ),
        )
    elif screen_id == state.STAFF_MENU_SCREEN:
        screen = staff_menu_screen(_current_role(context))
    elif screen_id in {
        state.SETTINGS_MENU_SCREEN,
        state.SETTINGS_CONTACTS_SCREEN,
        state.SETTINGS_NOTIFICATIONS_SCREEN,
        state.SETTINGS_DIAGNOSTICS_SCREEN,
    }:
        screen = settings_menu_screen(
            _current_role(context),
            protected_developer=_is_protected_developer(context),
        )
    else:
        screen = placeholder_screen()

    await context.send_text(screen.text, keyboard=screen.keyboard)


def _current_role(context: RouterContext, user: object | None = None) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"

    database_path = _database_path()
    if user is None:
        user = UsersRepository(database_path).find_by_platform_user_id(
            platform_user_id,
            platform=PLATFORM_MAX,
        )
    if user is None:
        return effective_role(
            None,
            platform_user_id=platform_user_id,
            dev_max_user_id=getenv("DEV_MAX_USER_ID"),
            max_user_id=context.event.max_user_id,
        )
    db_role = StaffRolesRepository(database_path).get_highest_role(platform_user_id, platform=PLATFORM_MAX)
    return effective_role(
        db_role,
        platform_user_id=platform_user_id,
        dev_max_user_id=getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )


def _current_user(context: RouterContext) -> object | None:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(
        platform_user_id,
        platform=PLATFORM_MAX,
    )


def _is_current_user_registered(context: RouterContext) -> bool:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return False
    user = UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)
    return is_registered(user)


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


def _is_protected_developer(context: RouterContext) -> bool:
    return is_protected_developer(
        context.event.platform_user_id,
        getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )
