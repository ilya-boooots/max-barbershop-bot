"""Navigation helpers for MAX bot screens."""

from __future__ import annotations

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.ui.screens import main_menu_screen, placeholder_screen


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


async def show_home(context: RouterContext) -> None:
    """Show the main menu and reset the navigation stack."""

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


async def render_screen(context: RouterContext, screen_id: str) -> None:
    """Render a known screen id."""

    if screen_id == state.MAIN_MENU_SCREEN:
        screen = main_menu_screen()
    else:
        screen = placeholder_screen()

    await context.send_text(screen.text, keyboard=screen.keyboard)
