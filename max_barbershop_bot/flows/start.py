"""Start flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.ui.screens import main_menu_screen
from max_barbershop_bot.ui.texts import START_GREETING_TEXT


async def handle_bot_started(context: RouterContext) -> None:
    """Send the start text when a user opens the bot."""

    await _show_start_screen(context)


async def handle_start(context: RouterContext) -> None:
    """Send the start text for the /start command."""

    await _show_start_screen(context)


async def _show_start_screen(context: RouterContext) -> None:
    """Reset navigation and send greeting with the main menu keyboard."""

    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    screen = main_menu_screen()
    await context.send_text(f"{START_GREETING_TEXT}\n\n{screen.text}", keyboard=screen.keyboard)
