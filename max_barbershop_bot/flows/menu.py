"""Main menu flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.ui.buttons import MENU_PAYLOADS
from max_barbershop_bot.ui.screens import main_menu_screen
from max_barbershop_bot.ui.texts import SECTION_SOON_TEXT


def register_menu_routes(router: Router) -> None:
    """Register main menu callback handlers."""

    for payload in MENU_PAYLOADS:
        router.on_callback(payload, handle_menu_section)


async def show_main_menu(context: RouterContext) -> None:
    """Send the main menu screen."""

    screen = main_menu_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_menu_section(context: RouterContext) -> None:
    """Handle a temporary placeholder menu section callback."""

    await context.answer_callback(SECTION_SOON_TEXT)
