"""Statistics flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_statistics, effective_role
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.statistics import (
    StatisticsLoadError,
    format_business_summary_text,
    get_business_summary,
)
from max_barbershop_bot.ui.buttons import (
    ADMIN_STATISTICS_PAYLOAD,
    statistics_result_keyboard,
)
from max_barbershop_bot.ui.texts import (
    STATISTICS_LOAD_ERROR_TEXT,
    STATISTICS_NO_ACCESS_TEXT,
)


def register_statistics_routes(router: Router) -> None:
    """Register statistics callbacks."""

    router.on_callback(ADMIN_STATISTICS_PAYLOAD, handle_statistics_menu)
    router.on_callback_prefix("stats:", handle_statistics_period)


async def handle_statistics_menu(context: RouterContext) -> None:
    """Open the Telegram-equivalent all-time statistics summary."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _show_summary(context)


async def handle_statistics_period(context: RouterContext) -> None:
    """Refresh the summary for legacy or repeated statistics callbacks."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _show_summary(context)


async def _show_summary(context: RouterContext) -> None:
    _push_current_screen(context, state.STATISTICS_RESULT_SCREEN)
    try:
        summary = await get_business_summary()
        text = format_business_summary_text(summary)
    except StatisticsLoadError:
        text = STATISTICS_LOAD_ERROR_TEXT
    await context.send_text(text, keyboard=statistics_result_keyboard())


def _can_access(context: RouterContext) -> bool:
    return can_view_statistics(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"
    db_role = StaffRolesRepository(_database_path()).get_highest_role(platform_user_id, platform=PLATFORM_MAX)
    return effective_role(
        db_role,
        platform_user_id=platform_user_id,
        dev_max_user_id=getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current != screen_id:
        state.push_screen(_user_id(context), _chat_id(context), current)
    state.set_current_screen(_user_id(context), _chat_id(context), screen_id)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, STATISTICS_NO_ACCESS_TEXT)
    await context.send_text(STATISTICS_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str | None = None) -> None:
    if context.event.callback_id:
        await context.answer_callback()


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
