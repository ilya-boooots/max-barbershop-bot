"""Start flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.permissions import is_protected_developer
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows.registration import start_registration
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.registration import is_registered
from max_barbershop_bot.ui.screens import main_menu_screen


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

    staff_repository = StaffRolesRepository(_database_path())
    _ensure_protected_developer(context, staff_repository)

    if not is_registered(user):
        await start_registration(context)
        return

    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    role = staff_repository.get_highest_role(platform_user_id, platform=PLATFORM_MAX)
    screen = main_menu_screen(role, display_name=_menu_display_name(user, context))
    await context.send_text(screen.text, keyboard=screen.keyboard)


def _menu_display_name(user: object, context: RouterContext) -> str:
    """Return the display name used in the Telegram main menu prompt."""

    for value in (
        getattr(user, "display_name", None),
        getattr(user, "first_name", None),
        " ".join(
            part.strip()
            for part in (context.event.first_name or "", context.event.last_name or "")
            if part and part.strip()
        ),
    ):
        cleaned = " ".join(str(value or "").split()).strip()
        if cleaned:
            return cleaned
    return "гость"


def _ensure_protected_developer(
    context: RouterContext,
    staff_repository: StaffRolesRepository,
) -> None:
    dev_max_user_id = getenv("DEV_MAX_USER_ID", "").strip() or None
    if is_protected_developer(
        context.event.platform_user_id,
        dev_max_user_id,
        max_user_id=context.event.max_user_id,
    ):
        staff_repository.ensure_developer(
            context.event.platform_user_id or dev_max_user_id,
            assigned_by_platform_user_id=context.event.platform_user_id,
            platform=PLATFORM_MAX,
        )


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
