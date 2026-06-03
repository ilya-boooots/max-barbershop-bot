"""Fallback flow handlers for unknown MAX user actions."""

from __future__ import annotations

from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.ui.texts import SECTION_SOON_TEXT, UNKNOWN_TEXT


async def handle_unknown_text(context: RouterContext) -> None:
    """Reply to a text command that is not registered yet."""

    await context.send_text(UNKNOWN_TEXT)


async def handle_unknown_callback(context: RouterContext) -> None:
    """Answer a callback button that is not registered yet."""

    await context.answer_callback(SECTION_SOON_TEXT)
