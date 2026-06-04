"""Booking step 1 flow for choosing YClients service categories and services."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.booking import (
    BookingCatalog,
    BookingService,
    BookingServiceError,
    BookingServiceItem,
    format_service_title,
    has_available_services,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_CATEGORY_NEXT_PAYLOAD,
    BOOKING_CATEGORY_PAYLOAD_PREFIX,
    BOOKING_CATEGORY_PREV_PAYLOAD,
    BOOKING_SERVICE_NEXT_PAYLOAD,
    BOOKING_SERVICE_PAYLOAD_PREFIX,
    BOOKING_SERVICE_PREV_PAYLOAD,
    MENU_BOOKING_PAYLOAD,
    booking_categories_keyboard,
    booking_services_keyboard,
    navigation_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BOOKING_CATEGORY_EMPTY_TEXT,
    BOOKING_CATEGORY_TEXT,
    BOOKING_EMPTY_TEXT,
    BOOKING_SERVICE_SELECTED_TEXT,
    BOOKING_SERVICE_TEXT,
)

logger = logging.getLogger(__name__)

_MAX_CALLBACK_ITEMS = 20
_CATALOG_STATE_KEY = "booking_catalog"
_CATEGORY_MAP_STATE_KEY = "booking_category_payloads"
_SERVICE_MAP_STATE_KEY = "booking_service_payloads"
_CATEGORY_PAGE_STATE_KEY = "booking_category_page"
_SERVICE_PAGE_STATE_KEY = "booking_service_page"
_SELECTED_CATEGORY_STATE_KEY = "selected_yclients_category_id"
_SELECTED_SERVICE_STATE_KEY = "selected_yclients_service_id"
_SELECTED_SERVICE_NAME_STATE_KEY = "selected_service_name"


def register_booking_routes(router: Router) -> None:
    """Register booking category/service callbacks."""

    router.on_callback(MENU_BOOKING_PAYLOAD, handle_booking_start)
    router.on_callback(BOOKING_BACK_PAYLOAD, handle_booking_back)
    router.on_callback(BOOKING_CATEGORY_PREV_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_CATEGORY_NEXT_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_SERVICE_PREV_PAYLOAD, handle_booking_service_page)
    router.on_callback(BOOKING_SERVICE_NEXT_PAYLOAD, handle_booking_service_page)
    for index in range(_MAX_CALLBACK_ITEMS):
        router.on_callback(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}{index}", handle_booking_category)
        router.on_callback(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}{index}", handle_booking_service)


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
    await _show_services(context, services, category_title=category.title if category else None)


async def handle_booking_service(context: RouterContext) -> None:
    """Save selected service and show placeholder for the next booking step."""

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
    _push_current_screen(context, state.BOOKING_SERVICE_SELECTED_SCREEN)
    await context.send_text(
        BOOKING_SERVICE_SELECTED_TEXT.format(service_name=service.title),
        keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD),
    )


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
    if current_screen == state.BOOKING_SERVICE_SELECTED_SCREEN:
        catalog = _catalog(context)
        category_id = _state_value(context, _SELECTED_CATEGORY_STATE_KEY)
        if catalog is not None:
            if isinstance(category_id, str) and category_id:
                category = next((item for item in catalog.categories if item.yclients_category_id == category_id), None)
                services = [item for item in catalog.services if item.yclients_category_id == category_id]
            else:
                category = None
                services = catalog.services
            await _show_services(context, services, category_title=category.title if category else None, push_current=False)
            return
        await _open_booking_catalog(context, push_current=False)
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
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, None)

    if not has_available_services(catalog):
        if push_current:
            _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
        await context.send_text(BOOKING_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    if catalog.categories:
        await _show_categories(context, catalog.categories, push_current=push_current)
        return
    await _show_services(context, catalog.services, category_title=None, push_current=push_current)


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


def _catalog(context: RouterContext) -> BookingCatalog | None:
    value = _state_value(context, _CATALOG_STATE_KEY)
    return value if isinstance(value, BookingCatalog) else None


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
