"""My bookings flow for viewing and cancelling future YClients records in MAX."""

from __future__ import annotations

import logging
from os import getenv
from typing import Any

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.my_bookings import (
    MY_BOOKING_CANCEL_IN_PROGRESS_TEXT,
    MY_BOOKING_NOT_FOUND_TEXT,
    MY_BOOKINGS_LOAD_ERROR_TEXT,
    MY_BOOKINGS_NO_PROFILE_TEXT,
    MyBookingCancellationError,
    MyBookingsLoadError,
    MyBookingsProfileMissingError,
    MyBookingsService,
    booking_display_data,
    format_booking_details_text,
    format_bookings_screen,
    format_cancel_confirmation_text,
    format_cancel_success_text,
)
from max_barbershop_bot.ui.buttons import (
    MENU_MY_BOOKINGS_PAYLOAD,
    MY_BOOKINGS_BACK_PAYLOAD,
    MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD,
    MY_BOOKINGS_CANCEL_START_PAYLOAD,
    MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX,
    my_booking_cancel_confirmation_keyboard,
    my_booking_cancel_result_keyboard,
    my_booking_details_keyboard,
    my_bookings_keyboard,
    my_bookings_list_keyboard,
)

logger = logging.getLogger(__name__)

_BOOKINGS_STATE_KEY = "my_bookings_items"
_BOOKINGS_TIMEZONE_STATE_KEY = "my_bookings_branch_timezone"
_SELECTED_BOOKING_STATE_KEY = "my_bookings_selected_booking"
_CANCEL_IN_PROGRESS_STATE_KEY = "booking_cancel_in_progress"
_MAX_BOOKING_BUTTONS = 20
_CANCELLATION_MARKER = "Запись отменена из MAX бота"


def register_my_bookings_routes(router: Router) -> None:
    """Register callbacks for the My bookings flow."""

    router.on_callback(MENU_MY_BOOKINGS_PAYLOAD, handle_my_bookings_open)
    router.on_callback(MY_BOOKINGS_BACK_PAYLOAD, handle_my_bookings_back)
    router.on_callback(MY_BOOKINGS_CANCEL_START_PAYLOAD, handle_my_booking_cancel_start)
    router.on_callback(MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD, handle_my_booking_cancel_confirm)
    for index in range(_MAX_BOOKING_BUTTONS):
        router.on_callback(f"{MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX}{index}", handle_my_booking_details)


async def handle_my_bookings_open(context: RouterContext) -> None:
    """Open the real My bookings screen instead of the placeholder."""

    await context.answer_callback("Открываем ваши записи 📅")
    await _show_my_bookings(context)


async def handle_my_booking_details(context: RouterContext) -> None:
    """Show selected booking details and cancellation action."""

    await context.answer_callback("Открываем запись 📋")
    booking = _booking_by_payload(context)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    timezone_name = _timezone_from_state(context)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, booking)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_DETAILS_SCREEN)
    await context.send_text(format_booking_details_text(booking, timezone_name=timezone_name), keyboard=my_booking_details_keyboard())


async def handle_my_booking_cancel_start(context: RouterContext) -> None:
    """Ask confirmation before cancelling selected booking."""

    await context.answer_callback("Подтвердите отмену ❌")
    booking = _selected_booking(context)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    timezone_name = _timezone_from_state(context)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_CONFIRM_SCREEN)
    await context.send_text(format_cancel_confirmation_text(booking, timezone_name=timezone_name), keyboard=my_booking_cancel_confirmation_keyboard())


async def handle_my_booking_cancel_confirm(context: RouterContext) -> None:
    """Cancel selected YClients booking with a simple duplicate-tap guard."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    booking = _selected_booking(context)
    if booking is None:
        await context.answer_callback(MY_BOOKING_NOT_FOUND_TEXT)
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    if state.get_state_data_value(platform_user_id, chat_id, _CANCEL_IN_PROGRESS_STATE_KEY) is True:
        await context.answer_callback(MY_BOOKING_CANCEL_IN_PROGRESS_TEXT)
        return

    record_id = _booking_record_id(booking)
    if not record_id:
        await context.answer_callback(MY_BOOKING_NOT_FOUND_TEXT)
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    state.set_state_data_value(platform_user_id, chat_id, _CANCEL_IN_PROGRESS_STATE_KEY, True)
    await context.answer_callback("Отменяем запись ⏳")
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    user = _current_user(context)
    try:
        await service.cancel_booking_for_user(user, yclients_record_id=record_id, platform_user_id=platform_user_id)
    except (MyBookingsProfileMissingError, MyBookingCancellationError) as exc:
        logger.warning(
            "Booking cancellation failed: operation=cancel_booking platform_user_id=%s yclients_record_id=%s error_class=%s",
            platform_user_id,
            record_id,
            type(exc).__name__,
        )
        state.set_state_data_value(platform_user_id, chat_id, _CANCEL_IN_PROGRESS_STATE_KEY, False)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_cancel_result_keyboard())
        return

    _log_local_cancellation(platform_user_id=platform_user_id, yclients_record_id=record_id, user=user)
    state.set_state_data_value(platform_user_id, chat_id, _CANCEL_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_SUCCESS_SCREEN)
    timezone_name = _timezone_from_state(context)
    await context.send_text(format_cancel_success_text(booking, timezone_name=timezone_name), keyboard=my_booking_cancel_result_keyboard())


async def handle_my_bookings_back(context: RouterContext) -> None:
    """Return from cancellation confirmation to details, or from details to the list."""

    await context.answer_callback("Назад ⬅️")
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen == state.MY_BOOKING_CANCEL_CONFIRM_SCREEN:
        booking = _selected_booking(context)
        if booking is not None:
            state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_DETAILS_SCREEN)
            await context.send_text(
                format_booking_details_text(booking, timezone_name=_timezone_from_state(context)),
                keyboard=my_booking_details_keyboard(),
            )
            return
    await _show_my_bookings(context, push_current=False)


async def _show_my_bookings(context: RouterContext, *, push_current: bool = True) -> None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    current_screen = state.get_current_screen(platform_user_id, chat_id)
    my_bookings_screens = {
        state.MY_BOOKINGS_SCREEN,
        state.MY_BOOKINGS_EMPTY_SCREEN,
        state.MY_BOOKINGS_ERROR_SCREEN,
        state.MY_BOOKING_DETAILS_SCREEN,
        state.MY_BOOKING_CANCEL_CONFIRM_SCREEN,
        state.MY_BOOKING_CANCEL_SUCCESS_SCREEN,
        state.MY_BOOKING_CANCEL_ERROR_SCREEN,
    }
    if push_current and current_screen not in my_bookings_screens:
        state.push_screen(platform_user_id, chat_id, current_screen)

    user = _current_user(context)
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        result = await service.get_future_bookings_for_user(user, platform_user_id=platform_user_id)
    except MyBookingsProfileMissingError:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_NO_PROFILE_TEXT, keyboard=my_bookings_keyboard())
        return
    except MyBookingsLoadError as exc:
        logger.warning(
            "My bookings screen failed: operation=show_my_bookings platform_user_id=%s "
            "user_exists=%s yclients_client_id_present=%s phone_present=%s error_class=%s",
            platform_user_id,
            user is not None,
            bool(user and user.yclients_client_id),
            bool(user and user.phone),
            type(exc).__name__,
        )
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_LOAD_ERROR_TEXT, keyboard=my_bookings_keyboard())
        return

    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_STATE_KEY, [booking_display_data(item, timezone_name=result.branch_timezone) for item in result.bookings])
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_TIMEZONE_STATE_KEY, result.branch_timezone)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _CANCEL_IN_PROGRESS_STATE_KEY, False)

    if result.is_empty:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_EMPTY_SCREEN)
        await context.send_text(format_bookings_screen([], timezone_name=result.branch_timezone), keyboard=my_bookings_keyboard(include_booking=True))
        return

    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_SCREEN)
    await context.send_text(
        format_bookings_screen(result.bookings, timezone_name=result.branch_timezone),
        keyboard=my_bookings_list_keyboard(len(result.bookings), max_buttons=_MAX_BOOKING_BUTTONS),
    )


def _booking_by_payload(context: RouterContext) -> dict[str, Any] | None:
    payload = context.event.callback_payload or ""
    raw_index = payload.removeprefix(MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX)
    if not raw_index.isdigit():
        return None
    bookings = _bookings_from_state(context)
    index = int(raw_index)
    if index < 0 or index >= len(bookings):
        return None
    return bookings[index]


def _selected_booking(context: RouterContext) -> dict[str, Any] | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _SELECTED_BOOKING_STATE_KEY)
    return value if isinstance(value, dict) else None


def _bookings_from_state(context: RouterContext) -> list[dict[str, Any]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BOOKINGS_STATE_KEY)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _timezone_from_state(context: RouterContext) -> str:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BOOKINGS_TIMEZONE_STATE_KEY)
    return value if isinstance(value, str) and value.strip() else "Europe/Moscow"


def _booking_record_id(booking: dict[str, Any]) -> str | None:
    value = booking.get("yclients_record_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _log_local_cancellation(*, platform_user_id: str | None, yclients_record_id: str, user: Any) -> None:
    if not platform_user_id:
        return
    try:
        PlatformAttributionRepository(_database_path()).create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=user.yclients_client_id if user else None,
            marker=_CANCELLATION_MARKER,
            platform=PLATFORM_MAX,
        )
        logger.info(
            "Local cancellation attribution logged: operation=cancel_booking platform_user_id=%s yclients_record_id=%s",
            platform_user_id,
            yclients_record_id,
        )
    except Exception as exc:  # noqa: BLE001 - local log must not change successful YClients cancellation.
        logger.warning(
            "Local cancellation attribution failed: operation=cancel_booking platform_user_id=%s yclients_record_id=%s error_class=%s",
            platform_user_id,
            yclients_record_id,
            type(exc).__name__,
        )


def _current_user(context: RouterContext):
    platform_user_id = _user_id(context)
    if not platform_user_id:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
