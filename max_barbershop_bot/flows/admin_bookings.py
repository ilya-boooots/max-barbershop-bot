"""Admin bookings reports flow for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_admin_bookings, effective_role
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.admin_bookings import (
    AdminBookingsFilter,
    AdminBookingsAuthError,
    AdminBookingsLoadError,
    AdminBookingsRateLimitError,
    AdminBookingsSettingsMissingError,
    STALE_LIST_TEXT,
    format_admin_booking_card,
    format_admin_booking_list_item,
    format_admin_bookings_list,
    load_admin_booking_detail,
    load_admin_bookings,
    load_master_options,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_BOOKINGS_BACK_PAYLOAD,
    ADMIN_BOOKINGS_HOME_PAYLOAD,
    ADMIN_BOOKINGS_ITEM_PAYLOAD_PREFIX,
    ADMIN_BOOKINGS_MASTER_PAYLOAD_PREFIX,
    ADMIN_BOOKINGS_OPEN_PAYLOAD,
    ADMIN_BOOKINGS_REFRESH_PAYLOAD,
    ADMIN_BOOKINGS_STATUS_PAYLOAD_PREFIX,
    ADMIN_BOOKINGS_TODAY_PAYLOAD,
    ADMIN_BOOKINGS_TOMORROW_PAYLOAD,
    admin_booking_detail_keyboard,
    admin_bookings_list_keyboard,
    admin_bookings_master_keyboard,
    admin_bookings_status_keyboard,
)
from max_barbershop_bot.ui.texts import (
    ADMIN_BOOKINGS_AUTH_ERROR_TEXT,
    ADMIN_BOOKINGS_GENERIC_ERROR_TEXT,
    ADMIN_BOOKINGS_NOT_CONFIGURED_TEXT,
    ADMIN_BOOKINGS_RATE_LIMIT_TEXT,
    ADMIN_BOOKINGS_UNAVAILABLE_TEXT,
    STATISTICS_NO_ACCESS_TEXT,
)

FILTER_KEY = "admin_bookings_filter"
BOOKINGS_KEY = "admin_bookings_rows"
STATUSES_KEY = "admin_bookings_statuses"
MASTERS_KEY = "admin_bookings_masters"


def register_admin_bookings_routes(router: Router) -> None:
    """Register admin bookings callbacks."""

    router.on_callback(ADMIN_BOOKINGS_OPEN_PAYLOAD, handle_admin_bookings_open)
    router.on_callback(ADMIN_BOOKINGS_TODAY_PAYLOAD, handle_admin_bookings_day)
    router.on_callback(ADMIN_BOOKINGS_TOMORROW_PAYLOAD, handle_admin_bookings_day)
    router.on_callback(ADMIN_BOOKINGS_REFRESH_PAYLOAD, handle_admin_bookings_refresh)
    router.on_callback("admbook:filter:master", handle_admin_bookings_master_picker)
    router.on_callback("admbook:filter:status", handle_admin_bookings_status_picker)
    router.on_callback("admbook:master:all", handle_admin_bookings_set_master)
    router.on_callback("admbook:status:all", handle_admin_bookings_set_status)
    router.on_callback(ADMIN_BOOKINGS_BACK_PAYLOAD, handle_admin_bookings_back)
    router.on_callback(ADMIN_BOOKINGS_HOME_PAYLOAD, handle_admin_bookings_home)
    for index in range(30):
        router.on_callback(f"{ADMIN_BOOKINGS_ITEM_PAYLOAD_PREFIX}{index}", handle_admin_bookings_detail)
        router.on_callback(f"{ADMIN_BOOKINGS_MASTER_PAYLOAD_PREFIX}{index}", handle_admin_bookings_set_master)
        router.on_callback(f"{ADMIN_BOOKINGS_STATUS_PAYLOAD_PREFIX}{index}", handle_admin_bookings_set_status)
    for page in range(10):
        router.on_callback(f"admbook:page:{page}", handle_admin_bookings_page)


async def handle_admin_bookings_open(context: RouterContext) -> None:
    """Open Telegram-equivalent admin bookings dashboard."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.ADMIN_BOOKINGS_LIST_SCREEN)
    _set_filter(context, AdminBookingsFilter())
    await _show_list(context)


async def handle_admin_bookings_day(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    current = _get_filter(context)
    day = "tomorrow" if context.event.callback_payload == ADMIN_BOOKINGS_TOMORROW_PAYLOAD else "today"
    _set_filter(context, AdminBookingsFilter(day=day, master_id=current.master_id, status=current.status))
    await _answer_callback_if_needed(context)
    await _show_list(context)


async def handle_admin_bookings_refresh(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _show_list(context)


async def handle_admin_bookings_page(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload or ""
    page = int(payload.rsplit(":", 1)[-1])
    current = _get_filter(context)
    _set_filter(context, AdminBookingsFilter(day=current.day, master_id=current.master_id, status=current.status, page=page))
    await _show_list(context)


async def handle_admin_bookings_master_picker(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    try:
        masters = await load_master_options()
    except Exception:
        await context.send_text("😔 Не удалось загрузить мастеров. Попробуйте позже 🙂", keyboard=admin_bookings_list_keyboard([], page=0, max_page=0))
        return
    state.set_state_data_value(_user_id(context), _chat_id(context), MASTERS_KEY, masters)
    state.set_current_screen(_user_id(context), _chat_id(context), state.ADMIN_BOOKINGS_FILTER_SCREEN)
    await context.send_text("👤 Выберите мастера для фильтра", keyboard=admin_bookings_master_keyboard(masters))


async def handle_admin_bookings_status_picker(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    statuses = _get_statuses(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.ADMIN_BOOKINGS_FILTER_SCREEN)
    await context.send_text("🧾 Выберите статус для фильтра", keyboard=admin_bookings_status_keyboard(statuses))


async def handle_admin_bookings_set_master(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload or ""
    master_id = None
    if not payload.endswith(":all"):
        masters = _get_masters(context)
        index = int(payload.rsplit(":", 1)[-1])
        if 0 <= index < len(masters):
            master_id = masters[index][0]
    current = _get_filter(context)
    _set_filter(context, AdminBookingsFilter(day=current.day, master_id=master_id, status=current.status))
    await _show_list(context)


async def handle_admin_bookings_set_status(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload or ""
    selected_status = None
    if not payload.endswith(":all"):
        statuses = _get_statuses(context)
        index = int(payload.rsplit(":", 1)[-1])
        if 0 <= index < len(statuses):
            selected_status = statuses[index]
    current = _get_filter(context)
    _set_filter(context, AdminBookingsFilter(day=current.day, master_id=current.master_id, status=selected_status))
    await _show_list(context)


async def handle_admin_bookings_detail(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload or ""
    rows = _get_rows(context)
    index = int(payload.rsplit(":", 1)[-1])
    if not 0 <= index < len(rows):
        await _answer_callback_if_needed(context)
        await context.send_text(STALE_LIST_TEXT, keyboard=admin_booking_detail_keyboard())
        return
    await _answer_callback_if_needed(context)
    try:
        item = await load_admin_booking_detail(rows[index])
    except (AdminBookingsSettingsMissingError, AdminBookingsLoadError, Exception):
        item = rows[index]
    state.set_current_screen(_user_id(context), _chat_id(context), state.ADMIN_BOOKING_DETAIL_SCREEN)
    await context.send_text(format_admin_booking_card(item), keyboard=admin_booking_detail_keyboard())


async def handle_admin_bookings_back(context: RouterContext) -> None:
    await _answer_callback_if_needed(context)
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.ADMIN_BOOKING_DETAIL_SCREEN:
        await _show_list(context)
        return
    if current == state.ADMIN_BOOKINGS_FILTER_SCREEN:
        await _show_list(context)
        return
    previous = state.pop_previous_screen(_user_id(context), _chat_id(context))
    if previous and previous != state.ADMIN_BOOKINGS_LIST_SCREEN:
        from max_barbershop_bot.services.navigation import render_screen

        await render_screen(context, previous)
        return
    await show_home(context)


async def handle_admin_bookings_home(context: RouterContext) -> None:
    await _answer_callback_if_needed(context)
    state.clear_state_data(_user_id(context), _chat_id(context))
    await show_home(context)


async def _show_list(context: RouterContext) -> None:
    state.set_current_screen(_user_id(context), _chat_id(context), state.ADMIN_BOOKINGS_LIST_SCREEN)
    filters = _get_filter(context)
    try:
        result = await load_admin_bookings(filters, actor_platform_user_id_present=_user_id(context) is not None)
    except AdminBookingsSettingsMissingError:
        text = ADMIN_BOOKINGS_NOT_CONFIGURED_TEXT
    except AdminBookingsAuthError:
        text = ADMIN_BOOKINGS_AUTH_ERROR_TEXT
    except AdminBookingsRateLimitError:
        text = ADMIN_BOOKINGS_RATE_LIMIT_TEXT
    except AdminBookingsLoadError:
        text = ADMIN_BOOKINGS_UNAVAILABLE_TEXT
    except Exception:  # noqa: BLE001 - match Telegram's safe generic handler fallback.
        text = ADMIN_BOOKINGS_GENERIC_ERROR_TEXT
    else:
        _set_filter(context, AdminBookingsFilter(day=filters.day, master_id=filters.master_id, status=filters.status, page=result.page))
        state.set_state_data_value(_user_id(context), _chat_id(context), BOOKINGS_KEY, result.page_bookings)
        state.set_state_data_value(_user_id(context), _chat_id(context), STATUSES_KEY, result.statuses)
        await context.send_text(
            format_admin_bookings_list(result),
            keyboard=admin_bookings_list_keyboard([format_admin_booking_list_item(item) for item in result.page_bookings], page=result.page, max_page=result.max_page),
        )
        return
    await context.send_text(text, keyboard=admin_bookings_list_keyboard([], page=0, max_page=0))


def _get_filter(context: RouterContext) -> AdminBookingsFilter:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), FILTER_KEY)
    return value if isinstance(value, AdminBookingsFilter) else AdminBookingsFilter()


def _set_filter(context: RouterContext, filters: AdminBookingsFilter) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), FILTER_KEY, filters)


def _get_rows(context: RouterContext) -> list[dict[str, object]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), BOOKINGS_KEY)
    return value if isinstance(value, list) else []


def _get_statuses(context: RouterContext) -> list[str]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), STATUSES_KEY)
    return value if isinstance(value, list) else []


def _get_masters(context: RouterContext) -> list[tuple[str, str]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), MASTERS_KEY)
    return value if isinstance(value, list) else []


def _can_access(context: RouterContext) -> bool:
    return can_view_admin_bookings(_actor_role(context))


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
