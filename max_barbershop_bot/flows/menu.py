"""Main menu flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.services.navigation import go_back, show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_CLIENTS_DIRECTORY_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
)

def register_menu_routes(router: Router) -> None:
    """Register main menu and navigation callback handlers."""

    router.on_callback(NAV_BACK_PAYLOAD, handle_nav_back)
    router.on_callback(NAV_HOME_PAYLOAD, handle_nav_home)
    router.on_callback(ADMIN_CLIENTS_DIRECTORY_PAYLOAD, handle_clients_directory_disabled)


async def show_main_menu(context: RouterContext) -> None:
    """Send the main menu screen and reset navigation."""

    await show_home(context)


async def handle_nav_back(context: RouterContext) -> None:
    """Handle the Back navigation callback."""

    await context.answer_callback()
    await go_back(context)


async def handle_nav_home(context: RouterContext) -> None:
    """Handle the Home navigation callback."""

    await context.answer_callback()
    await show_home(context)


async def handle_clients_directory_disabled(context: RouterContext) -> None:
    """Handle stale clients directory entry without exposing client data."""

    await context.answer_callback()
    await context.send_text("Этот раздел сейчас недоступен 🙏")
    await show_home(context)
