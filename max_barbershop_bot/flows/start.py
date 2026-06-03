"""Start flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows.registration import start_registration
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.registration import is_registered
from max_barbershop_bot.ui.screens import main_menu_screen
from max_barbershop_bot.ui.texts import START_GREETING_TEXT


async def handle_bot_started(context: RouterContext) -> None:
    """Send the start text when a user opens the bot."""

    await _show_start_screen(context)


async def handle_start(context: RouterContext) -> None:
    """Send the start text for the /start command."""

    await _show_start_screen(context)


async def _show_start_screen(context: RouterContext) -> None:
    """Create/update MAX identity and continue to registration or menu."""

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Попробуйте нажать /start ещё раз.")
        return

    repository = UsersRepository(_database_path())
    user = repository.create_or_update_user(
        platform=PLATFORM_MAX,
        platform_user_id=platform_user_id,
        max_user_id=context.event.max_user_id,
        chat_id=context.event.chat_id,
        first_name=context.event.first_name,
        last_name=context.event.last_name,
        username=context.event.username,
    )

    if not is_registered(user):
        await start_registration(context)
        return

    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    screen = main_menu_screen()
    await context.send_text(f"{START_GREETING_TEXT}\n\n{screen.text}", keyboard=screen.keyboard)


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
