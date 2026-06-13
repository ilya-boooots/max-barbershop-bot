"""Main menu flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.services.navigation import go_back, show_home
from max_barbershop_bot.ui.buttons import (
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
)

def register_menu_routes(router: Router) -> None:
    """Register main menu and navigation callback handlers."""

    router.on_callback(NAV_BACK_PAYLOAD, handle_nav_back)
    router.on_callback(NAV_HOME_PAYLOAD, handle_nav_home)


async def show_main_menu(context: RouterContext) -> None:
    """Send the main menu screen and reset navigation."""

    await show_home(context)


async def handle_nav_back(context: RouterContext) -> None:
    """Handle the Back navigation callback."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    await go_back(context)


async def handle_nav_home(context: RouterContext) -> None:
    """Handle the Home navigation callback."""

    await context.answer_callback("Открываем главное меню 🏠")
    await show_home(context)
