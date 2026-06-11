"""Booking flow for choosing YClients services and masters."""

from __future__ import annotations

import logging
from datetime import datetime
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.integrations.yclients.utils import MAX_BOOKING_COMMENT_MARKER
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.booking import (
    BookingCatalog,
    BookingService,
    BookingServiceError,
    BookingMasterItem,
    BookingServiceItem,
    BookingSlotItem,
    format_booking_success,
    format_booking_summary,
    format_date_button,
    format_master_title,
    format_service_title,
    format_slot_button,
    has_available_masters,
    has_available_services,
)
from max_barbershop_bot.services.master_photos import MasterPhotosService
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.services.reminders import send_immediate_confirmation
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_CATEGORY_NEXT_PAYLOAD,
    BOOKING_CATEGORY_PAYLOAD_PREFIX,
    BOOKING_CATEGORY_PREV_PAYLOAD,
    BOOKING_CONFIRM_PAYLOAD,
    BOOKING_MASTER_NEXT_PAYLOAD,
    BOOKING_DATE_PAYLOAD_PREFIX,
    BOOKING_MASTER_PAYLOAD_PREFIX,
    BOOKING_MASTER_PREV_PAYLOAD,
    BOOKING_SERVICE_NEXT_PAYLOAD,
    BOOKING_SERVICE_PAYLOAD_PREFIX,
    BOOKING_SERVICE_PREV_PAYLOAD,
    BOOKING_SLOT_PAYLOAD_PREFIX,
    MENU_BOOKING_PAYLOAD,
    booking_categories_keyboard,
    booking_dates_keyboard,
    booking_masters_keyboard,
    booking_services_keyboard,
    booking_slots_keyboard,
    booking_confirmation_keyboard,
    booking_success_keyboard,
    navigation_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BOOKING_CATEGORY_EMPTY_TEXT,
    BOOKING_CATEGORY_TEXT,
    BOOKING_CONFIRMATION_MISSING_DATA_TEXT,
    BOOKING_CREATE_ERROR_TEXT,
    BOOKING_CREATE_IN_PROGRESS_TEXT,
    BOOKING_EMPTY_TEXT,
    BOOKING_MASTER_TEXT,
    BOOKING_MASTERS_EMPTY_TEXT,
    BOOKING_SERVICE_TEXT,
    BOOKING_SLOTS_EMPTY_TEXT,
    BOOKING_SLOTS_TEXT,
)

logger = logging.getLogger(__name__)

_MAX_CALLBACK_ITEMS = 20
_CATALOG_STATE_KEY = "booking_catalog"
_CATEGORY_MAP_STATE_KEY = "booking_category_payloads"
_SERVICE_MAP_STATE_KEY = "booking_service_payloads"
_MASTER_MAP_STATE_KEY = "booking_master_payloads"
_DATE_MAP_STATE_KEY = "booking_date_payloads"
_SLOT_MAP_STATE_KEY = "booking_slot_payloads"
_MASTERS_STATE_KEY = "booking_masters"
_DATES_STATE_KEY = "booking_dates"
_SLOTS_STATE_KEY = "booking_slots"
_CATEGORY_PAGE_STATE_KEY = "booking_category_page"
_SERVICE_PAGE_STATE_KEY = "booking_service_page"
_MASTER_PAGE_STATE_KEY = "booking_master_page"
_SELECTED_CATEGORY_STATE_KEY = "selected_yclients_category_id"
_SELECTED_CATEGORY_NAME_STATE_KEY = "selected_category_name"
_SELECTED_SERVICE_STATE_KEY = "selected_yclients_service_id"
_SELECTED_SERVICE_NAME_STATE_KEY = "selected_service_name"
_SELECTED_SERVICE_PRICE_STATE_KEY = "selected_service_price"
_SELECTED_SERVICE_DURATION_STATE_KEY = "selected_service_duration"
_SELECTED_MASTER_STATE_KEY = "selected_yclients_master_id"
_SELECTED_MASTER_NAME_STATE_KEY = "selected_master_name"
_SELECTED_MASTER_SPECIALIZATION_STATE_KEY = "selected_master_specialization"
_SELECTED_MASTER_RATING_STATE_KEY = "selected_master_rating"
_SELECTED_DATE_STATE_KEY = "selected_booking_date"
_SELECTED_SLOT_TIME_STATE_KEY = "selected_booking_slot_time"
_SELECTED_SLOT_DATETIME_STATE_KEY = "selected_booking_datetime"
_SELECTED_SLOT_RAW_STATE_KEY = "selected_booking_slot_raw"
_BOOKING_DATE_STATE_KEY = "booking_date"
_BOOKING_SLOT_STATE_KEY = "booking_slot"
_BOOKING_CREATION_IN_PROGRESS_STATE_KEY = "booking_creation_in_progress"
_BOOKING_COMPLETED_RECORD_ID_STATE_KEY = "booking_completed_record_id"


def register_booking_routes(router: Router) -> None:
    """Register booking category/service callbacks."""

    router.on_callback(MENU_BOOKING_PAYLOAD, handle_booking_start)
    router.on_callback(BOOKING_BACK_PAYLOAD, handle_booking_back)
    router.on_callback(BOOKING_CONFIRM_PAYLOAD, handle_booking_confirm)
    router.on_callback(BOOKING_CATEGORY_PREV_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_CATEGORY_NEXT_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_SERVICE_PREV_PAYLOAD, handle_booking_service_page)
    router.on_callback(BOOKING_SERVICE_NEXT_PAYLOAD, handle_booking_service_page)
    router.on_callback(BOOKING_MASTER_PREV_PAYLOAD, handle_booking_master_page)
    router.on_callback(BOOKING_MASTER_NEXT_PAYLOAD, handle_booking_master_page)
    for index in range(_MAX_CALLBACK_ITEMS):
        router.on_callback(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}{index}", handle_booking_category)
        router.on_callback(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}{index}", handle_booking_service)
        router.on_callback(f"{BOOKING_MASTER_PAYLOAD_PREFIX}{index}", handle_booking_master)
        router.on_callback(f"{BOOKING_DATE_PAYLOAD_PREFIX}{index}", handle_booking_date)
        router.on_callback(f"{BOOKING_SLOT_PAYLOAD_PREFIX}{index}", handle_booking_slot)


async def handle_booking_start(context: RouterContext) -> None:
    """Open the first real booking step from the main menu."""

    await context.answer_callback("Открываем запись ✂️")
    await _open_booking_catalog(context)


async def handle_booking_category(context: RouterContext) -> None:
    """Show services from the selected category."""

    await context.answer_callback("Выбираем категорию ✂️")
    category_id = _mapped_value(context, _CATEGORY_MAP_STATE_KEY, context.event.callback_payload)
    catalog = _catalog(context)
    if not category_id or catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return

    category = next((item for item in catalog.categories if item.yclients_category_id == category_id), None)
    services = [item for item in catalog.services if item.yclients_category_id == category_id]
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_STATE_KEY, category_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_NAME_STATE_KEY, category.title if category else None)
    await _show_services(context, services, category_title=category.title if category else None)


async def handle_booking_service(context: RouterContext) -> None:
    """Save selected service and show the master selection step."""

    await context.answer_callback("Услуга выбрана ✅")
    service_id = _mapped_value(context, _SERVICE_MAP_STATE_KEY, context.event.callback_payload)
    catalog = _catalog(context)
    if not service_id or catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return

    service = next((item for item in catalog.services if item.yclients_service_id == service_id), None)
    if service is None:
        await _open_booking_catalog(context, push_current=False)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, service.yclients_service_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, service.title)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_PRICE_STATE_KEY, _service_price_text(service))
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_DURATION_STATE_KEY, service.duration)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, None)
    await _open_booking_masters(context, service.yclients_service_id)


async def handle_booking_category_page(context: RouterContext) -> None:
    """Move between category pages using short registered payloads."""

    await context.answer_callback("Листаем категории ✂️")
    catalog = _catalog(context)
    if catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return
    current_page = _int_state_value(context, _CATEGORY_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_CATEGORY_PREV_PAYLOAD else 1
    await _show_categories(context, catalog.categories, page=max(0, current_page + delta), push_current=False)


async def handle_booking_master_page(context: RouterContext) -> None:
    """Move between master pages using short registered payloads."""

    await context.answer_callback("Листаем мастеров 💈")
    masters = _masters(context)
    if masters is None:
        service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
        if isinstance(service_id, str) and service_id:
            await _open_booking_masters(context, service_id, push_current=False)
            return
        await _open_booking_catalog(context, push_current=False)
        return
    current_page = _int_state_value(context, _MASTER_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_MASTER_PREV_PAYLOAD else 1
    await _show_masters(context, masters, page=max(0, current_page + delta), push_current=False)


async def handle_booking_service_page(context: RouterContext) -> None:
    """Move between service pages using short registered payloads."""

    await context.answer_callback("Листаем услуги ✂️")
    catalog = _catalog(context)
    if catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return
    category_id = _state_value(context, _SELECTED_CATEGORY_STATE_KEY)
    if isinstance(category_id, str) and category_id:
        category = next((item for item in catalog.categories if item.yclients_category_id == category_id), None)
        services = [item for item in catalog.services if item.yclients_category_id == category_id]
    else:
        category = None
        services = catalog.services
    current_page = _int_state_value(context, _SERVICE_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_SERVICE_PREV_PAYLOAD else 1
    await _show_services(
        context,
        services,
        category_title=category.title if category else None,
        page=max(0, current_page + delta),
        push_current=False,
    )


async def handle_booking_master(context: RouterContext) -> None:
    """Save selected master and show date selection."""

    await context.answer_callback("Мастер выбран ✅")
    master_id = _mapped_value(context, _MASTER_MAP_STATE_KEY, context.event.callback_payload)
    masters = _masters(context)
    if not master_id or masters is None:
        service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
        if isinstance(service_id, str) and service_id:
            await _open_booking_masters(context, service_id, push_current=False)
            return
        await _open_booking_catalog(context, push_current=False)
        return

    master = next((item for item in masters if item.yclients_master_id == master_id), None)
    if master is None:
        await _show_masters(context, masters, push_current=False)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, master.yclients_master_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, master.title)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, master.specialization)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, master.rating)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, None)
    await _show_booking_dates(context)


async def handle_booking_date(context: RouterContext) -> None:
    """Save selected date and load YClients slots."""

    await context.answer_callback("Выбираем дату 📅")
    booking_date = _mapped_value(context, _DATE_MAP_STATE_KEY, context.event.callback_payload)
    if not booking_date:
        await _show_booking_dates(context, push_current=False)
        return
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, booking_date)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_DATE_STATE_KEY, booking_date)
    await _open_booking_slots(context, booking_date)


async def handle_booking_slot(context: RouterContext) -> None:
    """Save selected slot and show the next-step placeholder."""

    await context.answer_callback("Время выбрано ✅")
    slot_time = _mapped_value(context, _SLOT_MAP_STATE_KEY, context.event.callback_payload)
    slots = _slots(context)
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    if not slot_time or slots is None or not isinstance(booking_date, str):
        if isinstance(booking_date, str) and booking_date:
            await _open_booking_slots(context, booking_date, push_current=False)
            return
        await _show_booking_dates(context, push_current=False)
        return

    slot = next((item for item in slots if item.time == slot_time), None)
    if slot is None:
        await _open_booking_slots(context, booking_date, push_current=False)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, slot.time)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, slot.time)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, slot.datetime_iso)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, slot.raw)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    await _show_booking_confirmation(context)


async def handle_booking_confirm(context: RouterContext) -> None:
    """Create a real YClients booking after the final confirmation tap."""

    if _state_value(context, _BOOKING_CREATION_IN_PROGRESS_STATE_KEY) is True:
        await context.answer_callback(BOOKING_CREATE_IN_PROGRESS_TEXT)
        return
    if _state_value(context, _BOOKING_COMPLETED_RECORD_ID_STATE_KEY):
        await context.answer_callback("Запись уже создана ✅")
        return

    booking_data = _booking_state_snapshot(context)
    user = _current_user(context)
    if user is None or not user.phone or not booking_data.get("selected_service_id") or not booking_data.get("selected_master_id") or not booking_data.get("selected_date") or not booking_data.get("selected_slot_time"):
        await context.answer_callback("Не хватает данных 🙏")
        await context.send_text(BOOKING_CONFIRMATION_MISSING_DATA_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    await context.answer_callback("Создаём запись ✂️")
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, True)
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        created = await booking_service.create_booking(
            yclients_service_id=str(booking_data["selected_service_id"]),
            yclients_master_id=str(booking_data["selected_master_id"]),
            booking_date=str(booking_data["selected_date"]),
            booking_slot=str(booking_data["selected_slot_time"]),
            selected_datetime=_optional_state_text(booking_data.get("selected_datetime")),
            client_name=_user_full_name(user),
            client_phone=user.phone,
            comment=MAX_BOOKING_COMMENT_MARKER,
        )
    except BookingServiceError as exc:
        logger.warning(
            "Booking create failed: operation=confirm_booking service_id=%s master_id=%s date=%s slot_time=%s error_class=%s",
            booking_data.get("selected_service_id"),
            booking_data.get("selected_master_id"),
            booking_data.get("selected_date"),
            booking_data.get("selected_slot_time"),
            type(exc).__name__,
        )
        await context.send_text(exc.user_message or BOOKING_CREATE_ERROR_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
    finally:
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)

    if 'created' not in locals():
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, created.yclients_record_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    _save_attribution_safely(
        platform_user_id=user.platform_user_id,
        yclients_record_id=created.yclients_record_id,
        yclients_client_id=created.yclients_client_id or user.yclients_client_id,
    )
    await _send_immediate_confirmation_safely(context, created=created, user=user, booking_data=booking_data)


async def handle_booking_back(context: RouterContext) -> None:
    """Navigate back inside booking without affecting other flows."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen == state.BOOKING_CATEGORIES_SCREEN:
        await show_home(context)
        return
    if current_screen == state.BOOKING_SERVICES_SCREEN:
        catalog = _catalog(context)
        if catalog and catalog.categories:
            await _show_categories(context, catalog.categories, push_current=False)
            return
        await show_home(context)
        return
    if current_screen == state.BOOKING_MASTERS_SCREEN:
        await _show_selected_category_services(context)
        return
    if current_screen == state.BOOKING_DATES_SCREEN:
        masters = _masters(context)
        if masters is not None:
            await _show_masters(context, masters, push_current=False)
            return
        service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
        if isinstance(service_id, str) and service_id:
            await _open_booking_masters(context, service_id, push_current=False)
            return
        await _show_selected_category_services(context)
        return
    if current_screen == state.BOOKING_SLOTS_SCREEN:
        await _show_booking_dates(context, push_current=False)
        return
    if current_screen in {state.BOOKING_SLOT_SELECTED_SCREEN, state.BOOKING_CONFIRMATION_SCREEN}:
        booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
        if isinstance(booking_date, str) and booking_date:
            slots = _slots(context)
            if slots is not None:
                await _show_slots(context, slots, push_current=False)
                return
            await _open_booking_slots(context, booking_date, push_current=False)
            return
        await _show_booking_dates(context, push_current=False)
        return
    if current_screen == state.BOOKING_SUCCESS_SCREEN:
        await show_home(context)
        return
    if current_screen == state.BOOKING_SERVICE_SELECTED_SCREEN:
        await _show_selected_category_services(context)
        return
    await show_home(context)


async def _open_booking_catalog(context: RouterContext, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        catalog = await booking_service.get_service_categories_and_services()
    except BookingServiceError as exc:
        if push_current:
            _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
        await context.send_text(exc.user_message, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _CATALOG_STATE_KEY, catalog)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_PRICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_DURATION_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _DATES_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOTS_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_DATE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, None)

    if not has_available_services(catalog):
        if push_current:
            _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
        await context.send_text(BOOKING_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    if catalog.categories:
        await _show_categories(context, catalog.categories, push_current=push_current)
        return
    await _show_services(context, catalog.services, category_title=None, push_current=push_current)


async def _open_booking_masters(context: RouterContext, yclients_service_id: str, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        masters = await booking_service.get_available_masters_for_service(yclients_service_id)
    except BookingServiceError as exc:
        logger.warning(
            "Booking masters screen failed: operation=show_booking_masters service_id=%s error_class=%s",
            yclients_service_id,
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_MASTERS_SCREEN)
        await context.send_text(exc.user_message, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY, masters)
    if not has_available_masters(masters):
        if push_current:
            _push_current_screen(context, state.BOOKING_MASTERS_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_MASTERS_SCREEN)
        await context.send_text(BOOKING_MASTERS_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    await _show_masters(context, masters, push_current=push_current)


async def _show_booking_dates(context: RouterContext, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    dates = booking_service.get_available_dates(days=14)
    state.set_state_data_value(_user_id(context), _chat_id(context), _DATES_STATE_KEY, dates)
    await _show_dates(context, dates, timezone_name=timezone_name, push_current=push_current)


async def _open_booking_slots(context: RouterContext, booking_date: str, *, push_current: bool = True) -> None:
    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    if not isinstance(service_id, str) or not service_id or not isinstance(master_id, str) or not master_id:
        await _show_booking_dates(context, push_current=False)
        return

    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slots = await booking_service.get_available_slots(
            yclients_service_id=service_id,
            yclients_master_id=master_id,
            booking_date=booking_date,
        )
    except BookingServiceError as exc:
        logger.warning(
            "Booking slots screen failed: operation=show_booking_slots service_id=%s master_id=%s date=%s error_class=%s",
            service_id,
            master_id,
            booking_date,
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_SLOTS_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SLOTS_SCREEN)
        await context.send_text(exc.user_message, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOTS_STATE_KEY, slots)
    await _show_slots(context, slots, push_current=push_current)



async def _show_booking_confirmation(context: RouterContext) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    _push_current_screen(context, state.BOOKING_CONFIRMATION_SCREEN)
    await context.send_text(
        format_booking_summary(_booking_state_snapshot(context), timezone_name=timezone_name),
        keyboard=booking_confirmation_keyboard(back_payload=BOOKING_BACK_PAYLOAD),
    )


async def _show_booking_success(context: RouterContext) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SUCCESS_SCREEN)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    await context.send_text(
        format_booking_success(_booking_state_snapshot(context), timezone_name=timezone_name),
        keyboard=booking_success_keyboard(),
    )


async def _send_immediate_confirmation_safely(context: RouterContext, *, created, user, booking_data: dict) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SUCCESS_SCREEN)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)

    booking_datetime = _parse_booking_datetime(created.datetime_iso or booking_data.get("selected_datetime"), timezone_name)
    try:
        history = await send_immediate_confirmation(
            context.sender,
            database_path=_database_path(),
            platform_user_id=user.platform_user_id,
            max_user_id=user.max_user_id or context.event.max_user_id,
            chat_id=user.chat_id or context.event.chat_id,
            yclients_record_id=created.yclients_record_id,
            yclients_client_id=created.yclients_client_id or user.yclients_client_id,
            booking_datetime=booking_datetime,
            service_name=str(booking_data.get("selected_service_name") or "услуга"),
            master_name=str(booking_data.get("selected_master_name") or "ваш мастер"),
            timezone_name=timezone_name,
            keyboard=booking_success_keyboard(),
        )
        if history is None or history.status != "sent":
            await _show_booking_success(context)
    except Exception as exc:  # noqa: BLE001 - booking is already created; keep success flow intact.
        logger.warning(
            "Booking immediate confirmation failed safely: platform_user_id=%s yclients_record_id=%s error_class=%s",
            user.platform_user_id,
            created.yclients_record_id,
            type(exc).__name__,
        )
        await _show_booking_success(context)


def _parse_booking_datetime(value: object | None, timezone_name: str) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                from zoneinfo import ZoneInfo

                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            return parsed
        except Exception:
            logger.warning("Booking datetime parse failed safely: value_present=%s", bool(raw))
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(timezone_name))


def _booking_state_snapshot(context: RouterContext) -> dict[str, object | None]:
    return {
        "selected_service_id": _state_value(context, _SELECTED_SERVICE_STATE_KEY),
        "selected_service_name": _state_value(context, _SELECTED_SERVICE_NAME_STATE_KEY),
        "selected_service_price": _state_value(context, _SELECTED_SERVICE_PRICE_STATE_KEY),
        "selected_service_duration": _state_value(context, _SELECTED_SERVICE_DURATION_STATE_KEY),
        "selected_master_id": _state_value(context, _SELECTED_MASTER_STATE_KEY),
        "selected_master_name": _state_value(context, _SELECTED_MASTER_NAME_STATE_KEY),
        "selected_master_specialization": _state_value(context, _SELECTED_MASTER_SPECIALIZATION_STATE_KEY),
        "selected_master_rating": _state_value(context, _SELECTED_MASTER_RATING_STATE_KEY),
        "selected_date": _state_value(context, _SELECTED_DATE_STATE_KEY),
        "selected_slot_time": _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY),
        "selected_datetime": _state_value(context, _SELECTED_SLOT_DATETIME_STATE_KEY),
    }


def _current_user(context: RouterContext):
    platform_user_id = _user_id(context)
    if not platform_user_id:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)


def _user_full_name(user) -> str:
    return (
        " ".join(part for part in (user.first_name, user.last_name) if part)
        or user.display_name
        or user.username
        or "Гость"
    ).strip()


def _save_attribution_safely(
    *,
    platform_user_id: str,
    yclients_record_id: str,
    yclients_client_id: str | None,
) -> None:
    try:
        PlatformAttributionRepository(_database_path()).create_if_missing(
            platform=PLATFORM_MAX,
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=yclients_client_id,
            marker=MAX_BOOKING_COMMENT_MARKER,
        )
    except Exception as exc:  # noqa: BLE001 - booking already exists in YClients, only local attribution failed.
        logger.exception(
            "Booking attribution save failed: operation=save_booking_attribution platform=%s platform_user_id=%s "
            "yclients_record_id=%s yclients_client_id_present=%s error_class=%s",
            PLATFORM_MAX,
            platform_user_id,
            yclients_record_id,
            bool(yclients_client_id),
            type(exc).__name__,
        )


def _optional_state_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _show_selected_category_services(context: RouterContext) -> None:
    catalog = _catalog(context)
    category_id = _state_value(context, _SELECTED_CATEGORY_STATE_KEY)
    if catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return
    if isinstance(category_id, str) and category_id:
        category = next((item for item in catalog.categories if item.yclients_category_id == category_id), None)
        services = [item for item in catalog.services if item.yclients_category_id == category_id]
    else:
        category = None
        services = catalog.services
    await _show_services(context, services, category_title=category.title if category else None, push_current=False)


async def _show_categories(context: RouterContext, categories: list, *, page: int = 0, push_current: bool = True) -> None:
    page = _clamp_page(page, len(categories))
    start = page * _MAX_CALLBACK_ITEMS
    display_categories = categories[start : start + _MAX_CALLBACK_ITEMS]
    category_payloads = {
        f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}{index}": category.yclients_category_id
        for index, category in enumerate(display_categories)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _CATEGORY_MAP_STATE_KEY, category_payloads)
    state.set_state_data_value(_user_id(context), _chat_id(context), _CATEGORY_PAGE_STATE_KEY, page)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SERVICE_MAP_STATE_KEY, {})
    if push_current:
        _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_CATEGORIES_SCREEN)
    if not display_categories:
        await context.send_text(BOOKING_CATEGORY_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await context.send_text(
        BOOKING_CATEGORY_TEXT,
        keyboard=booking_categories_keyboard(
            display_categories,
            page=page,
            has_previous=page > 0,
            has_next=(page + 1) * _MAX_CALLBACK_ITEMS < len(categories),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
    )


async def _show_services(
    context: RouterContext,
    services: list[BookingServiceItem],
    *,
    category_title: str | None,
    page: int = 0,
    push_current: bool = True,
) -> None:
    page = _clamp_page(page, len(services))
    start = page * _MAX_CALLBACK_ITEMS
    display_services = services[start : start + _MAX_CALLBACK_ITEMS]
    service_payloads = {
        f"{BOOKING_SERVICE_PAYLOAD_PREFIX}{index}": service.yclients_service_id
        for index, service in enumerate(display_services)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _SERVICE_MAP_STATE_KEY, service_payloads)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SERVICE_PAGE_STATE_KEY, page)
    if push_current:
        _push_current_screen(context, state.BOOKING_SERVICES_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SERVICES_SCREEN)
    if not display_services:
        await context.send_text(BOOKING_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    logger.info(
        "Booking services screen shown: service_count=%s category_title_present=%s",
        len(display_services),
        bool(category_title),
    )
    await context.send_text(
        BOOKING_SERVICE_TEXT,
        keyboard=booking_services_keyboard(
            display_services,
            format_service_title,
            page=page,
            has_previous=page > 0,
            has_next=(page + 1) * _MAX_CALLBACK_ITEMS < len(services),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
    )


async def _show_masters(
    context: RouterContext,
    masters: list[BookingMasterItem],
    *,
    page: int = 0,
    push_current: bool = True,
) -> None:
    page = _clamp_page(page, len(masters))
    start = page * _MAX_CALLBACK_ITEMS
    display_masters = masters[start : start + _MAX_CALLBACK_ITEMS]
    master_payloads = {
        f"{BOOKING_MASTER_PAYLOAD_PREFIX}{index}": master.yclients_master_id
        for index, master in enumerate(display_masters)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTER_MAP_STATE_KEY, master_payloads)
    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTER_PAGE_STATE_KEY, page)
    if push_current:
        _push_current_screen(context, state.BOOKING_MASTERS_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_MASTERS_SCREEN)
    if not display_masters:
        await context.send_text(BOOKING_MASTERS_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    logger.info(
        "Booking masters screen shown: service_id=%s masters_count=%s",
        _state_value(context, _SELECTED_SERVICE_STATE_KEY),
        len(display_masters),
    )
    await context.send_text(
        BOOKING_MASTER_TEXT,
        keyboard=booking_masters_keyboard(
            display_masters,
            format_master_title,
            page=page,
            has_previous=page > 0,
            has_next=(page + 1) * _MAX_CALLBACK_ITEMS < len(masters),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
    )


async def _show_dates(
    context: RouterContext,
    dates: list,
    *,
    timezone_name: str,
    push_current: bool = True,
) -> None:
    date_payloads = {
        f"{BOOKING_DATE_PAYLOAD_PREFIX}{index}": item.isoformat()
        for index, item in enumerate(dates[:_MAX_CALLBACK_ITEMS])
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _DATE_MAP_STATE_KEY, date_payloads)
    if push_current:
        _push_current_screen(context, state.BOOKING_DATES_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
    master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    attachment = _master_photo_service().photo_attachment(str(master_id) if master_id else None)
    await context.send_text(
        _booking_step_text(context, tail="📅 Выберите дату:", include_selected_date=False),
        keyboard=booking_dates_keyboard(
            dates[:_MAX_CALLBACK_ITEMS],
            lambda value: format_date_button(value, timezone_name=timezone_name),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
        attachments=[attachment] if attachment is not None else None,
    )


async def _show_slots(context: RouterContext, slots: list[BookingSlotItem], *, push_current: bool = True) -> None:
    display_slots = slots[:_MAX_CALLBACK_ITEMS]
    slot_payloads = {
        f"{BOOKING_SLOT_PAYLOAD_PREFIX}{index}": item.time
        for index, item in enumerate(display_slots)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOT_MAP_STATE_KEY, slot_payloads)
    if push_current:
        _push_current_screen(context, state.BOOKING_SLOTS_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SLOTS_SCREEN)
    if not display_slots:
        await context.send_text(BOOKING_SLOTS_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await context.send_text(
        BOOKING_SLOTS_TEXT,
        keyboard=booking_slots_keyboard(display_slots, format_slot_button, back_payload=BOOKING_BACK_PAYLOAD),
    )


def _clamp_page(page: int, item_count: int) -> int:
    max_page = max((item_count - 1) // _MAX_CALLBACK_ITEMS, 0)
    return max(0, min(page, max_page))


def _int_state_value(context: RouterContext, key: str) -> int:
    value = _state_value(context, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _push_current_screen(context: RouterContext, next_screen: str) -> None:
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    if current_screen != next_screen:
        state.push_screen(_user_id(context), _chat_id(context), current_screen)
    state.set_current_screen(_user_id(context), _chat_id(context), next_screen)


def _service_price_text(service: BookingServiceItem) -> str | None:
    price = service.price_min if service.price_min not in (None, "") else service.price_max
    return f"{price} ₽" if price not in (None, "") else None


def _catalog(context: RouterContext) -> BookingCatalog | None:
    value = _state_value(context, _CATALOG_STATE_KEY)
    return value if isinstance(value, BookingCatalog) else None


def _masters(context: RouterContext) -> list[BookingMasterItem] | None:
    value = _state_value(context, _MASTERS_STATE_KEY)
    if isinstance(value, list) and all(isinstance(item, BookingMasterItem) for item in value):
        return value
    return None




def _booking_step_text(context: RouterContext, *, tail: str, include_selected_date: bool = True) -> str:
    service_name = str(_state_value(context, _SELECTED_SERVICE_NAME_STATE_KEY) or "—").strip() or "—"
    master_name = str(_state_value(context, _SELECTED_MASTER_NAME_STATE_KEY) or "Любой мастер").strip() or "Любой мастер"
    lines = [
        f"✂️ Услуга: {service_name}",
        f"💈 Мастер: {master_name}",
    ]
    if include_selected_date:
        selected_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
        lines.append(f"📅 Дата: {_format_selected_date(str(selected_date)) if selected_date else '—'}")
    lines.append(tail)
    return "\n".join(lines)


def _master_photo_service() -> MasterPhotosService:
    return MasterPhotosService(MasterPhotosRepository(_database_path()), YClientsSettingsRepository(_database_path()))


def _slots(context: RouterContext) -> list[BookingSlotItem] | None:
    value = _state_value(context, _SLOTS_STATE_KEY)
    if isinstance(value, list) and all(isinstance(item, BookingSlotItem) for item in value):
        return value
    return None


def _format_selected_date(value: str) -> str:
    timezone_name = BookingService(YClientsSettingsRepository(_database_path())).get_branch_timezone()
    try:
        return format_date_button(value, timezone_name=timezone_name).replace("📅 ", "")
    except ValueError:
        return value


def _mapped_value(context: RouterContext, key: str, payload: str | None) -> str | None:
    if payload is None:
        return None
    mapping = _state_value(context, key)
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(payload)
    return value if isinstance(value, str) else None


def _state_value(context: RouterContext, key: str) -> object | None:
    return state.get_state_data_value(_user_id(context), _chat_id(context), key)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
