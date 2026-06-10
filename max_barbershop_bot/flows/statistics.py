"""Statistics flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_statistics
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.services.statistics import (
    StatisticsLoadError,
    StatisticsSettingsMissingError,
    format_statistics_text,
    get_statistics_for_period,
)
from max_barbershop_bot.ui.buttons import (
    ADMIN_STATISTICS_PAYLOAD,
    STATISTICS_7_DAYS_PAYLOAD,
    STATISTICS_30_DAYS_PAYLOAD,
    STATISTICS_90_DAYS_PAYLOAD,
    STATISTICS_BACK_PAYLOAD,
    STATISTICS_HOME_PAYLOAD,
    STATISTICS_TODAY_PAYLOAD,
    statistics_period_keyboard,
    statistics_result_keyboard,
)
from max_barbershop_bot.ui.texts import (
    STATISTICS_LOAD_ERROR_TEXT,
    STATISTICS_MENU_TEXT,
    STATISTICS_NO_ACCESS_TEXT,
    STATISTICS_NOT_CONFIGURED_TEXT,
)

_PERIODS: dict[str, tuple[int | None, str]] = {
    STATISTICS_TODAY_PAYLOAD: (None, "Сегодня"),
    STATISTICS_7_DAYS_PAYLOAD: (7, "7 дней"),
    STATISTICS_30_DAYS_PAYLOAD: (30, "30 дней"),
    STATISTICS_90_DAYS_PAYLOAD: (90, "90 дней"),
}


def register_statistics_routes(router: Router) -> None:
    """Register statistics callbacks."""

    router.on_callback(ADMIN_STATISTICS_PAYLOAD, handle_statistics_menu)
    for payload in _PERIODS:
        router.on_callback(payload, handle_statistics_period)
    router.on_callback(STATISTICS_BACK_PAYLOAD, handle_statistics_back)
    router.on_callback(STATISTICS_HOME_PAYLOAD, handle_statistics_home)


async def handle_statistics_menu(context: RouterContext) -> None:
    """Open statistics period selection for allowed roles."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем статистику 📊")
    _push_current_screen(context, state.STATISTICS_PERIOD_SCREEN)
    await _show_period_selection(context)


async def handle_statistics_period(context: RouterContext) -> None:
    """Load and show statistics for a selected period."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload or ""
    period = _PERIODS.get(payload)
    if period is None:
        return

    await _answer_callback_if_needed(context, "Загружаем статистику 📊")
    days, label = period
    try:
        result = await get_statistics_for_period(days, label)
    except StatisticsSettingsMissingError:
        await context.send_text(STATISTICS_NOT_CONFIGURED_TEXT, keyboard=statistics_period_keyboard())
        return
    except StatisticsLoadError:
        await context.send_text(STATISTICS_LOAD_ERROR_TEXT, keyboard=statistics_period_keyboard())
        return

    _push_current_screen(context, state.STATISTICS_RESULT_SCREEN)
    await context.send_text(format_statistics_text(result), keyboard=statistics_result_keyboard())


async def handle_statistics_back(context: RouterContext) -> None:
    """Navigate back inside statistics screens."""

    await _answer_callback_if_needed(context, "Возвращаемся назад ⬅️")
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.STATISTICS_RESULT_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.STATISTICS_PERIOD_SCREEN)
        await _show_period_selection(context)
        return
    previous = state.pop_previous_screen(_user_id(context), _chat_id(context))
    if previous and previous != state.STATISTICS_PERIOD_SCREEN:
        from max_barbershop_bot.services.navigation import render_screen

        await render_screen(context, previous)
        return
    await show_home(context)


async def handle_statistics_home(context: RouterContext) -> None:
    """Return from statistics to the role-based main menu."""

    await _answer_callback_if_needed(context, "Открываем главное меню 🏠")
    await show_home(context)


async def _show_period_selection(context: RouterContext) -> None:
    state.set_current_screen(_user_id(context), _chat_id(context), state.STATISTICS_PERIOD_SCREEN)
    await context.send_text(STATISTICS_MENU_TEXT, keyboard=statistics_period_keyboard())


def _can_access(context: RouterContext) -> bool:
    return can_view_statistics(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"
    return StaffRolesRepository(_database_path()).get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current != screen_id:
        state.push_screen(_user_id(context), _chat_id(context), current)
    state.set_current_screen(_user_id(context), _chat_id(context), screen_id)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, STATISTICS_NO_ACCESS_TEXT)
    await context.send_text(STATISTICS_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
