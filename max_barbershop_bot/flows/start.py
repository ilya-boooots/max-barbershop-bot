"""Start flow handlers for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import RouterContext

START_TEXT = """Привет! 👋 Это MAX-версия бота барбершопа.

Скоро здесь появится запись, мои визиты, уведомления и связь с администратором."""


async def handle_bot_started(context: RouterContext) -> None:
    """Send the start text when a user opens the bot."""

    await context.send_text(START_TEXT)


async def handle_start(context: RouterContext) -> None:
    """Send the start text for the /start command."""

    await context.send_text(START_TEXT)
