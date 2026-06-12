"""Fallback flow handlers for unknown MAX user actions."""

from __future__ import annotations

from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.services.navigation import show_stale_callback
from max_barbershop_bot.ui.buttons import stale_screen_keyboard
from max_barbershop_bot.ui.texts import UNKNOWN_TEXT


async def handle_unknown_text(context: RouterContext) -> None:
    """Reply to a text command that is not registered yet."""

    await context.send_text(UNKNOWN_TEXT, keyboard=stale_screen_keyboard())


async def handle_unknown_callback(context: RouterContext) -> None:
    """Answer an unknown or stale callback without mutating navigation state."""

    await show_stale_callback(context)
