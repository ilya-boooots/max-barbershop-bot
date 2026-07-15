"""My bookings flow for viewing and cancelling future YClients records in MAX."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from os import getenv
from typing import Any

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import DEFAULT_ACTION_LOCK_TTL_SECONDS, acquire_action_lock, release_action_lock
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import is_developer
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE, build_yclients_action_comment, normalize_branch_timezone
from max_barbershop_bot.services.cancellation_recovery import create_cancellation_recovery_event
from max_barbershop_bot.services.booking import (
    BookingService,
    BookingServiceError,
    DATE_LOOKAHEAD_DAYS,
    format_date_button,
    format_slot_button,
)
from max_barbershop_bot.flows.booking import start_repeat_booking_with_prefill
from max_barbershop_bot.services.master_photos import MasterPhotosService
from max_barbershop_bot.services.my_bookings import (
    MY_BOOKING_CANCEL_IN_PROGRESS_TEXT,
    MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT,
    MY_BOOKING_NOT_FOUND_TEXT,
    MY_BOOKING_RESCHEDULE_DATES_TEXT,
    MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT,
    MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT,
    MY_BOOKING_RESCHEDULE_NO_DATES_TEXT,
    MY_BOOKING_RESCHEDULE_STALE_SLOT_TEXT,
    MY_BOOKING_RESCHEDULE_STALE_DATE_TEXT,
    MY_BOOKING_RESCHEDULE_SAME_SLOT_TEXT,
    MY_BOOKING_REPEAT_PREPARE_ERROR_TEXT,
    MY_BOOKING_REPEAT_SERVICE_UNAVAILABLE_TEXT,
    MY_BOOKING_REPEAT_MASTER_UNAVAILABLE_TEXT,
    MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT,
    MY_BOOKING_RESCHEDULE_SLOTS_TEXT,
    MY_BOOKINGS_LOAD_ERROR_TEXT,
    MY_BOOKINGS_RATE_LIMIT_TEXT,
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
    format_bookings_list_screen,
    format_visit_history_screen,
    format_cancel_confirmation_text,
    format_cancel_success_text,
    format_display_date,
    format_reschedule_confirmation_text,
    build_new_datetime_iso,
    format_reschedule_success_text,
    parse_booking_datetime,
    is_booking_cancelable,
    is_booking_reschedulable,
    is_future_booking,
    is_visible_my_booking,
    split_bookings_by_period,
)
from max_barbershop_bot.ui.buttons import (
    MENU_MY_BOOKINGS_PAYLOAD,
    MY_BOOKINGS_BACK_PAYLOAD,
    MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD,
    MY_BOOKINGS_CANCEL_START_PAYLOAD,
    MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX,
    MY_BOOKINGS_PAGE_PAYLOAD_PREFIX,
    MY_BOOKINGS_SHOW_ALL_ACTIVE_PAYLOAD,
    MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX,
    MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX,
    MY_BOOKINGS_REPEAT_START_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_START_PAYLOAD,
    my_booking_cancel_confirmation_keyboard,
    my_booking_active_card_keyboard,
    my_booking_entry_keyboard,
    my_booking_cancel_result_keyboard,
    my_booking_details_keyboard,
    my_bookings_empty_keyboard,
    my_booking_reschedule_confirmation_keyboard,
    my_booking_reschedule_dates_keyboard,
    my_booking_reschedule_result_keyboard,
    my_booking_reschedule_slots_keyboard,
    my_bookings_history_keyboard,
    my_bookings_keyboard,
    my_bookings_rate_limit_keyboard,
    my_bookings_list_keyboard,
    my_bookings_main_keyboard,
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
_BOOKINGS_PAGE_STATE_KEY = "my_bookings_page"
_ACTIVE_BOOKINGS_STATE_KEY = "my_bookings_active_items"
_ACTIVE_BOOKING_INDEX_STATE_KEY = "my_bookings_active_index"
_PAST_BOOKINGS_STATE_KEY = "my_bookings_past_items"
_RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY = "reschedule_completed_old_record_id"
_RESCHEDULE_NEW_RECORD_STATE_KEY = "reschedule_new_record_id"
_RESCHEDULE_OUTCOME_STATE_KEY = "my_booking_reschedule_outcome"
_RESCHEDULE_RESULT_STATE_KEY = "my_booking_reschedule_result"
_RESCHEDULE_OUTCOME_SUCCESS = "success"
_RESCHEDULE_OUTCOME_PARTIAL_FAILURE = "partial_failure"
_MAX_BOOKING_BUTTONS = 10
_MAX_RESCHEDULE_DATES = DATE_LOOKAHEAD_DAYS
_MAX_RESCHEDULE_SLOTS = 30
_CANCELLATION_MARKER_PREFIX = "Клиент отменил запись из MAX бота"
MY_BOOKINGS_HISTORY_SCREEN = "my_bookings_history"
_SUPPORTED_REPEAT_SOURCE_SCREENS = {state.MY_BOOKING_DETAILS_SCREEN, MY_BOOKINGS_HISTORY_SCREEN}


def register_my_bookings_routes(router: Router) -> None:
    """Register callbacks for the My bookings flow."""

    router.on_callback(MENU_MY_BOOKINGS_PAYLOAD, handle_my_bookings_open)
    router.on_callback(MY_BOOKINGS_BACK_PAYLOAD, handle_my_bookings_back)
    router.on_callback(MY_BOOKINGS_CANCEL_START_PAYLOAD, handle_my_booking_cancel_start)
    router.on_callback(MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD, handle_my_booking_cancel_confirm)
    router.on_callback(MY_BOOKINGS_REPEAT_START_PAYLOAD, handle_my_booking_repeat_start)
    router.on_callback(MY_BOOKINGS_RESCHEDULE_START_PAYLOAD, handle_my_booking_reschedule_start)
    router.on_callback(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD, handle_my_booking_reschedule_confirm)
    for index in range(_MAX_RESCHEDULE_DATES):
        router.on_callback(f"{MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX}{index}", handle_my_booking_reschedule_date)
    for index in range(_MAX_RESCHEDULE_SLOTS):
        router.on_callback(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}{index}", handle_my_booking_reschedule_slot)
    for index in range(100):
        router.on_callback(f"{MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX}{index}", handle_my_booking_details)
    router.on_callback(MY_BOOKINGS_SHOW_ALL_ACTIVE_PAYLOAD, handle_my_bookings_show_all_active)
    for page in range(10):
        router.on_callback(f"{MY_BOOKINGS_PAGE_PAYLOAD_PREFIX}{page}", handle_my_bookings_page)
        router.on_callback(f"{MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}{page}", handle_my_bookings_active_page)
        router.on_callback(f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}{page}", handle_my_bookings_history)


async def handle_my_bookings_show_all_active(context: RouterContext) -> None:
    """Open Telegram-reference active bookings carousel from the first active record."""

    await _show_active_booking_page(context, page=0)


async def handle_my_bookings_active_page(context: RouterContext) -> None:
    """Open one page of the active bookings carousel."""

    payload = context.event.callback_payload or ""
    raw_page = payload.removeprefix(MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX)
    page = int(raw_page) if raw_page.isdigit() else 0
    await _show_active_booking_page(context, page=page)


async def handle_my_bookings_history(context: RouterContext) -> None:
    """Show Telegram-reference visit history from already loaded past bookings."""

    await context.answer_callback()
    payload = context.event.callback_payload or ""
    raw_page = payload.removeprefix(MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX)
    page = int(raw_page) if raw_page.isdigit() else 0
    past = _past_bookings_from_state(context)
    timezone_name = _timezone_from_state(context)
    if not past:
        await _show_my_bookings(context, push_current=False)
        past = _past_bookings_from_state(context)
    page_size = 5
    max_page = max((len(past) - 1) // page_size, 0) if past else 0
    page = min(max(page, 0), max_page)
    start = page * page_size
    end = start + page_size
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    state.set_state_data_value(platform_user_id, chat_id, "my_bookings_history_page", page)
    state.set_current_screen(platform_user_id, chat_id, MY_BOOKINGS_HISTORY_SCREEN)
    await context.send_text(
        format_visit_history_screen(past, timezone_name=timezone_name, page=page, page_size=page_size),
        keyboard=my_bookings_history_keyboard(page=page, has_next=end < len(past), include_repeat=bool(past)),
    )


async def _show_active_booking_page(context: RouterContext, *, page: int) -> None:
    await context.answer_callback()
    active = _active_bookings_from_state(context)
    timezone_name = _timezone_from_state(context)
    if not active:
        await _show_my_bookings(context, push_current=False)
        active = _active_bookings_from_state(context)
    if not active:
        await _show_my_bookings(context, push_current=False)
        return
    index = min(max(page, 0), len(active) - 1)
    booking = active[index]
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, booking)
    state.set_state_data_value(platform_user_id, chat_id, _ACTIVE_BOOKING_INDEX_STATE_KEY, index)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_DETAILS_SCREEN)
    can_cancel = is_booking_cancelable(booking, timezone_name=timezone_name)
    can_reschedule = is_booking_reschedulable(booking, timezone_name=timezone_name)
    await context.send_text(
        format_booking_details_text(booking, timezone_name=timezone_name, title="📋 Активная запись"),
        keyboard=my_booking_active_card_keyboard(index=index, total=len(active), can_cancel=can_cancel, can_reschedule=can_reschedule),
        attachments=_booking_master_photo_attachment(booking),
    )


async def handle_my_bookings_page(context: RouterContext) -> None:
    """Open another page of the already loaded My bookings list."""

    await context.answer_callback()
    payload = context.event.callback_payload or ""
    raw_page = payload.removeprefix(MY_BOOKINGS_PAGE_PAYLOAD_PREFIX)
    page = int(raw_page) if raw_page.isdigit() else 0
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    bookings = _bookings_from_state(context)
    timezone_name = _timezone_from_state(context)
    if not bookings:
        await _show_my_bookings(context, push_current=False)
        return
    max_page = max((len(bookings) - 1) // _MAX_BOOKING_BUTTONS, 0)
    page = min(max(page, 0), max_page)
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_PAGE_STATE_KEY, page)
    start = page * _MAX_BOOKING_BUTTONS
    end = min(start + _MAX_BOOKING_BUTTONS, len(bookings))
    await context.send_text(
        format_bookings_list_screen(bookings[start:end], timezone_name=timezone_name),
        keyboard=my_bookings_main_keyboard(bookings, timezone_name=timezone_name, max_buttons=_MAX_BOOKING_BUTTONS, page=page),
    )


async def handle_my_bookings_open(context: RouterContext) -> None:
    """Open the real My bookings screen instead of the placeholder."""

    await context.answer_callback()
    await context.send_text("⏳ Загружаю ваши записи…")
    try:
        await _show_my_bookings(context)
    except Exception as exc:  # noqa: BLE001 - never leave the user with a silent callback.
        logger.exception(
            "My bookings open failed unexpectedly: platform_user_id_present=%s chat_id_present=%s error_class=%s",
            bool(_user_id(context)),
            bool(_chat_id(context)),
            type(exc).__name__,
        )
        state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_LOAD_ERROR_TEXT, keyboard=my_bookings_keyboard())


async def handle_my_booking_details(context: RouterContext) -> None:
    """Show selected booking details and cancellation action."""

    await context.answer_callback()
    booking = _booking_by_payload(context)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_booking_cancel_result_keyboard())
        return

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    timezone_name = _timezone_from_state(context)
    try:
        fresh_booking = await MyBookingsService(YClientsSettingsRepository(_database_path())).get_booking_for_user(
            _current_user(context),
            yclients_record_id=_booking_record_id(booking) or "",
            platform_user_id=platform_user_id,
        )
        booking = fresh_booking
    except (MyBookingsProfileMissingError, MyBookingsLoadError):
        logger.info(
            "MAX my bookings cancellation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s fresh_details_loaded=%s",
            bool(platform_user_id),
            bool(_booking_record_id(booking)),
            False,
        )
        await context.send_text("Эта запись уже недоступна 🙏", keyboard=my_booking_cancel_result_keyboard())
        return
    if not is_visible_my_booking(booking, timezone_name=timezone_name):
        await context.send_text("Эта запись уже недоступна 🙏", keyboard=my_booking_cancel_result_keyboard())
        return
    can_cancel = is_booking_cancelable(booking, timezone_name=timezone_name)
    is_active = is_future_booking(booking, timezone_name=timezone_name)
    can_reschedule = is_booking_reschedulable(booking, timezone_name=timezone_name)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, booking_display_data(booking, timezone_name=timezone_name))
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_DETAILS_SCREEN)
    await context.send_text(
        format_booking_details_text(booking, timezone_name=timezone_name),
        keyboard=my_booking_details_keyboard(can_cancel=can_cancel, is_active=is_active, can_reschedule=can_reschedule),
        attachments=_booking_master_photo_attachment(booking),
    )


async def handle_my_booking_cancel_start(context: RouterContext) -> None:
    """Ask confirmation before cancelling selected booking."""

    await context.answer_callback()
    booking = _selected_booking(context)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    timezone_name = _timezone_from_state(context)
    try:
        fresh_booking = await MyBookingsService(YClientsSettingsRepository(_database_path())).get_booking_for_user(
            _current_user(context),
            yclients_record_id=_booking_record_id(booking) or "",
            platform_user_id=platform_user_id,
        )
        booking = fresh_booking
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, booking_display_data(booking, timezone_name=timezone_name))
    except (MyBookingsProfileMissingError, MyBookingsLoadError) as exc:
        logger.warning(
            "MAX my bookings cancellation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s fresh_details_loaded=%s yclients_error_category=%s http_status=%s trace_id=%s",
            bool(platform_user_id),
            bool(_booking_record_id(booking)),
            False,
            type(exc).__name__,
            None,
            None,
        )
        await context.send_text("Эта запись уже недоступна 🙏", keyboard=my_booking_cancel_result_keyboard())
        return
    if not is_booking_cancelable(booking, timezone_name=timezone_name):
        await context.send_text(MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT, keyboard=my_booking_cancel_result_keyboard())
        return
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_CONFIRM_SCREEN)
    await context.send_text(format_cancel_confirmation_text(booking, timezone_name=timezone_name), keyboard=my_booking_cancel_confirmation_keyboard())


async def handle_my_booking_cancel_confirm(context: RouterContext) -> None:
    """Cancel selected YClients booking with a simple duplicate-tap guard."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    booking = _selected_booking(context)
    if booking is None:
        await context.answer_callback()
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    record_id = _booking_record_id(booking)
    if not record_id:
        await context.answer_callback()
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    lock_key = f"booking:cancel:{platform_user_id or 'unknown'}:{record_id}"

    if _cancel_completed(context, record_id):
        logger.info(
            "MAX booking cancel diagnostic: platform_user_id_present=%s yclients_record_id_present=%s cancel_already_completed=%s",
            bool(platform_user_id),
            True,
            True,
        )
        await context.answer_callback()
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_booking_cancel_result_keyboard())
        return

    if _cancel_in_progress(context, record_id):
        logger.info(
            "MAX booking cancel diagnostic: platform_user_id_present=%s yclients_record_id_present=%s cancel_in_progress=%s",
            bool(platform_user_id),
            True,
            True,
        )
        await context.answer_callback()
        await context.send_text(MY_BOOKING_CANCEL_IN_PROGRESS_TEXT)
        return

    if not acquire_action_lock(lock_key, ttl_seconds=DEFAULT_ACTION_LOCK_TTL_SECONDS):
        logger.info(
            "MAX antiflood/action lock diagnostic: event_type=%s platform_user_id_present=%s chat_id_present=%s action=%s lock_key_type=%s lock_acquired=%s lock_active=%s ttl_seconds=%s payload_present=%s",
            context.event.update_type, bool(platform_user_id), bool(chat_id), "cancel_booking", "booking:cancel", False, True, DEFAULT_ACTION_LOCK_TTL_SECONDS, bool(context.event.callback_payload),
        )
        await context.answer_callback()
        await context.send_text(MY_BOOKING_CANCEL_IN_PROGRESS_TEXT)
        return
    _set_cancel_in_progress(context, record_id)
    await context.answer_callback()
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
        release_action_lock(lock_key)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_cancel_result_keyboard())
        return

    _log_local_cancellation(platform_user_id=platform_user_id, yclients_record_id=record_id, user=user, marker=marker)
    create_cancellation_recovery_event(
        database_path=_database_path(),
        platform_user_id=platform_user_id,
        yclients_record_id=record_id,
        user=user,
    )
    _mark_cancel_completed(context, record_id)
    _clear_cancel_in_progress(context)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_PAGE_STATE_KEY, 0)
    state.set_state_data_value(platform_user_id, chat_id, _ACTIVE_BOOKING_INDEX_STATE_KEY, 0)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_CANCEL_SUCCESS_SCREEN)
    timezone_name = _timezone_from_state(context)
    await context.send_text(format_cancel_success_text(booking, timezone_name=timezone_name), keyboard=my_booking_cancel_result_keyboard())
    await _show_my_bookings(context, push_current=False)


async def handle_my_booking_repeat_start(context: RouterContext) -> None:
    """Start repeat booking for the selected YClients record."""

    await context.answer_callback()
    source_screen = _repeat_source_screen(context)
    booking = _repeat_booking_from_history(context) if source_screen == MY_BOOKINGS_HISTORY_SCREEN else _selected_booking(context)
    if booking is not None:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_BOOKING_STATE_KEY, booking)
    if booking is None:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return
    record_id = _booking_record_id(booking)
    if not record_id:
        await context.send_text(MY_BOOKING_NOT_FOUND_TEXT, keyboard=my_bookings_keyboard())
        return

    platform_user_id = _user_id(context)
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        repeat_context = await service.prepare_repeat_context(
            _current_user(context),
            yclients_record_id=record_id,
            platform_user_id=platform_user_id,
        )
    except (MyBookingsProfileMissingError, MyBookingReschedulePrepareError, MyBookingRescheduleError) as exc:
        logger.warning(
            "MAX booking repeat diagnostic: platform_user_id_present=%s source_record_id_present=%s "
            "selected_service_id_present=%s selected_master_id_present=%s service_available=%s "
            "master_available=%s slots_count=%s create_success=%s yclients_record_id_present=%s error_class=%s http_status=%s trace_id=%s",
            bool(platform_user_id),
            bool(record_id),
            False,
            False,
            False,
            False,
            0,
            False,
            False,
            type(exc).__name__,
            getattr(exc, "status_code", None),
            getattr(exc, "trace_id", None),
        )
        await context.send_text(MY_BOOKING_REPEAT_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    except Exception as exc:  # noqa: BLE001 - never expose raw YClients/runtime details to users.
        logger.warning(
            "MAX booking repeat unexpected diagnostic: platform_user_id_present=%s source_record_id_present=%s error_class=%s",
            bool(platform_user_id),
            bool(record_id),
            type(exc).__name__,
        )
        await context.send_text(MY_BOOKING_REPEAT_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return

    service_id = _clean_state_text(repeat_context.get("service_id"))
    staff_id = _clean_state_text(repeat_context.get("staff_id"))
    if not service_id:
        await context.send_text(MY_BOOKING_REPEAT_SERVICE_UNAVAILABLE_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    state.set_current_screen(platform_user_id, _chat_id(context), state.MY_BOOKING_DETAILS_SCREEN)
    await start_repeat_booking_with_prefill(
        context,
        service_id=service_id,
        service_name=_clean_state_text(repeat_context.get("service_name")) or _clean_state_text(booking.get("service_name")) or "Услуга",
        master_id=staff_id,
        master_name=_clean_state_text(repeat_context.get("staff_name")) or _clean_state_text(booking.get("master_name")) or None,
        service_price=_clean_state_text(repeat_context.get("price")) or _clean_state_text(booking.get("price")) or None,
        service_duration=(
            f"{_clean_state_text(repeat_context.get('duration_minutes'))} мин"
            if _clean_state_text(repeat_context.get("duration_minutes"))
            else (f"{_clean_state_text(booking.get('duration_minutes'))} мин" if _clean_state_text(booking.get("duration_minutes")) else None)
        ),
        source_screen=source_screen,
    )


async def handle_my_booking_reschedule_start(context: RouterContext) -> None:
    """Start selected future booking reschedule by loading authoritative YClients details."""

    await context.answer_callback()
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
    _clear_persisted_reschedule_outcome(platform_user_id, chat_id)
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

    try:
        dates = await _load_valid_reschedule_dates(reschedule_context)
    except BookingServiceError as exc:
        logger.warning(
            "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
            "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
            "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
            "yclients_error_category=%s http_status=%s trace_id=%s",
            bool(record_id),
            bool(_clean_state_text(reschedule_context.get("service_id"))),
            bool(_clean_state_text(reschedule_context.get("staff_id"))),
            False,
            False,
            reschedule_context.get("branch_timezone"),
            0,
            0,
            0,
            0,
            False,
            exc.diagnostic.get("error_category") if hasattr(exc, "diagnostic") else None,
            exc.diagnostic.get("http_status") if hasattr(exc, "diagnostic") else None,
            exc.diagnostic.get("trace_id") if hasattr(exc, "diagnostic") else None,
        )
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_CONTEXT_STATE_KEY, reschedule_context)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_DATES_STATE_KEY, [item.isoformat() for item in dates])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, [])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
    if not dates:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
        await context.send_text(MY_BOOKING_RESCHEDULE_NO_DATES_TEXT, keyboard=my_booking_reschedule_dates_keyboard([], lambda value: format_date_button(value, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context)))))
        return
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
        await context.answer_callback()
        reschedule_context = _reschedule_context(context)
        if reschedule_context:
            await context.send_text(MY_BOOKING_RESCHEDULE_STALE_DATE_TEXT)
            await _reload_reschedule_dates(context, reschedule_context)
        else:
            await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    new_booking_date = dates[index]
    reschedule_context = _reschedule_context(context)
    service_id = _clean_state_text(reschedule_context.get("service_id"))
    staff_id = _clean_state_text(reschedule_context.get("staff_id"))
    if not service_id or not staff_id:
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return

    await context.answer_callback()
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

    if not slots:
        logger.info(
            "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
            "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
            "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
            "yclients_error_category=%s http_status=%s trace_id=%s",
            bool(reschedule_context.get("yclients_record_id")),
            bool(service_id),
            bool(staff_id),
            bool(new_booking_date),
            False,
            reschedule_context.get("branch_timezone"),
            len(dates),
            len(dates),
            0,
            0,
            True,
            None,
            None,
            None,
        )
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, None)
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, [])
        await context.send_text(MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT)
        await _reload_reschedule_dates(context, reschedule_context)
        return

    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, new_booking_date)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, slots[:_MAX_RESCHEDULE_SLOTS])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    await context.send_text(MY_BOOKING_RESCHEDULE_SLOTS_TEXT, keyboard=my_booking_reschedule_slots_keyboard(slots[:_MAX_RESCHEDULE_SLOTS], format_slot_button))


async def handle_my_booking_reschedule_slot(context: RouterContext) -> None:
    """Save selected new slot and show old/new confirmation."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    index = _payload_index(context, MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX)
    slots = _reschedule_slots(context)
    if index is None or index < 0 or index >= len(slots):
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_STALE_SLOT_TEXT)
        new_booking_date = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY))
        reschedule_context = _reschedule_context(context)
        if new_booking_date and reschedule_context:
            await _reload_reschedule_slots(context, reschedule_context, new_booking_date)
        elif reschedule_context:
            await _reload_reschedule_dates(context, reschedule_context)
        return
    selected_slot = slots[index]
    slot_time = _clean_state_text(getattr(selected_slot, "time", None))
    if not slot_time:
        await context.answer_callback()
        return

    reschedule_context = _reschedule_context(context)
    new_booking_date = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY))
    selected_datetime = _clean_state_text(getattr(selected_slot, "datetime_iso", None))
    new_datetime = build_new_datetime_iso(new_booking_date, slot_time, selected_datetime=selected_datetime)
    if _is_same_reschedule_datetime(new_datetime, reschedule_context, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))):
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_SAME_SLOT_TEXT)
        await _reload_reschedule_slots(context, reschedule_context, new_booking_date)
        return

    confirmation_data = {
        "old_date": reschedule_context.get("old_date"),
        "old_time": reschedule_context.get("old_time"),
        "new_date": format_display_date(new_booking_date, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))),
        "new_time": slot_time,
        "new_datetime": new_datetime,
        "service_name": reschedule_context.get("service_name"),
        "staff_name": reschedule_context.get("staff_name"),
    }
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, confirmation_data)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    await context.answer_callback()
    await context.send_text(format_reschedule_confirmation_text(confirmation_data), keyboard=my_booking_reschedule_confirmation_keyboard())


async def handle_my_booking_reschedule_confirm(context: RouterContext) -> None:
    """Update the selected YClients record with duplicate confirm protection."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    if state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY) is True:
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT)
        return
    persisted_result = _persisted_reschedule_result(context)
    if persisted_result:
        await context.answer_callback()
        await context.send_text(
            _clean_state_text(persisted_result.get("text")) or MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT,
            keyboard=my_booking_reschedule_result_keyboard(),
        )
        return
    reschedule_context = _reschedule_context(context)
    slot_data = _reschedule_new_slot_data(context)
    selected_booking = _selected_booking(context)
    record_id = _clean_state_text(reschedule_context.get("yclients_record_id"))
    new_datetime = _clean_state_text(slot_data.get("new_datetime"))
    service_id = _clean_state_text(reschedule_context.get("service_id"))
    staff_id = _clean_state_text(reschedule_context.get("staff_id"))
    new_date = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY))
    new_time = _clean_state_text(slot_data.get("new_time"))
    completed_old_id = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY))
    if completed_old_id and (not record_id or completed_old_id == record_id):
        await context.answer_callback()
        await context.send_text(
            _format_reschedule_success_card(selected_booking, slot_data, timezone_name=_timezone_from_state(context)),
            keyboard=my_booking_reschedule_result_keyboard(),
            attachments=_booking_master_photo_attachment(selected_booking),
        )
        return
    if not record_id or not new_datetime:
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    lock_key = f"booking:reschedule:{platform_user_id or 'unknown'}:{record_id}"
    if not acquire_action_lock(lock_key, ttl_seconds=DEFAULT_ACTION_LOCK_TTL_SECONDS):
        logger.info(
            "MAX antiflood/action lock diagnostic: event_type=%s platform_user_id_present=%s chat_id_present=%s action=%s lock_key_type=%s lock_acquired=%s lock_active=%s ttl_seconds=%s payload_present=%s",
            context.event.update_type, bool(platform_user_id), bool(chat_id), "reschedule_booking", "booking:reschedule", False, True, DEFAULT_ACTION_LOCK_TTL_SECONDS, bool(context.event.callback_payload),
        )
        await context.answer_callback()
        await context.send_text(MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT)
        return
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        current_slots = await booking_service.get_available_slots(
            yclients_service_id=service_id,
            yclients_master_id=staff_id,
            booking_date=new_date,
        )
    except BookingServiceError as exc:
        logger.warning(
            "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
            "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
            "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
            "yclients_error_category=%s http_status=%s trace_id=%s",
            bool(record_id), bool(service_id), bool(staff_id), bool(new_date), bool(new_time),
            reschedule_context.get("branch_timezone"), 0, 0, 0, 0, True,
            exc.diagnostic.get("error_category") if hasattr(exc, "diagnostic") else None,
            exc.diagnostic.get("http_status") if hasattr(exc, "diagnostic") else None,
            exc.diagnostic.get("trace_id") if hasattr(exc, "diagnostic") else None,
        )
        await context.answer_callback()
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return
    service = MyBookingsService(YClientsSettingsRepository(_database_path()))
    try:
        reschedule_context = await service.revalidate_reschedule_source(
            _current_user(context),
            reschedule_context=reschedule_context,
            platform_user_id=platform_user_id,
        )
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_CONTEXT_STATE_KEY, reschedule_context)
    except (MyBookingsProfileMissingError, MyBookingReschedulePrepareError, MyBookingRescheduleError) as exc:
        await context.answer_callback()
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return
    if _is_same_reschedule_datetime(new_datetime, reschedule_context, timezone_name=str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))):
        await context.answer_callback()
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        await context.send_text(MY_BOOKING_RESCHEDULE_SAME_SLOT_TEXT)
        await _reload_reschedule_slots(context, reschedule_context, new_date)
        return
    if not _slot_available(current_slots, new_time, new_datetime):
        await context.answer_callback()
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
        state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, current_slots[:_MAX_RESCHEDULE_SLOTS])
        logger.info(
            "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
            "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
            "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
            "yclients_error_category=%s http_status=%s trace_id=%s",
            bool(record_id), bool(service_id), bool(staff_id), bool(new_date), bool(new_time),
            reschedule_context.get("branch_timezone"), 0, 0, len(current_slots), len(current_slots), True, None, None, None,
        )
        release_action_lock(lock_key)
        await context.send_text(MY_BOOKING_RESCHEDULE_STALE_SLOT_TEXT)
        await _reload_reschedule_slots(context, reschedule_context, new_date)
        return

    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, True)
    await context.answer_callback()
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
        partial_new_id = _clean_state_text(getattr(exc, "diagnostic", {}).get("new_record_id") if hasattr(exc, "diagnostic") else None)
        if partial_new_id:
            state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY, record_id)
            state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_RECORD_STATE_KEY, partial_new_id)
            _store_reschedule_outcome(
                platform_user_id,
                chat_id,
                outcome=_RESCHEDULE_OUTCOME_PARTIAL_FAILURE,
                text=exc.user_message,
                old_record_id=record_id,
                new_record_id=partial_new_id,
            )
        release_action_lock(lock_key)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return

    new_record_id = _clean_state_text(result.get("new_record_id")) if isinstance(result, dict) else ""
    _log_local_reschedule(
        platform_user_id=platform_user_id,
        old_record_id=record_id,
        new_record_id=new_record_id,
        user=_current_user(context),
        timezone_name=_timezone_from_state(context),
    )
    success_text = _format_reschedule_success_card(selected_booking, slot_data, timezone_name=_timezone_from_state(context))
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY, record_id)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_RECORD_STATE_KEY, new_record_id)
    _store_reschedule_outcome(
        platform_user_id,
        chat_id,
        outcome=_RESCHEDULE_OUTCOME_SUCCESS,
        text=success_text,
        old_record_id=record_id,
        new_record_id=new_record_id,
    )
    _clear_transient_reschedule_state(platform_user_id, chat_id)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_STATE_KEY, [])
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_PAGE_STATE_KEY, 0)
    release_action_lock(lock_key)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_SUCCESS_SCREEN)
    await context.send_text(
        success_text,
        keyboard=my_booking_reschedule_result_keyboard(),
        attachments=_booking_master_photo_attachment(selected_booking),
    )


async def handle_my_bookings_back(context: RouterContext) -> None:
    """Return from cancellation confirmation to details, or from details to the list."""

    await context.answer_callback()
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen in {state.MY_BOOKING_CANCEL_CONFIRM_SCREEN, state.MY_BOOKING_RESCHEDULE_DATES_SCREEN}:
        booking = _selected_booking(context)
        if booking is not None:
            state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_DETAILS_SCREEN)
            timezone_name = _timezone_from_state(context)
            await context.send_text(
                format_booking_details_text(booking, timezone_name=timezone_name),
                keyboard=my_booking_details_keyboard(
                    can_cancel=is_booking_cancelable(booking, timezone_name=timezone_name),
                    is_active=is_future_booking(booking, timezone_name=timezone_name),
                    can_reschedule=is_booking_reschedulable(booking, timezone_name=timezone_name),
                ),
                attachments=_booking_master_photo_attachment(booking),
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
        MY_BOOKINGS_HISTORY_SCREEN,
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
        result = await service.get_bookings_for_user(user, platform_user_id=platform_user_id)
    except MyBookingsProfileMissingError:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        await context.send_text(MY_BOOKINGS_NO_PROFILE_TEXT, keyboard=my_bookings_keyboard())
        return
    except MyBookingsLoadError as exc:
        diagnostic = _my_bookings_flow_diagnostic(context, user, exc)
        logger.warning("MAX my bookings runtime error: %s", diagnostic)
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_ERROR_SCREEN)
        is_rate_limit = diagnostic.get("error_category") == "rate_limit" or diagnostic.get("error_type") == "YClientsRateLimitError"
        await context.send_text(
            MY_BOOKINGS_RATE_LIMIT_TEXT if is_rate_limit else MY_BOOKINGS_LOAD_ERROR_TEXT,
            keyboard=my_bookings_rate_limit_keyboard() if is_rate_limit else my_bookings_keyboard(),
        )
        await _send_my_bookings_dev_error_card_if_needed(context, user, diagnostic)
        return

    rendered_split = split_bookings_by_period(result.bookings, timezone_name=result.branch_timezone)
    booking_state_items = [booking_display_data(item, timezone_name=result.branch_timezone) for item in result.bookings]
    active_state_items = [booking_display_data(item, timezone_name=result.branch_timezone) for item in rendered_split.upcoming]
    past_state_items = [booking_display_data(item, timezone_name=result.branch_timezone) for item in rendered_split.past]
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_STATE_KEY, booking_state_items)
    state.set_state_data_value(platform_user_id, chat_id, _ACTIVE_BOOKINGS_STATE_KEY, active_state_items)
    state.set_state_data_value(platform_user_id, chat_id, _PAST_BOOKINGS_STATE_KEY, past_state_items)
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_TIMEZONE_STATE_KEY, result.branch_timezone)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _BOOKINGS_PAGE_STATE_KEY, 0)
    _clear_cancel_in_progress(context)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)

    rendered_buttons_count = min(len(rendered_split.upcoming), _MAX_BOOKING_BUTTONS)
    logger.info(
        "MAX my bookings list diagnostic: platform_user_id_present=%s phone_present_masked=%s "
        "yclients_client_id_present=%s raw_records_count=%s after_status_filter_count=%s upcoming_count=%s "
        "past_count=%s rendered_buttons_count=%s state_map_size=%s page=%s page_size=%s branch_timezone=%s",
        bool(platform_user_id),
        bool(result.phone_exists),
        bool(result.yclients_client_id),
        len(result.bookings),
        len(result.bookings),
        len(rendered_split.upcoming),
        len(rendered_split.past),
        rendered_buttons_count,
        len(booking_state_items),
        0,
        _MAX_BOOKING_BUTTONS,
        result.branch_timezone,
    )

    if not rendered_split.upcoming:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_EMPTY_SCREEN)
        await context.send_text(
            MY_BOOKINGS_EMPTY_TEXT,
            keyboard=my_bookings_empty_keyboard(),
        )
        return
    else:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKINGS_SCREEN)

    nearest_booking = active_state_items[0]
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_BOOKING_STATE_KEY, nearest_booking)
    await context.send_text(
        format_booking_details_text(
            nearest_booking,
            timezone_name=result.branch_timezone,
            title="📅 Моя ближайшая запись",
        ),
        keyboard=my_booking_entry_keyboard(
            can_cancel=is_booking_cancelable(nearest_booking, timezone_name=result.branch_timezone),
            show_all=len(active_state_items) > 1,
            can_reschedule=is_booking_reschedulable(nearest_booking, timezone_name=result.branch_timezone),
        ),
        attachments=_booking_master_photo_attachment(nearest_booking),
    )


async def _load_valid_reschedule_dates(reschedule_context: dict[str, Any]) -> list[Any]:
    service_id = _clean_state_text(reschedule_context.get("service_id"))
    staff_id = _clean_state_text(reschedule_context.get("staff_id"))
    timezone_name = str(reschedule_context.get("branch_timezone") or DEFAULT_BRANCH_TIMEZONE)
    if not service_id or not staff_id:
        logger.info(
            "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
            "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
            "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
            "yclients_error_category=%s http_status=%s trace_id=%s",
            bool(reschedule_context.get("yclients_record_id")), bool(service_id), bool(staff_id), False, False,
            timezone_name, 0, 0, 0, 0, False, None, None, None,
        )
        return []
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    dates = await booking_service.get_available_dates_for_selection(
        yclients_service_id=service_id,
        yclients_master_id=staff_id,
        days=_MAX_RESCHEDULE_DATES,
    )
    logger.info(
        "MAX reschedule availability diagnostic: yclients_record_id_present=%s service_id_present=%s "
        "staff_id_present=%s selected_date_present=%s selected_time_present=%s branch_timezone=%s "
        "raw_dates_count=%s valid_dates_count=%s raw_slots_count=%s valid_slots_count=%s is_stale=%s "
        "yclients_error_category=%s http_status=%s trace_id=%s",
        bool(reschedule_context.get("yclients_record_id")), True, True, False, False, timezone_name,
        _MAX_RESCHEDULE_DATES, len(dates), 0, 0, False, None, None, None,
    )
    return dates


async def _reload_reschedule_dates(context: RouterContext, reschedule_context: dict[str, Any]) -> None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    try:
        dates = await _load_valid_reschedule_dates(reschedule_context)
    except BookingServiceError as exc:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_DATES_STATE_KEY, [item.isoformat() for item in dates])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, [])
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    timezone_name = str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))
    await context.send_text(
        MY_BOOKING_RESCHEDULE_DATES_TEXT if dates else MY_BOOKING_RESCHEDULE_NO_DATES_TEXT,
        keyboard=my_booking_reschedule_dates_keyboard(dates, lambda value: format_date_button(value, timezone_name=timezone_name)),
    )


def _slot_available(slots: list[Any], selected_time: str, selected_datetime: str) -> bool:
    normalized_selected_time = _slot_time_from_values(selected_time, selected_datetime)
    if not normalized_selected_time:
        return False
    for slot in slots:
        slot_time = _slot_time_from_values(_slot_value(slot, "time"), _slot_value(slot, "datetime_iso") or _slot_value(slot, "datetime"))
        if slot_time == normalized_selected_time:
            return True
    return False


def _slot_time_from_values(slot_time: Any, datetime_value: Any = None) -> str:
    raw_time = _clean_state_text(slot_time)
    if len(raw_time) >= 5 and raw_time[2] == ":" and raw_time[:2].isdigit() and raw_time[3:5].isdigit():
        return raw_time[:5]
    raw_datetime = _clean_state_text(datetime_value)
    for separator in ("T", " "):
        if separator in raw_datetime:
            candidate = raw_datetime.split(separator, 1)[1][:5]
            if len(candidate) == 5 and candidate[2] == ":" and candidate.replace(":", "").isdigit():
                return candidate
    return ""


def _slot_value(slot: Any, key: str) -> Any:
    if isinstance(slot, dict):
        return slot.get(key)
    return getattr(slot, key, None)


async def _show_reschedule_dates_from_state(context: RouterContext) -> None:
    dates = _reschedule_dates(context)
    reschedule_context = _reschedule_context(context)
    timezone_name = str(reschedule_context.get("branch_timezone") or _timezone_from_state(context))
    state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    await context.send_text(
        MY_BOOKING_RESCHEDULE_DATES_TEXT,
        keyboard=my_booking_reschedule_dates_keyboard(dates, lambda value: format_date_button(value, timezone_name=timezone_name)),
    )


async def _reload_reschedule_slots(context: RouterContext, reschedule_context: dict[str, Any], selected_date: str) -> None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    service_id = _clean_state_text(reschedule_context.get("service_id"))
    staff_id = _clean_state_text(reschedule_context.get("staff_id"))
    if not service_id or not staff_id or not selected_date:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT, keyboard=my_booking_reschedule_result_keyboard())
        return
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slots = await booking_service.get_available_slots(
            yclients_service_id=service_id,
            yclients_master_id=staff_id,
            booking_date=selected_date,
        )
    except BookingServiceError as exc:
        state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_ERROR_SCREEN)
        await context.send_text(exc.user_message, keyboard=my_booking_reschedule_result_keyboard())
        return
    fresh_slots = _normalize_reschedule_slots(slots)[:_MAX_RESCHEDULE_SLOTS]
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, selected_date)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, fresh_slots)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)
    state.set_current_screen(platform_user_id, chat_id, state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    await context.send_text(
        MY_BOOKING_RESCHEDULE_SLOTS_TEXT if fresh_slots else MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT,
        keyboard=my_booking_reschedule_slots_keyboard(fresh_slots, format_slot_button),
    )


def _normalize_reschedule_slots(slots: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for slot in slots:
        key = _slot_time_from_values(_slot_value(slot, "time"), _slot_value(slot, "datetime_iso") or _slot_value(slot, "datetime"))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(slot)
    return sorted(result, key=lambda item: _slot_time_from_values(_slot_value(item, "time"), _slot_value(item, "datetime_iso") or _slot_value(item, "datetime")))


async def _show_reschedule_slots_from_state(context: RouterContext) -> None:
    slots = _reschedule_slots(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    text = MY_BOOKING_RESCHEDULE_SLOTS_TEXT if slots else MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT
    await context.send_text(text, keyboard=my_booking_reschedule_slots_keyboard(slots, format_slot_button))




def _is_same_reschedule_datetime(new_datetime: str, reschedule_context: dict[str, Any], *, timezone_name: str) -> bool:
    old_raw = _clean_state_text(reschedule_context.get("old_datetime") or reschedule_context.get("datetime"))
    if not old_raw or not new_datetime:
        return False
    old_dt = _parse_reschedule_datetime(old_raw, timezone_name=timezone_name)
    new_dt = _parse_reschedule_datetime(new_datetime, timezone_name=timezone_name)
    if old_dt is None or new_dt is None:
        return False
    return old_dt.replace(second=0, microsecond=0) == new_dt.replace(second=0, microsecond=0)


def _parse_reschedule_datetime(value: str, *, timezone_name: str):
    parsed = parse_booking_datetime({"datetime": value}, timezone_name=timezone_name)
    return parsed


def _clear_transient_reschedule_state(platform_user_id: str | None, chat_id: str | None) -> None:
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_CONTEXT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_DATES_STATE_KEY, [])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_SLOTS_STATE_KEY, [])
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_DATE_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_SLOT_STATE_KEY, None)



def _persisted_reschedule_result(context: RouterContext) -> dict[str, Any] | None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    outcome = _clean_state_text(state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_OUTCOME_STATE_KEY))
    result = state.get_state_data_value(platform_user_id, chat_id, _RESCHEDULE_RESULT_STATE_KEY)
    if outcome not in {_RESCHEDULE_OUTCOME_SUCCESS, _RESCHEDULE_OUTCOME_PARTIAL_FAILURE}:
        return None
    if not isinstance(result, dict):
        return None
    text = _clean_state_text(result.get("text"))
    if not text:
        return None
    if _clean_state_text(result.get("outcome")) != outcome:
        return None
    return result


def _store_reschedule_outcome(
    platform_user_id: str | None,
    chat_id: str | None,
    *,
    outcome: str,
    text: str,
    old_record_id: str,
    new_record_id: str,
) -> None:
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_OUTCOME_STATE_KEY, outcome)
    state.set_state_data_value(
        platform_user_id,
        chat_id,
        _RESCHEDULE_RESULT_STATE_KEY,
        {
            "outcome": outcome,
            "text": text,
            "old_record_id": old_record_id,
            "new_record_id": new_record_id,
        },
    )


def _clear_persisted_reschedule_outcome(platform_user_id: str | None, chat_id: str | None) -> None:
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_OUTCOME_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_RESULT_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _RESCHEDULE_NEW_RECORD_STATE_KEY, None)

def _booking_master_photo_attachment(booking: Any) -> list[dict[str, object]] | None:
    """Return configured MAX master photo attachment for a booking details card."""

    staff_id = _booking_staff_id(booking)
    if not staff_id:
        return None
    try:
        service = MasterPhotosService(
            MasterPhotosRepository(_database_path()),
            YClientsSettingsRepository(_database_path()),
        )
        return service.photo_attachments(staff_id)
    except Exception as exc:  # noqa: BLE001 - master photo must not block booking details.
        logger.warning("My bookings master photo skipped safely: error_class=%s", type(exc).__name__)
        return None


def _booking_staff_id(booking: Any) -> str | None:
    if booking is None:
        return None
    value = getattr(booking, "yclients_staff_id", None)
    if value:
        return _clean_state_text(value)
    if isinstance(booking, dict):
        return _clean_state_text(booking.get("yclients_staff_id") or booking.get("staff_id") or booking.get("master_id"))
    return None

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
    return build_yclients_action_comment(
        _CANCELLATION_MARKER_PREFIX,
        timezone_name=timezone_name,
        action_type="booking_cancel",
    )


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


def _booking_by_payload(context: RouterContext) -> Any | None:
    payload = context.event.callback_payload or ""
    raw_index = payload.removeprefix(MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX)
    if not raw_index.isdigit():
        return None
    bookings = _bookings_from_state(context)
    index = int(raw_index)
    if index < 0 or index >= len(bookings):
        return None
    return bookings[index]


def _repeat_source_screen(context: RouterContext) -> str:
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    return current if current in _SUPPORTED_REPEAT_SOURCE_SCREENS else state.MY_BOOKING_DETAILS_SCREEN


def _repeat_booking_from_history(context: RouterContext) -> Any | None:
    for item in reversed(_bookings_from_state(context)):
        if _booking_record_id(item):
            return item
    return None

def _selected_booking(context: RouterContext) -> Any | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _SELECTED_BOOKING_STATE_KEY)
    return value if isinstance(value, dict) else None


def _bookings_from_state(context: RouterContext) -> list[dict[str, Any]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BOOKINGS_STATE_KEY)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _active_bookings_from_state(context: RouterContext) -> list[dict[str, Any]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _ACTIVE_BOOKINGS_STATE_KEY)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _past_bookings_from_state(context: RouterContext) -> list[dict[str, Any]]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _PAST_BOOKINGS_STATE_KEY)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _timezone_from_state(context: RouterContext) -> str:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BOOKINGS_TIMEZONE_STATE_KEY)
    return normalize_branch_timezone(value if isinstance(value, str) else DEFAULT_BRANCH_TIMEZONE, flow="my_bookings", operation="timezone_from_state")


def _booking_record_id(booking: Any) -> str | None:
    if hasattr(booking, "yclients_record_id"):
        value = getattr(booking, "yclients_record_id", None)
    elif isinstance(booking, dict):
        value = booking.get("yclients_record_id") or booking.get("record_id") or booking.get("id")
    else:
        value = None
    return str(value).strip() if value is not None and str(value).strip() else None


def _log_local_reschedule(*, platform_user_id: str | None, old_record_id: str, new_record_id: str, user: Any, timezone_name: str) -> None:
    if not platform_user_id:
        return
    try:
        repo = PlatformAttributionRepository(_database_path())
        if new_record_id:
            repo.create_record(
                platform_user_id=platform_user_id,
                yclients_record_id=new_record_id,
                yclients_client_id=user.yclients_client_id if user else None,
                marker=build_yclients_action_comment(
                    "Клиент перенёс запись из MAX бота",
                    timezone_name=timezone_name,
                    action_type="local_reschedule_new",
                ),
                platform=PLATFORM_MAX,
            )
        repo.create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=old_record_id,
            yclients_client_id=user.yclients_client_id if user else None,
            marker=build_yclients_action_comment(
                "Запись перенесена из MAX бота",
                timezone_name=timezone_name,
                action_type="local_reschedule_old",
            ),
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


def _my_bookings_flow_diagnostic(context: RouterContext, user: Any, exc: Exception) -> dict[str, Any]:
    service_diagnostic = getattr(exc, "diagnostic", None)
    if isinstance(service_diagnostic, dict) and service_diagnostic:
        result = dict(service_diagnostic)
        result.setdefault("callback", context.event.callback_payload or "n/a")
        return result
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return {
        "function": "max_barbershop_bot.flows.my_bookings._show_my_bookings",
        "callback": context.event.callback_payload or "n/a",
        "max_user_id": context.event.max_user_id or "n/a",
        "platform_user_id": context.event.platform_user_id or "n/a",
        "has_yclients_client_id": bool(user and getattr(user, "yclients_client_id", None)),
        "has_phone": bool(user and getattr(user, "phone", None)),
        "user_role": getattr(user, "role", None) or "n/a",
        "yclients_company_id_present": "unknown",
        "endpoint_name": "list_user_bookings",
        "request_mode": "unknown",
        "yclients_records_count": "unknown",
        "parsed_records_count": "unknown",
        "active_records_count": "unknown",
        "skipped_malformed_count": "unknown",
        "error_type": type(exc).__name__,
        "error_message_short": str(exc)[:180],
        "traceback_last_5_lines": "".join(tb_lines[-5:])[:900],
        "duration_ms": "unknown",
    }


async def _send_my_bookings_dev_error_card_if_needed(
    context: RouterContext,
    user: Any,
    diagnostic: dict[str, Any],
) -> None:
    if not (user and is_developer(getattr(user, "role", None))):
        return
    if diagnostic.get("error_category") == "rate_limit" or diagnostic.get("error_type") == "YClientsRateLimitError":
        text = "\n".join(
            [
                "MAX YClients rate limit diagnostic:",
                f"function: {diagnostic.get('function', 'n/a')}",
                f"endpoint_name: {diagnostic.get('endpoint_name', 'n/a')}",
                f"request_mode: {diagnostic.get('request_mode', 'n/a')}",
                f"retry_after_seconds: {diagnostic.get('retry_after_seconds', 'n/a')}",
                f"max_user_id: {diagnostic.get('max_user_id', 'n/a')}",
                f"has_yclients_client_id: {diagnostic.get('has_yclients_client_id', 'n/a')}",
                f"has_phone: {diagnostic.get('has_phone', 'n/a')}",
                f"action: {diagnostic.get('action', 'my_bookings_open')}",
                f"duration_ms: {diagnostic.get('duration_ms', 'n/a')}",
            ]
        )
    else:
        tb_tail = str(diagnostic.get("traceback_last_5_lines") or "")
        tb_last_3 = "\n".join(tb_tail.splitlines()[-3:])[:600] or "—"
        text = "\n".join(
            [
                "🛠️ MAX my bookings runtime error:",
                f"function: {diagnostic.get('function', 'n/a')}",
                f"error_type: {diagnostic.get('error_type', 'n/a')}",
                f"message: {diagnostic.get('error_message_short', '—')}",
                f"endpoint: {diagnostic.get('endpoint_name', 'n/a')}",
                f"request_mode: {diagnostic.get('request_mode', 'n/a')}",
                "counters: "
                f"records={diagnostic.get('yclients_records_count', 'n/a')}, "
                f"parsed={diagnostic.get('parsed_records_count', 'n/a')}, "
                f"active={diagnostic.get('active_records_count', 'n/a')}, "
                f"skipped={diagnostic.get('skipped_malformed_count', 'n/a')}",
                f"traceback_last_3_lines:\n{tb_last_3}",
            ]
        )
    await context.send_text(text[:1800])
