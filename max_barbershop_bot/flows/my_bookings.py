"""My bookings flow for viewing and cancelling future YClients records in MAX."""

from __future__ import annotations

import logging
from datetime import datetime
from os import getenv
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.booking import (
    BookingService,
    BookingServiceError,
    build_booking_dates,
    format_date_button,
    format_slot_button,
)
from max_barbershop_bot.services.my_bookings import (
    MY_BOOKING_CANCEL_IN_PROGRESS_TEXT,
    MY_BOOKING_NOT_FOUND_TEXT,
    MY_BOOKING_RESCHEDULE_DATES_TEXT,
    MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT,
    MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT,
    MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT,
    MY_BOOKING_RESCHEDULE_SLOTS_TEXT,
    MY_BOOKINGS_LOAD_ERROR_TEXT,
    MY_BOOKINGS_NO_PROFILE_TEXT,
    MyBookingCancellationError,
    MyBookingRescheduleError,
    MyBookingRescheduleNotAllowedError,
    MyBookingReschedulePrepareError,
    MyBookingsLoadError,
    MyBookingsProfileMissingError,
    MyBookingsService,
    booking_display_data,
    format_booking_details_text,
    format_bookings_screen,
    format_cancel_confirmation_text,
    format_cancel_success_text,
    format_display_date,
    format_reschedule_confirmation_text,
    build_new_datetime_iso,
    format_reschedule_success_text,
)
from max_barbershop_bot.ui.buttons import (
    MENU_MY_BOOKINGS_PAYLOAD,
    MY_BOOKINGS_BACK_PAYLOAD,
    MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD,
    MY_BOOKINGS_CANCEL_START_PAYLOAD,
    MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_START_PAYLOAD,
    my_booking_cancel_confirmation_keyboard,
    my_booking_cancel_result_keyboard,
    my_booking_details_keyboard,
    my_booking_reschedule_confirmation_keyboard,
    my_booking_reschedule_dates_keyboard,
    my_booking_reschedule_result_keyboard,
    my_booking_reschedule_slots_keyboard,
    my_bookings_keyboard,
    my_bookings_list_keyboard,
)

logger = logging.getLogger(__name__)

_BOOKINGS_STATE_KEY = "my_bookings_items"
_BOOKINGS_TIMEZONE_STATE_KEY = "my_bookings_branch_timezone"
_SELECTED_BOOKING_STATE_KEY = "my_bookings_selected_booking"
_CANCEL_IN_PROGRESS_STATE_KEY = "my_bookings_cancel_in_progress"
_CANCEL_COMPLETED_STATE_KEY = "my_bookings_cancel_completed_record_ids"
_RESCHEDULE_CONTEXT_STATE_KEY = "my_booking_reschedule_context"
_RESCHEDULE_DATES_STATE_KEY = "my_booking_reschedule_dates"
_RESCHEDULE_SLOTS_STATE_KEY = "my_booking_reschedule_slots"
_RESCHEDULE_NEW_DATE_STATE_KEY = "my_booking_reschedule_new_date"
_RESCHEDULE_NEW_SLOT_STATE_KEY = "my_booking_reschedule_new_slot"
_RESCHEDULE_IN_PROGRESS_STATE_KEY = "booking_reschedule_in_progress"
_RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY = "reschedule_completed_old_record_id"
_RESCHEDULE_NEW_RECORD_STATE_KEY = "reschedule_new_record_id"
_MAX_BOOKING_BUTTONS = 20
_MAX_RESCHEDULE_DATES = 14
_MAX_RESCHEDULE_SLOTS = 30
_CANCELLATION_MARKER_PREFIX = "Запись отменена из MAX бота"


def register_my_bookings_routes(router: Router) -> None:
    """Register callbacks for the My bookings flow."""

    router.on_callback(MENU_MY_BOOKINGS_PAYLOAD, handle_my_bookings_open)
    router.on_callback(MY_BOOKINGS_BACK_PAYLOAD, handle_my_bookings_back)
    router.on_callback(MY_BOOKINGS_CANCEL_START_PAYLOAD, handle_my_booking_cancel_start)
    router.on_callback(MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD, handle_my_booking_cancel_confirm)
    router.on_callback(MY_BOOKINGS_RESCHEDULE_START_PAYLOAD, handle_my_booking_reschedule_start)
    router.on_callback(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD, handle_my_booking_reschedule_confirm)
    for index in range(_MAX_RESCHEDULE_DATES):
        router.on_callback(f"{MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX}{index}", handle_my_booking_reschedule_date)
    for index in range(_MAX_RESCHEDULE_SLOTS):
        router.on_callback(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}{index}", handle_my_booking_reschedule_slot)
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
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_booking_cancel_result_keyboard())
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

    record_id = _booking_record_id(booking)
    if not record_id:
        await context.answer_callback(MY_BOOKING_NOT_FOUND_TEXT)
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    if _cancel_completed(context, record_id):
        logger.info(
            "MAX booking cancel diagnostic: platform_user_id_present=%s yclients_record_id_present=%s cancel_already_completed=%s",
            bool(platform_user_id),
            True,
            True,
        )
        await context.answer_callback(MY_BOOKING_NOT_FOUND_TEXT)
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_booking_cancel_result_keyboard())
        return

    if _cancel_in_progress(context, record_id):
        logger.info(
            "MAX booking cancel diagnostic: platform_user_id_present=%s yclients_record_id_present=%s cancel_in_progress=%s",
            bool(platform_user_id),
            True,
            True,
        )
        await context.answer_callback(MY_BOOKING_CANCEL_IN_PROGRESS_TEXT)
        return

    _set_cancel_in_progress(context, record_id)
    await context.answer_callback("Отменяем запись ⏳")
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    user = _current_user(context)
    marker = _build_cancellation_marker(_timezone_from_state(context))
    try:
        await service.cancel_booking_for_user(
            user,
            yclients_record_id=record_id,
            platform_user_id=platform_user_id,
            cancellation_marker=marker,
        )
    except (MyBookingsProfileMissingError, MyBookingCancellationError) as exc:
        logger.warning(
            "Booking cancellation failed: operation=cancel_booking platform_user_id=%s yclients_record_id=%s error_class=%s",
            platform_user_id,
            record_id,
            type(exc).__name__,
        )
        _clear_cancel_in_progress(context)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_cancel_result_keyboard())
        return

    _log_local_cancellation(platform_user_id=platform_user_id, yclients_record_id=record_id, user=user, marker=marker)
    _mark_cancel_completed(context, record_id)
    _clear_cancel_in_progress(context)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_SUCCESS_SCREEN)
    timezone_name = _timezone_from_state(context)
    await context.send_text(format_cancel_success_text(booking, timezone_name=timezone_name), keyboard=my_booking_cancel_result_keyboard())
    await _show_my_bookings(context, push_current=False)


async def handle_my_booking_reschedule_start(context: RouterContext) -> None:
    """Start selected future booking reschedule by loading authoritative YClients details."""

    await context.answer_callback("Готовим перенос 🔁")
    booking = _selected_booking(context)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return
    record_id = _booking_record_id(booking)
    if not record_id:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        reschedule_context = await service.prepare_reschedule_context(
            _current_user(context),
            yclients_record_id=record_id,
            platform_user_id=platform_user_id,
        )
    except (MyBookingsProfileMissingError, MyBookingReschedulePrepareError, MyBookingRescheduleError) as exc:
        logger.warning(
            "Booking reschedule prepare failed: operation=prepare_reschedule platform_user_id=%s "
            "yclients_record_id=%s error_class=%s",
            platform_user_id,
            record_id,
            type(exc).__name__,
        )
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return

    dates = build_booking_dates(days=_MAX_RESCHEDULE_DATES, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context)))
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_CONTEXT_STATE_KEY, reschedule_context)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_DATES_STATE_KEY, [item.isoformat() for item in dates])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, [])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    await context.send_text(
        MY_BOOKING_RESCHEDULE_DATES_TEXT,
        keyboard=my_booking_reschedule_dates_keyboard(dates, lambda value: format_date_button(value, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context)))),
    )


async def handle_my_booking_reschedule_date(context: RouterContext) -> None:
    """Save new date and load available slots for the same service and master."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    index = _payload_index(context, MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX)
    dates = _reschedule_dates(context)
    if index is None or index < 0 or index >= len(dates):
        await context.answer_callback("Дата не найдена")
        return
    new_booking_date = dates[index]
    reschedule_context = _reschedule_context(context)
    service_id = _clean_state_text(reschedule_context.get("service_id"))
    staff_id = _clean_state_text(reschedule_context.get("staff_id"))
    if not service_id or not staff_id:
        await context.answer_callback("Не удалось подготовить перенос 🙏")
        await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return

    await context.answer_callback("Ищем свободное время 🕒")
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slots = await booking_service.get_available_slots(
            yclients_service_id=service_id,
            yclients_master_id=staff_id,
            booking_date=new_booking_date,
        )
    except BookingServiceError as exc:
        logger.warning(
            "Booking reschedule slots failed: operation=reschedule_slots platform_user_id=%s service_id=%s "
            "staff_id=%s booking_date=%s error_class=%s",
            platform_user_id,
            service_id,
            staff_id,
            new_booking_date,
            type(exc).__name__,
        )
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return

    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, new_booking_date)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, slots[:_MAX_RESCHEDULE_SLOTS])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    text = MY_BOOKING_RESCHEDULE_SLOTS_TEXT if slots else MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT
    await context.send_text(text, keyboard=my_booking_reschedule_slots_keyboard(slots[:_MAX_RESCHEDULE_SLOTS], format_slot_button))


async def handle_my_booking_reschedule_slot(context: RouterContext) -> None:
    """Save selected new slot and show old/new confirmation."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    index = _payload_index(context, MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX)
    slots = _reschedule_slots(context)
    if index is None or index < 0 or index >= len(slots):
        await context.answer_callback("Время не найдено")
        return
    selected_slot = slots[index]
    slot_time = _clean_state_text(getattr(selected_slot, "time", None))
    if not slot_time:
        await context.answer_callback("Время не найдено")
        return

    reschedule_context = _reschedule_context(context)
    new_booking_date = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY))
    selected_datetime = _clean_state_text(getattr(selected_slot, "datetime_iso", None))
    new_datetime = build_new_datetime_iso(new_booking_date, slot_time, selected_datetime=selected_datetime)
    confirmation_data = {
        "old_date": reschedule_context.get("old_date"),
        "old_time": reschedule_context.get("old_time"),
        "new_date": format_display_date(new_booking_date, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))),
        "new_time": slot_time,
        "new_datetime": new_datetime,
    }
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, confirmation_data)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    await context.answer_callback("Проверьте перенос 🔁")
    await context.send_text(format_reschedule_confirmation_text(confirmation_data), keyboard=my_booking_reschedule_confirmation_keyboard())


async def handle_my_booking_reschedule_confirm(context: RouterContext) -> None:
    """Update the selected YClients record with duplicate confirm protection."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    if state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY) is True:
        await context.answer_callback(MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT)
        return
    reschedule_context = _reschedule_context(context)
    slot_data = _reschedule_new_slot_data(context)
    selected_booking = _selected_booking(context)
    record_id = _clean_state_text(reschedule_context.get("yclients_record_id"))
    new_datetime = _clean_state_text(slot_data.get("new_datetime"))
    if not record_id or not new_datetime:
        await context.answer_callback("Не удалось подготовить перенос 🙏")
        await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    completed_old_id = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY))
    if completed_old_id == record_id:
        await context.answer_callback("Запись уже перенесена ✅")
        await context.send_text(_format_reschedule_success_card(selected_booking, slot_data, timezone_name=_timezone_from_state(context)), keyboard=my_booking_reschedule_result_keyboard())
        return

    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, True)
    await context.answer_callback("Переносим запись ⏳")
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        result = await service.reschedule_booking_for_user(
            _current_user(context),
            reschedule_context=reschedule_context,
            new_datetime_iso=new_datetime,
            platform_user_id=platform_user_id,
        )
    except (MyBookingsProfileMissingError, MyBookingRescheduleNotAllowedError, MyBookingRescheduleError) as exc:
        logger.warning(
            "Booking reschedule failed: operation=reschedule_booking platform_user_id=%s yclients_record_id=%s "
            "new_datetime=%s error_class=%s",
            platform_user_id,
            record_id,
            new_datetime,
            type(exc).__name__,
        )
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return

    new_record_id = _clean_state_text(result.get("new_record_id")) if isinstance(result, dict) else ""
    _log_local_reschedule(platform_user_id=platform_user_id, old_record_id=record_id, new_record_id=new_record_id, user=_current_user(context))
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY, record_id)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_RECORD_STATE_KEY, new_record_id)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_CONTEXT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_SUCCESS_SCREEN)
    await context.send_text(_format_reschedule_success_card(selected_booking, slot_data, timezone_name=_timezone_from_state(context)), keyboard=my_booking_reschedule_result_keyboard())


async def handle_my_bookings_back(context: RouterContext) -> None:
    """Return from cancellation confirmation to details, or from details to the list."""

    await context.answer_callback("Назад ⬅️")
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen in {state.MY_BOOKING_CANCEL_CONFIRM_SCREEN, state.MY_BOOKING_RESCHEDULE_DATES_SCREEN}:
        booking = _selected_booking(context)
        if booking is not None:
            state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_DETAILS_SCREEN)
            await context.send_text(
                format_booking_details_text(booking, timezone_name=_timezone_from_state(context)),
                keyboard=my_booking_details_keyboard(),
            )
            return
    if current_screen == state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN:
        await _show_reschedule_dates_from_state(context)
        return
    if current_screen == state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN:
        await _show_reschedule_slots_from_state(context)
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
        state.MY_BOOKING_RESCHEDULE_DATES_SCREEN,
        state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN,
        state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN,
        state.MY_BOOKING_RESCHEDULE_SUCCESS_SCREEN,
        state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN,
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
    _clear_cancel_in_progress(context)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)

    if result.is_empty:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_EMPTY_SCREEN)
        await context.send_text(format_bookings_screen([], timezone_name=result.branch_timezone), keyboard=my_bookings_keyboard(include_booking=True))
        return

    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_SCREEN)
    await context.send_text(
        format_bookings_screen(result.bookings, timezone_name=result.branch_timezone),
        keyboard=my_bookings_list_keyboard(len(result.bookings), max_buttons=_MAX_BOOKING_BUTTONS),
    )


async def _show_reschedule_dates_from_state(context: RouterContext) -> None:
    dates = _reschedule_dates(context)
    reschedule_context = _reschedule_context(context)
    timezone_name = str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))
    state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    await context.send_text(
        MY_BOOKING_RESCHEDULE_DATES_TEXT,
        keyboard=my_booking_reschedule_dates_keyboard(dates, lambda value: format_date_button(value, timezone_name=timezone_name)),
    )


async def _show_reschedule_slots_from_state(context: RouterContext) -> None:
    slots = _reschedule_slots(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    text = MY_BOOKING_RESCHEDULE_SLOTS_TEXT if slots else MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT
    await context.send_text(text, keyboard=my_booking_reschedule_slots_keyboard(slots, format_slot_button))



def _format_reschedule_success_card(booking: dict[str, Any] | None, slot_data: dict[str, Any], *, timezone_name: str) -> str:
    if not booking:
        return format_reschedule_success_text(slot_data)
    updated = dict(booking)
    if slot_data.get("new_datetime"):
        updated["datetime"] = slot_data.get("new_datetime")
    updated["date"] = slot_data.get("new_date") or updated.get("date")
    updated["time"] = slot_data.get("new_time") or updated.get("time")
    return "Запись перенесена ✅\n\n" + format_booking_details_text(updated, timezone_name=timezone_name)

def _cancel_in_progress(context: RouterContext, record_id: str) -> bool:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _CANCEL_IN_PROGRESS_STATE_KEY)
    return value == record_id or value is True


def _set_cancel_in_progress(context: RouterContext, record_id: str) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), _CANCEL_IN_PROGRESS_STATE_KEY, record_id)


def _clear_cancel_in_progress(context: RouterContext) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), _CANCEL_IN_PROGRESS_STATE_KEY, None)


def _cancel_completed(context: RouterContext, record_id: str) -> bool:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _CANCEL_COMPLETED_STATE_KEY)
    return isinstance(value, list) and record_id in {str(item) for item in value}


def _mark_cancel_completed(context: RouterContext, record_id: str) -> None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _CANCEL_COMPLETED_STATE_KEY)
    completed = [str(item) for item in value] if isinstance(value, list) else []
    if record_id not in completed:
        completed.append(record_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _CANCEL_COMPLETED_STATE_KEY, completed)


def _build_cancellation_marker(timezone_name: str) -> str:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Europe/Moscow")
    return f"{_CANCELLATION_MARKER_PREFIX} {datetime.now(tz).strftime('%d.%m.%Y в %H:%M')}"


def _payload_index(context: RouterContext, prefix: str) -> int | None:
    payload = context.event.callback_payload or ""
    raw_index = payload.removeprefix(prefix)
    return int(raw_index) if raw_index.isdigit() else None


def _reschedule_context(context: RouterContext) -> dict[str, Any]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _RESCHEDULE_CONTEXT_STATE_KEY)
    return value if isinstance(value, dict) else {}


def _reschedule_dates(context: RouterContext) -> list[str]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _RESCHEDULE_DATES_STATE_KEY)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _reschedule_slots(context: RouterContext) -> list[Any]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _RESCHEDULE_SLOTS_STATE_KEY)
    return value if isinstance(value, list) else []


def _reschedule_new_slot_data(context: RouterContext) -> dict[str, Any]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _RESCHEDULE_NEW_SLOT_STATE_KEY)
    return value if isinstance(value, dict) else {}


def _clean_state_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


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


def _log_local_reschedule(*, platform_user_id: str | None, old_record_id: str, new_record_id: str, user: Any) -> None:
    if not platform_user_id:
        return
    try:
        repo = PlatformAttributionRepository(_database_path())
        if new_record_id:
            repo.create_record(
                platform_user_id=platform_user_id,
                yclients_record_id=new_record_id,
                yclients_client_id=user.yclients_client_id if user else None,
                marker="Клиент перенёс запись из MAX бота",
                platform=PLATFORM_MAX,
            )
        repo.create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=old_record_id,
            yclients_client_id=user.yclients_client_id if user else None,
            marker="Запись перенесена из MAX бота",
            platform=PLATFORM_MAX,
        )
        logger.info(
            "Local reschedule attribution logged: operation=reschedule_booking platform_user_id=%s old_record_id=%s new_record_id=%s",
            platform_user_id,
            old_record_id,
            new_record_id,
        )
    except Exception as exc:  # noqa: BLE001 - local log must not change successful YClients update.
        logger.warning(
            "Local reschedule attribution failed: operation=reschedule_booking platform_user_id=%s old_record_id=%s new_record_id=%s error_class=%s",
            platform_user_id,
            old_record_id,
            new_record_id,
            type(exc).__name__,
        )


def _log_local_cancellation(*, platform_user_id: str | None, yclients_record_id: str, user: Any, marker: str) -> None:
    if not platform_user_id:
        return
    try:
        PlatformAttributionRepository(_database_path()).create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=user.yclients_client_id if user else None,
            marker=marker,
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
    repository = UsersRepository(_database_path())
    if platform_user_id:
        user = repository.find_by_identifier(platform_user_id, platform=PLATFORM_MAX)
        if user is not None:
            return user
    chat_id = _chat_id(context)
    if chat_id:
        return repository.find_by_chat_id(chat_id)
    return None


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
