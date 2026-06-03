"""Start flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows.menu import show_main_menu


async def handle_bot_started(context: RouterContext) -> None:
    """Send the start text when a user opens the bot."""

    await show_main_menu(context)


async def handle_start(context: RouterContext) -> None:
    """Send the start text for the /start command."""

    await show_main_menu(context)
