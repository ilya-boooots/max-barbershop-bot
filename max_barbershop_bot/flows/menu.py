"""Main menu flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.services.navigation import go_back, open_screen, show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_BROADCASTS_PAYLOAD,
    ADMIN_SETTINGS_PAYLOAD,
    ADMIN_STAFF_PAYLOAD,
    ADMIN_STATISTICS_PAYLOAD,
    ADMIN_YCLIENTS_PAYLOAD,
    MENU_BOOKING_PAYLOAD,
    MENU_CONTACTS_PAYLOAD,
    MENU_MASTERS_PAYLOAD,
    MENU_MY_BOOKINGS_PAYLOAD,
    MENU_SUPPORT_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
)
from max_barbershop_bot.ui.texts import SECTION_SOON_TEXT

MENU_SCREENS = {
    MENU_BOOKING_PAYLOAD: state.BOOKING_PLACEHOLDER_SCREEN,
    MENU_MY_BOOKINGS_PAYLOAD: state.MY_BOOKINGS_PLACEHOLDER_SCREEN,
    MENU_MASTERS_PAYLOAD: state.MASTERS_PLACEHOLDER_SCREEN,
    MENU_CONTACTS_PAYLOAD: state.CONTACTS_PLACEHOLDER_SCREEN,
    MENU_SUPPORT_PAYLOAD: state.SUPPORT_PLACEHOLDER_SCREEN,
    ADMIN_STAFF_PAYLOAD: state.STAFF_MENU_SCREEN,
    ADMIN_SETTINGS_PAYLOAD: state.SETTINGS_PLACEHOLDER_SCREEN,
    ADMIN_BROADCASTS_PAYLOAD: state.BROADCASTS_PLACEHOLDER_SCREEN,
    ADMIN_STATISTICS_PAYLOAD: state.STATISTICS_PLACEHOLDER_SCREEN,
    ADMIN_YCLIENTS_PAYLOAD: state.YCLIENTS_PLACEHOLDER_SCREEN,
}


def register_menu_routes(router: Router) -> None:
    """Register main menu and navigation callback handlers."""

    for payload in MENU_SCREENS:
        router.on_callback(payload, handle_menu_section)
    router.on_callback(NAV_BACK_PAYLOAD, handle_nav_back)
    router.on_callback(NAV_HOME_PAYLOAD, handle_nav_home)


async def show_main_menu(context: RouterContext) -> None:
    """Send the main menu screen and reset navigation."""

    await show_home(context)


async def handle_menu_section(context: RouterContext) -> None:
    """Handle a temporary placeholder menu section callback."""

    await context.answer_callback(SECTION_SOON_TEXT)
    screen_id = MENU_SCREENS.get(context.event.callback_payload)
    if screen_id is None:
        return
    await open_screen(context, screen_id)


async def handle_nav_back(context: RouterContext) -> None:
    """Handle the Back navigation callback."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    await go_back(context)


async def handle_nav_home(context: RouterContext) -> None:
    """Handle the Home navigation callback."""

    await context.answer_callback("Открываем главное меню 🏠")
    await show_home(context)
