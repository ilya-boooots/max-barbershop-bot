"""Booking flow for choosing YClients services and masters."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import BOOKING_CREATE_LOCK_TTL_SECONDS, acquire_action_lock, release_action_lock
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.integrations.yclients.utils import MAX_BOOKING_COMMENT_MARKER, MAX_REPEAT_BOOKING_COMMENT_MARKER
from max_barbershop_bot.repositories.birthday_funnel_events import BirthdayFunnelEventsRepository
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.repeat_visit_events import RepeatVisitEventsRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE, build_yclients_action_comment, localize_datetime, normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.booking import (
    BookingCatalog,
    BookingService,
    BookingServiceError,
    BookingMasterItem,
    BookingServiceItem,
    BookingSlotItem,
    BOOKING_DATES_EMPTY_TEXT,
    DATE_LOOKAHEAD_DAYS,
    format_booking_success,
    format_booking_summary,
    format_date_button,
    format_master_title,
    format_service_title,
    format_slot_button,
    has_available_masters,
    has_available_services,
    is_service_compatible_with_master,
)
from max_barbershop_bot.services.birthday_funnel import (
    BIRTHDAY_BUTTON_BOOK,
    apply_birthday_warning,
)
from max_barbershop_bot.services.cancellation_recovery import CANCELLATION_RECOVERY_BOOKING_PAYLOAD
from max_barbershop_bot.services.contacts import ContactsService
from max_barbershop_bot.services.master_photos import MasterPhotosService
from max_barbershop_bot.services.navigation import show_booking_stale_callback, show_home
from max_barbershop_bot.services.registration import contains_contact_attachment, extract_contact_phone, normalize_phone
from max_barbershop_bot.services.notifications import (
    BOOKING_CONFIRMATION_IMMEDIATE,
    get_notification_history,
    mark_notification_history_skipped,
)
from max_barbershop_bot.services.reminders import send_immediate_confirmation
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_HUB_DATETIME_PAYLOAD,
    BOOKING_HUB_SERVICE_PAYLOAD,
    BOOKING_HUB_STAFF_PAYLOAD,
    BOOKING_CATEGORY_NEXT_PAYLOAD,
    BOOKING_CATEGORY_PAYLOAD_PREFIX,
    BOOKING_CATEGORY_PREV_PAYLOAD,
    BOOKING_CONFIRM_PAYLOAD,
    BOOKING_CANCEL_DRAFT_PAYLOAD,
    BOOKING_DATE_NEXT_PAYLOAD,
    BOOKING_PHONE_USE_REGISTERED_PAYLOAD,
    BOOKING_DATE_PREV_PAYLOAD,
    BOOKING_MASTER_NEXT_PAYLOAD,
    BOOKING_MASTER_ANY_PAYLOAD,
    BOOKING_DATE_PAYLOAD_PREFIX,
    BOOKING_MASTER_PAYLOAD_PREFIX,
    BOOKING_MASTER_PREV_PAYLOAD,
    BOOKING_SERVICE_NEXT_PAYLOAD,
    BOOKING_SERVICE_PAYLOAD_PREFIX,
    BOOKING_SERVICE_PREV_PAYLOAD,
    BOOKING_SLOT_NEXT_PAYLOAD,
    BOOKING_SLOT_PAYLOAD_PREFIX,
    BOOKING_SLOT_PREV_PAYLOAD,
    MENU_BOOKING_PAYLOAD,
    NAV_HOME_PAYLOAD,
    REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX,
    booking_categories_keyboard,
    booking_hub_keyboard,
    booking_dates_keyboard,
    booking_masters_keyboard,
    booking_services_keyboard,
    booking_slots_keyboard,
    booking_confirmation_keyboard,
    booking_phone_keyboard,
    booking_success_keyboard,
    my_booking_details_keyboard,
    my_bookings_history_keyboard,
    navigation_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BOOKING_CATEGORY_TEXT,
    BOOKING_DATETIME_FIRST_CATEGORY_TEXT,
    BOOKING_DATETIME_FIRST_SERVICE_TEXT,
    BOOKING_CONFIRMATION_MISSING_DATA_TEXT,
    BOOKING_CREATE_ERROR_TEXT,
    BOOKING_EMPTY_TEXT,
    BOOKING_MASTER_TEXT,
    BOOKING_CONTACT_PHONE_MISSING_TEXT,
    BOOKING_MASTERS_EMPTY_TEXT,
    BOOKING_PHONE_INVALID_TEXT,
    BOOKING_PHONE_TEXT,
    BOOKING_PHONE_WITHOUT_REGISTERED_TEXT,
    BOOKING_REGISTERED_PHONE_MISSING_TEXT,
    BOOKING_SERVICE_TEXT,
    BOOKING_SLOTS_EMPTY_TEXT,
    BOOKING_STALE_DATE_TEXT,
    BOOKING_STALE_SLOT_TEXT,
    BOOKING_STAFF_FIRST_CATEGORY_TEXT,
    CANCELLATION_RECOVERY_LATER_TEXT,
    CANCELLATION_RECOVERY_STALE_TEXT,
)

logger = logging.getLogger(__name__)

BOOKING_PAGE_SIZE = 8
DATE_PAGE_SIZE = 10
TIME_PAGE_SIZE = 15
_CALLBACK_PAYLOAD_SLOTS = 20

_CATALOG_STATE_KEY = "booking_catalog"
_CATEGORY_MAP_STATE_KEY = "booking_category_payloads"
_SERVICE_MAP_STATE_KEY = "booking_service_payloads"
_MASTER_MAP_STATE_KEY = "booking_master_payloads"
_DATE_MAP_STATE_KEY = "booking_date_payloads"
_SLOT_MAP_STATE_KEY = "booking_slot_payloads"
_MASTERS_STATE_KEY = "booking_masters"
_DATES_STATE_KEY = "booking_dates"
_SLOTS_STATE_KEY = "booking_slots"
_ELIGIBLE_SERVICE_IDS_STATE_KEY = "booking_eligible_service_ids"
_ELIGIBLE_MASTER_IDS_STATE_KEY = "booking_eligible_master_ids"
_CATEGORY_PAGE_STATE_KEY = "booking_category_page"
_SERVICE_PAGE_STATE_KEY = "booking_service_page"
_MASTER_PAGE_STATE_KEY = "booking_master_page"
_DATE_PAGE_STATE_KEY = "booking_date_page"
_SLOT_PAGE_STATE_KEY = "booking_slot_page"
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
_BOOKING_PHONE_STATE_KEY = "booking_phone"
_BOOKING_PHONE_SOURCE_STATE_KEY = "booking_phone_source"
_REGISTERED_PHONE_STATE_KEY = "registered_phone"
_ENTRY_MODE_STATE_KEY = "booking_entry_mode"
_ENTRY_MODE_SERVICE_FIRST = "service_first"
_ENTRY_MODE_STAFF_FIRST = "staff_first"
_ENTRY_MODE_DATETIME_FIRST = "datetime_first"
_ENTRY_MODE_REPEAT = "repeat_booking"
_REPEAT_SOURCE_SCREEN_STATE_KEY = "repeat_source_screen"
_BOOKING_STATE_KEYS = (
    _CATALOG_STATE_KEY,
    _CATEGORY_MAP_STATE_KEY,
    _SERVICE_MAP_STATE_KEY,
    _MASTER_MAP_STATE_KEY,
    _DATE_MAP_STATE_KEY,
    _SLOT_MAP_STATE_KEY,
    _MASTERS_STATE_KEY,
    _DATES_STATE_KEY,
    _SLOTS_STATE_KEY,
    _ELIGIBLE_SERVICE_IDS_STATE_KEY,
    _ELIGIBLE_MASTER_IDS_STATE_KEY,
    _CATEGORY_PAGE_STATE_KEY,
    _SERVICE_PAGE_STATE_KEY,
    _MASTER_PAGE_STATE_KEY,
    _DATE_PAGE_STATE_KEY,
    _SLOT_PAGE_STATE_KEY,
    _SELECTED_CATEGORY_STATE_KEY,
    _SELECTED_CATEGORY_NAME_STATE_KEY,
    _SELECTED_SERVICE_STATE_KEY,
    _SELECTED_SERVICE_NAME_STATE_KEY,
    _SELECTED_SERVICE_PRICE_STATE_KEY,
    _SELECTED_SERVICE_DURATION_STATE_KEY,
    _SELECTED_MASTER_STATE_KEY,
    _SELECTED_MASTER_NAME_STATE_KEY,
    _SELECTED_MASTER_SPECIALIZATION_STATE_KEY,
    _SELECTED_MASTER_RATING_STATE_KEY,
    _SELECTED_DATE_STATE_KEY,
    _SELECTED_SLOT_TIME_STATE_KEY,
    _SELECTED_SLOT_DATETIME_STATE_KEY,
    _SELECTED_SLOT_RAW_STATE_KEY,
    _BOOKING_DATE_STATE_KEY,
    _BOOKING_SLOT_STATE_KEY,
    _BOOKING_CREATION_IN_PROGRESS_STATE_KEY,
    _BOOKING_COMPLETED_RECORD_ID_STATE_KEY,
    _BOOKING_PHONE_STATE_KEY,
    _BOOKING_PHONE_SOURCE_STATE_KEY,
    _ENTRY_MODE_STATE_KEY,
    _REPEAT_SOURCE_SCREEN_STATE_KEY,
    "booking_source",
    "booking_origin",
    "booking_origin_type",
    "birthday_event_id",
    "birthday_discount_context",
    "birthday_is_test",
    "birthday_source",
    "birthday_claimed_at_utc",
    "repeat_visit_event_id",
    "cancellation_recovery_event_id",
    "notification_event_id",
    "notification_is_test",
    "notification_source",
    "yclients_client_id",
)


def apply_lost_client_discount_comment(base_comment: str, *, booking_origin_type: str | None, lost_days: int | None) -> str:
    """Append Telegram-equivalent lost-client discount marker once."""

    if booking_origin_type != "lost_client" or not isinstance(lost_days, int) or lost_days not in {30, 60, 90}:
        return base_comment
    warning = f"Клиент не посещал {lost_days} дней. НУЖНО СДЕЛАТЬ СКИДКУ"
    if warning in base_comment:
        return base_comment
    return f"{base_comment}\n{warning}" if base_comment else warning
def _confirm_lock_key(context: RouterContext) -> str:
    return f"booking_create|{_user_id(context) or 'unknown'}"


def register_booking_routes(router: Router) -> None:
    """Register booking category/service callbacks."""

    router.on_callback(MENU_BOOKING_PAYLOAD, handle_booking_start)
    router.on_callback_prefix(REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX, handle_repeat_visit_booking_start)
    router.on_callback(CANCELLATION_RECOVERY_BOOKING_PAYLOAD, handle_booking_start)
    router.on_callback_prefix(f"{BIRTHDAY_BUTTON_BOOK}:", handle_birthday_booking_start)
    router.on_callback(BOOKING_BACK_PAYLOAD, handle_booking_back)
    router.on_callback(NAV_HOME_PAYLOAD, handle_booking_home)
    router.on_callback(BOOKING_CONFIRM_PAYLOAD, handle_booking_confirm)
    router.on_callback(BOOKING_CANCEL_DRAFT_PAYLOAD, handle_booking_cancel_draft)
    router.on_callback(BOOKING_PHONE_USE_REGISTERED_PAYLOAD, handle_booking_phone_use_registered)
    router.on_callback(BOOKING_HUB_SERVICE_PAYLOAD, handle_booking_hub_service)
    router.on_callback(BOOKING_HUB_STAFF_PAYLOAD, handle_booking_hub_staff)
    router.on_callback(BOOKING_HUB_DATETIME_PAYLOAD, handle_booking_hub_datetime)
    router.on_screen_text(state.BOOKING_PHONE_SCREEN, handle_booking_phone_input)
    router.on_callback(BOOKING_CATEGORY_PREV_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_CATEGORY_NEXT_PAYLOAD, handle_booking_category_page)
    router.on_callback(BOOKING_SERVICE_PREV_PAYLOAD, handle_booking_service_page)
    router.on_callback(BOOKING_SERVICE_NEXT_PAYLOAD, handle_booking_service_page)
    router.on_callback(BOOKING_MASTER_PREV_PAYLOAD, handle_booking_master_page)
    router.on_callback(BOOKING_MASTER_NEXT_PAYLOAD, handle_booking_master_page)
    router.on_callback(BOOKING_MASTER_ANY_PAYLOAD, handle_booking_master_any)
    router.on_callback(BOOKING_DATE_PREV_PAYLOAD, handle_booking_date_page)
    router.on_callback(BOOKING_DATE_NEXT_PAYLOAD, handle_booking_date_page)
    router.on_callback(BOOKING_SLOT_PREV_PAYLOAD, handle_booking_slot_page)
    router.on_callback(BOOKING_SLOT_NEXT_PAYLOAD, handle_booking_slot_page)
    for index in range(_CALLBACK_PAYLOAD_SLOTS):
        router.on_callback(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}{index}", handle_booking_category)
        router.on_callback(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}{index}", handle_booking_service)
        router.on_callback(f"{BOOKING_MASTER_PAYLOAD_PREFIX}{index}", handle_booking_master)
        router.on_callback(f"{BOOKING_DATE_PAYLOAD_PREFIX}{index}", handle_booking_date)
        router.on_callback(f"{BOOKING_SLOT_PAYLOAD_PREFIX}{index}", handle_booking_slot)



async def start_repeat_booking_with_prefill(
    context: RouterContext,
    *,
    service_id: str,
    service_name: str,
    master_id: str | None,
    master_name: str | None,
    service_price: str | None = None,
    service_duration: str | None = None,
    source_screen: str,
) -> None:
    """Start repeat booking with Telegram-compatible prefill/fallback."""

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    _clear_booking_state(context)
    state.set_state_data_value(platform_user_id, chat_id, _ENTRY_MODE_STATE_KEY, _ENTRY_MODE_REPEAT)
    state.set_state_data_value(platform_user_id, chat_id, _REPEAT_SOURCE_SCREEN_STATE_KEY, source_screen or state.MY_BOOKING_DETAILS_SCREEN)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_DATE_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SLOT_TIME_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SLOT_DATETIME_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SLOT_RAW_STATE_KEY, None)
    try:
        catalog = await booking_service.get_valid_categories_for_entry_mode(entry_mode=_ENTRY_MODE_SERVICE_FIRST)
    except BookingServiceError as exc:
        logger.warning(
            "MAX booking repeat diagnostic: platform_user_id_present=%s source_record_id_present=%s "
            "selected_service_id_present=%s selected_master_id_present=%s service_available=%s "
            "master_available=%s slots_count=%s create_success=%s yclients_record_id_present=%s error_class=%s http_status=%s trace_id=%s",
            bool(platform_user_id), True, bool(service_id), bool(master_id), False, False, 0, False, False,
            type(exc).__name__, getattr(exc, "status_code", None), getattr(exc, "trace_id", None),
        )
        await context.send_text("Эта услуга сейчас недоступна 🙏\n\nВыберите другую услугу для записи.", keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    state.set_state_data_value(platform_user_id, chat_id, _CATALOG_STATE_KEY, catalog)
    service = next((item for item in catalog.services if item.yclients_service_id == str(service_id)), None)
    if service is None:
        logger.info(
            "MAX booking repeat diagnostic: platform_user_id_present=%s source_record_id_present=%s selected_service_id_present=%s selected_master_id_present=%s service_available=%s master_available=%s slots_count=%s create_success=%s yclients_record_id_present=%s error_class=%s",
            bool(platform_user_id), True, bool(service_id), bool(master_id), False, False, 0, False, False, "none",
        )
        await _show_categories(context, catalog.categories, push_current=False)
        return

    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_CATEGORY_STATE_KEY, service.yclients_category_id)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_CATEGORY_NAME_STATE_KEY, service.category_title)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SERVICE_STATE_KEY, service.yclients_service_id)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SERVICE_NAME_STATE_KEY, service.title or service_name)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SERVICE_PRICE_STATE_KEY, service_price or _service_price_text(service))
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_SERVICE_DURATION_STATE_KEY, service_duration or service.duration)
    try:
        masters = await booking_service.get_available_masters_for_service(service.yclients_service_id, service=service)
    except BookingServiceError as exc:
        logger.warning(
            "MAX booking repeat diagnostic: platform_user_id_present=%s source_record_id_present=%s selected_service_id_present=%s selected_master_id_present=%s service_available=%s master_available=%s slots_count=%s create_success=%s yclients_record_id_present=%s error_class=%s http_status=%s trace_id=%s",
            bool(platform_user_id), True, bool(service_id), bool(master_id), True, False, 0, False, False,
            type(exc).__name__, getattr(exc, "status_code", None), getattr(exc, "trace_id", None),
        )
        await _show_selected_category_services(context)
        return
    state.set_state_data_value(platform_user_id, chat_id, _MASTERS_STATE_KEY, masters)

    master = next((item for item in masters if master_id and item.yclients_master_id == str(master_id)), None)
    if master is not None:
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_STATE_KEY, master.yclients_master_id)
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_NAME_STATE_KEY, master.title or master_name or "Любой мастер")
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, master.specialization)
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_RATING_STATE_KEY, master.rating)
        await _show_booking_dates(context)
        return

    if not master_id:
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_STATE_KEY, "0")
        state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_NAME_STATE_KEY, master_name or "Любой мастер")
        await _show_booking_dates(context)
        return

    logger.info(
        "MAX booking repeat diagnostic: platform_user_id_present=%s source_record_id_present=%s selected_service_id_present=%s selected_master_id_present=%s service_available=%s master_available=%s slots_count=%s create_success=%s yclients_record_id_present=%s error_class=%s",
        bool(platform_user_id), True, bool(service_id), bool(master_id), True, False, 0, False, False, "none",
    )
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(platform_user_id, chat_id, _SELECTED_MASTER_NAME_STATE_KEY, None)
    await _show_masters(context, masters, push_current=False)


async def start_staff_first_booking_with_master(context: RouterContext, master: BookingMasterItem) -> None:
    """Start staff-first booking from an already selected master."""

    _clear_booking_state(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), _ENTRY_MODE_STATE_KEY, _ENTRY_MODE_STAFF_FIRST)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, master.yclients_master_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, master.title)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, master.specialization)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, master.rating)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, None)
    await _open_booking_catalog(context)


async def handle_birthday_booking_start(context: RouterContext) -> None:
    """Open booking from birthday-funnel CTA with attribution."""

    await context.answer_callback()
    event_id = _birthday_event_id_from_payload(context.event.callback_payload)
    events_repo = BirthdayFunnelEventsRepository(_database_path())
    event = events_repo.get_event(event_id) if event_id is not None else None
    if event is None or event.platform_user_id != str(_user_id(context) or ""):
        await context.send_text("⚠️ Это предложение уже устарело. Вы можете записаться через главное меню.")
        return
    events_repo.mark_status(event.id, "clicked_booking", clicked=True)
    _clear_booking_state(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), "booking_source", "birthday_funnel")
    state.set_state_data_value(_user_id(context), _chat_id(context), "birthday_event_id", event.id)
    state.set_state_data_value(_user_id(context), _chat_id(context), "birthday_discount_context", True)
    state.set_state_data_value(_user_id(context), _chat_id(context), "birthday_is_test", event.is_test)
    state.set_state_data_value(_user_id(context), _chat_id(context), "birthday_source", event.source)
    state.set_state_data_value(_user_id(context), _chat_id(context), "birthday_claimed_at_utc", event.clicked_at_utc or datetime.utcnow().isoformat())
    state.set_state_data_value(_user_id(context), _chat_id(context), "notification_is_test", event.is_test)
    state.set_state_data_value(_user_id(context), _chat_id(context), "notification_source", event.source)
    await _show_booking_hub(context)


async def handle_booking_start(context: RouterContext) -> None:
    """Open the first real booking step from the main menu."""

    await context.answer_callback()
    _clear_booking_state(context)
    await _show_booking_hub(context)


async def handle_repeat_visit_booking_start(context: RouterContext) -> None:
    """Open normal booking flow from repeat visit CTA and preserve attribution."""

    payload = context.event.callback_payload or ""
    raw_event_id = payload.removeprefix(REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX).strip()
    try:
        event_id = int(raw_event_id)
    except ValueError:
        await show_booking_stale_callback(context)
        return
    repo = RepeatVisitEventsRepository(_database_path())
    event = repo.get_event(event_id)
    if event is None:
        await show_booking_stale_callback(context)
        return
    repo.mark_status(event.id, "clicked_booking", clicked=True)
    await context.answer_callback()
    _clear_booking_state(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), "booking_source", "repeat_visit")
    state.set_state_data_value(_user_id(context), _chat_id(context), "booking_origin_type", "repeat_visit")
    state.set_state_data_value(_user_id(context), _chat_id(context), "repeat_visit_event_id", event.id)
    state.set_state_data_value(_user_id(context), _chat_id(context), "notification_event_id", event.id)
    state.set_state_data_value(_user_id(context), _chat_id(context), "yclients_client_id", event.yclients_client_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), "notification_is_test", event.is_test)
    state.set_state_data_value(_user_id(context), _chat_id(context), "notification_source", event.source)
    await _show_booking_hub(context)


async def handle_booking_hub_service(context: RouterContext) -> None:
    """Start the service-first booking route from the hub."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    state.set_state_data_value(_user_id(context), _chat_id(context), _ENTRY_MODE_STATE_KEY, _ENTRY_MODE_SERVICE_FIRST)
    await _open_booking_catalog(context)


async def handle_booking_hub_staff(context: RouterContext) -> None:
    """Start the staff-first booking route from the hub."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    state.set_state_data_value(_user_id(context), _chat_id(context), _ENTRY_MODE_STATE_KEY, _ENTRY_MODE_STAFF_FIRST)
    await _open_staff_first_masters(context)


async def handle_booking_hub_datetime(context: RouterContext) -> None:
    """Start the Telegram-reference date/time-first route from the hub."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    state.set_state_data_value(_user_id(context), _chat_id(context), _ENTRY_MODE_STATE_KEY, _ENTRY_MODE_DATETIME_FIRST)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
    await _open_datetime_first_dates(context)


async def handle_booking_category(context: RouterContext) -> None:
    """Show services from the selected category."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    category_id = _mapped_value(context, _CATEGORY_MAP_STATE_KEY, context.event.callback_payload)
    catalog = _catalog(context)
    if not category_id or catalog is None:
        await context.send_text("😔 Список категорий уже обновился. Показываю актуальные категории 🙂")
        await _refresh_categories_after_stale(context)
        return

    category = next((item for item in catalog.categories if item.yclients_category_id == category_id), None)
    services = [item for item in catalog.services if item.yclients_category_id == category_id]
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_STATE_KEY, category_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_NAME_STATE_KEY, category.title if category else None)
    await _show_services(context, services, category_title=category.title if category else None)


async def handle_booking_service(context: RouterContext) -> None:
    """Save selected service and show the master selection step."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    service_id = _mapped_value(context, _SERVICE_MAP_STATE_KEY, context.event.callback_payload)
    catalog = _catalog(context)
    if not service_id or catalog is None:
        await context.send_text("😔 Список услуг уже обновился. Показываю актуальные услуги 🙂")
        await _refresh_services_after_stale(context)
        return

    service = next((item for item in catalog.services if item.yclients_service_id == service_id), None)
    if service is None:
        await context.send_text("😔 Эта услуга уже недоступна. Показываю актуальные услуги 🙂")
        await _refresh_services_after_stale(context)
        return
    eligible_service_ids = _state_value(context, _ELIGIBLE_SERVICE_IDS_STATE_KEY)
    if (
        _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST
        and isinstance(eligible_service_ids, list)
        and service.yclients_service_id not in eligible_service_ids
    ):
        await context.send_text(
            "Это время уже недоступно 🙏\n\nПожалуйста, выберите другое время.",
            keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD),
        )
        await _open_datetime_first_catalog(context, push_current=False)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, service.yclients_service_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, service.title)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_PRICE_STATE_KEY, _service_price_text(service))
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_DURATION_STATE_KEY, service.duration)

    selected_master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST and isinstance(selected_master_id, str) and selected_master_id:
        if not is_service_compatible_with_master(service, selected_master_id):
            await context.send_text("😔 Эта услуга недоступна у выбранного мастера. Пожалуйста, выберите другую услугу.")
            state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
            state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, None)
            state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_PRICE_STATE_KEY, None)
            state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_DURATION_STATE_KEY, None)
            await _show_selected_category_services(context)
            return
        await _show_booking_dates(context)
        return
    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and _state_value(context, _SELECTED_DATE_STATE_KEY) and _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY):
        await _open_datetime_first_masters(context, service.yclients_service_id)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, None)
    await _open_booking_masters(context, service.yclients_service_id)


async def handle_booking_category_page(context: RouterContext) -> None:
    """Move between category pages using short registered payloads."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    catalog = _catalog(context)
    if catalog is None:
        await _open_booking_catalog(context, push_current=False)
        return
    current_page = _int_state_value(context, _CATEGORY_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_CATEGORY_PREV_PAYLOAD else 1
    await _show_categories(context, catalog.categories, page=max(0, current_page + delta), push_current=False)


async def handle_booking_master_page(context: RouterContext) -> None:
    """Move between master pages using short registered payloads."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    masters = _masters(context)
    if masters is None:
        service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
        if isinstance(service_id, str) and service_id:
            await _open_booking_masters(context, service_id, push_current=False)
            return
        if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST:
            await _open_staff_first_masters(context, push_current=False)
            return
        await _open_booking_catalog(context, push_current=False)
        return
    current_page = _int_state_value(context, _MASTER_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_MASTER_PREV_PAYLOAD else 1
    await _show_masters(context, masters, page=max(0, current_page + delta), push_current=False)


async def handle_booking_date_page(context: RouterContext) -> None:
    """Move between booking date pages using Telegram-reference prev/next behavior."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    dates = _dates(context)
    if dates is None:
        await context.send_text("😔 Список дат уже обновился. Показываю актуальные даты 🙂")
        if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
            await _open_datetime_first_dates(context, push_current=False)
            return
        await _show_booking_dates(context, push_current=False)
        return
    current_page = _int_state_value(context, _DATE_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_DATE_PREV_PAYLOAD else 1
    requested_page = current_page + delta
    page = _clamp_page(requested_page, len(dates), page_size=DATE_PAGE_SIZE)
    if page != requested_page:
        await context.send_text("😔 Список дат уже обновился. Показываю актуальные даты 🙂")
    await _show_dates(context, dates, timezone_name=BookingService(YClientsSettingsRepository(_database_path())).get_branch_timezone(), page=page, push_current=False)


async def handle_booking_slot_page(context: RouterContext) -> None:
    """Move between booking time slot pages using Telegram-reference prev/next behavior."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    slots = _slots(context)
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    if slots is None:
        await context.send_text("😔 Список времени уже обновился. Показываю актуальные окна 🙂")
        if isinstance(booking_date, str) and booking_date:
            if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and (
                not _state_value(context, _SELECTED_SERVICE_STATE_KEY) or not _state_value(context, _SELECTED_MASTER_STATE_KEY)
            ):
                await _open_datetime_first_slots(context, booking_date, push_current=False, stale_if_empty=True)
                return
            await _open_booking_slots(context, booking_date, push_current=False, stale_if_empty=True)
            return
        await _show_booking_dates(context, push_current=False)
        return
    current_page = _int_state_value(context, _SLOT_PAGE_STATE_KEY)
    delta = -1 if context.event.callback_payload == BOOKING_SLOT_PREV_PAYLOAD else 1
    requested_page = current_page + delta
    page = _clamp_page(requested_page, len(slots), page_size=TIME_PAGE_SIZE)
    if page != requested_page:
        await context.send_text("😔 Список времени уже обновился. Показываю актуальные окна 🙂")
    await _show_slots(context, slots, page=page, push_current=False)


async def handle_booking_service_page(context: RouterContext) -> None:
    """Move between service pages using short registered payloads."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
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

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    master_id = _mapped_value(context, _MASTER_MAP_STATE_KEY, context.event.callback_payload)
    masters = _masters(context)
    if not master_id or masters is None:
        await context.send_text("😔 Список мастеров уже обновился. Показываю актуальных мастеров 🙂")
        await _refresh_masters_after_stale(context)
        return

    master = next((item for item in masters if item.yclients_master_id == master_id), None)
    if master is None:
        await _show_masters(context, masters, push_current=False)
        return
    eligible_master_ids = _state_value(context, _ELIGIBLE_MASTER_IDS_STATE_KEY)
    if (
        _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST
        and isinstance(eligible_master_ids, list)
        and master.yclients_master_id not in eligible_master_ids
    ):
        await context.send_text(
            "Для выбранного времени нет доступных мастеров 🙏\n\nПопробуйте выбрать другое время или услугу.",
            keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD),
        )
        service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
        if isinstance(service_id, str) and service_id:
            await _open_datetime_first_masters(context, service_id, push_current=False)
        else:
            await _open_datetime_first_catalog(context, push_current=False)
        return

    entry_mode = _entry_mode(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, master.yclients_master_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, master.title)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, master.specialization)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, master.rating)
    if entry_mode != _ENTRY_MODE_DATETIME_FIRST:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, None)

    if entry_mode == _ENTRY_MODE_STAFF_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
        await _open_booking_catalog(context)
        return
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    if entry_mode == _ENTRY_MODE_DATETIME_FIRST and isinstance(booking_date, str) and booking_date:
        if _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY):
            await _show_booking_phone(context)
            return
        await _open_booking_slots(context, booking_date, stale_if_empty=True)
        return
    await _show_booking_dates(context)


async def handle_booking_master_any(context: RouterContext) -> None:
    """Telegram-equivalent 'Любой специалист': let YClients choose any available staff."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    entry_mode = _entry_mode(context)
    if entry_mode == _ENTRY_MODE_STAFF_FIRST:
        await _show_booking_hub(context, push_current=False)
        return
    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    if not isinstance(service_id, str) or not service_id:
        await _refresh_services_after_stale(context)
        return
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, "0")
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, "Любой специалист")
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, None)
    if entry_mode == _ENTRY_MODE_DATETIME_FIRST and _state_value(context, _SELECTED_DATE_STATE_KEY) and _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY):
        await _show_booking_phone(context)
        return
    await _show_booking_dates(context)


async def handle_booking_date(context: RouterContext) -> None:
    """Save selected date and load YClients slots."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    booking_date = _mapped_value(context, _DATE_MAP_STATE_KEY, context.event.callback_payload)
    if not booking_date:
        await context.send_text("😔 На эту дату свободного времени нет. Выберите другую дату.")
        await _refresh_dates_after_stale(context)
        return
    dates = _dates(context)
    if dates is not None and not any(item.isoformat() == booking_date for item in dates):
        await context.send_text("😔 На эту дату свободного времени нет. Выберите другую дату.")
        await _refresh_dates_after_stale(context)
        return
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, booking_date)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_DATE_STATE_KEY, booking_date)
    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and (
        not _state_value(context, _SELECTED_SERVICE_STATE_KEY) or not _state_value(context, _SELECTED_MASTER_STATE_KEY)
    ):
        await _open_datetime_first_slots(context, booking_date, stale_if_empty=True)
        return
    if not _state_value(context, _SELECTED_SERVICE_STATE_KEY) or not _state_value(context, _SELECTED_MASTER_STATE_KEY):
        await _open_booking_catalog(context)
        return
    await _open_booking_slots(context, booking_date, stale_if_empty=True)


async def handle_booking_slot(context: RouterContext) -> None:
    """Save selected slot and show the next-step placeholder."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return

    slot_time = _mapped_value(context, _SLOT_MAP_STATE_KEY, context.event.callback_payload)
    slots = _slots(context)
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    if not slot_time or slots is None or not isinstance(booking_date, str):
        await context.answer_callback()
        await context.send_text("😔 Это окно уже неактуально. Обновляю список 🙂")
        await _refresh_slots_after_stale(context)
        return

    slot = next((item for item in slots if item.time == slot_time), None)
    if slot is None:
        await context.answer_callback()
        await context.send_text("😔 Это окно уже неактуально. Обновляю список 🙂")
        await _refresh_slots_after_stale(context)
        return

    await context.answer_callback()
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, slot.time)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, slot.time)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, slot.datetime_iso)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, slot.raw)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_PHONE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_PHONE_SOURCE_STATE_KEY, None)
    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and (
        not _state_value(context, _SELECTED_SERVICE_STATE_KEY) or not _state_value(context, _SELECTED_MASTER_STATE_KEY)
    ):
        await _open_datetime_first_catalog(context)
        return
    await _show_booking_phone(context)


async def handle_booking_phone_use_registered(context: RouterContext) -> None:
    """Use the saved profile phone for booking and continue to confirmation."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    user = _current_user(context)
    phone = normalize_phone(user.phone if user is not None else None)
    if phone is None:
        await context.send_text(
            BOOKING_REGISTERED_PHONE_MISSING_TEXT,
            keyboard=booking_phone_keyboard(include_registered_phone=False, back_payload=BOOKING_BACK_PAYLOAD),
        )
        return
    await _save_booking_phone_and_confirm(context, phone=phone, source="registered")


async def handle_booking_phone_input(context: RouterContext) -> None:
    """Accept contact/manual phone on the booking phone step."""

    phone = extract_contact_phone(context.event.attachments)
    source = "contact" if phone is not None else "manual"
    if phone is None and contains_contact_attachment(context.event.attachments):
        await context.send_text(BOOKING_CONTACT_PHONE_MISSING_TEXT)
        return

    phone = phone or normalize_phone(context.event.text)
    if phone is None:
        await context.send_text(BOOKING_PHONE_INVALID_TEXT)
        return
    await _save_booking_phone_and_confirm(context, phone=phone, source=source)


async def handle_booking_cancel_draft(context: RouterContext) -> None:
    """Cancel only the draft booking flow before a YClients record is created."""

    if not _is_active_booking_screen(context):
        await show_booking_stale_callback(context)
        return
    await context.answer_callback()
    state.clear_state_data(_user_id(context), _chat_id(context))
    await context.send_text("❌ Запись отменена.")
    await show_home(context)


async def handle_booking_confirm(context: RouterContext) -> None:
    """Create a real YClients booking after the final confirmation tap."""

    if not _is_active_booking_screen(context) or state.get_current_screen(_user_id(context), _chat_id(context)) != state.BOOKING_CONFIRMATION_SCREEN:
        await show_booking_stale_callback(context)
        return

    lock_key = _confirm_lock_key(context)
    if _state_value(context, _BOOKING_CREATION_IN_PROGRESS_STATE_KEY) is True:
        logger.info(
            "MAX booking confirm duplicate diagnostic: max_user_id=%s lock_key=%s callback=%s action=%s",
            _user_id(context), lock_key, context.event.callback_payload, "duplicate",
        )
        await context.answer_callback()
        await context.send_text("⏳ Уже создаём запись, секундочку 🙂")
        return
    if _state_value(context, _BOOKING_COMPLETED_RECORD_ID_STATE_KEY):
        logger.info(
            "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s yclients_record_id=%s",
            _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "stale", _state_value(context, _BOOKING_COMPLETED_RECORD_ID_STATE_KEY),
        )
        await context.answer_callback()
        return
    if not acquire_action_lock(lock_key, ttl_seconds=BOOKING_CREATE_LOCK_TTL_SECONDS):
        logger.info(
            "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s",
            _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "duplicate",
        )
        await context.answer_callback()
        await context.send_text("⏳ Уже создаём запись, секундочку 🙂")
        return
    logger.info(
        "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s",
        _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "acquired",
    )
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, True)
    await _create_booking_after_lock(context, lock_key=lock_key)


async def _create_booking_after_lock(context: RouterContext, *, lock_key: str) -> None:
    booking_data = _booking_state_snapshot(context)
    user = _current_user(context)
    booking_phone = normalize_phone(str(booking_data.get("booking_phone") or ""))
    if booking_phone is None:
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        await context.answer_callback()
        await _show_booking_phone(context, push_current=False)
        return
    if user is None or not booking_data.get("selected_service_id") or not booking_data.get("selected_master_id") or not booking_data.get("selected_date") or not booking_data.get("selected_slot_time"):
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        await context.answer_callback()
        await context.send_text(BOOKING_CONFIRMATION_MISSING_DATA_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    logger.info(
        "MAX booking confirm diagnostic: platform_user_id_present=%s entry_mode=%s selected_service_id_present=%s selected_master_id_present=%s selected_date_present=%s selected_time_present=%s booking_phone_present=%s lock_active=%s completed_record_id_present=%s create_started=%s",
        bool(_user_id(context)),
        booking_data.get("entry_mode"),
        bool(booking_data.get("selected_service_id")),
        bool(booking_data.get("selected_master_id")),
        bool(booking_data.get("selected_date")),
        bool(booking_data.get("selected_slot_time")),
        bool(booking_phone),
        True,
        bool(_state_value(context, _BOOKING_COMPLETED_RECORD_ID_STATE_KEY)),
        True,
    )
    await context.answer_callback()
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slot_is_valid = await booking_service.revalidate_selected_slot(
            yclients_service_id=str(booking_data["selected_service_id"]),
            yclients_master_id=str(booking_data["selected_master_id"]),
            booking_date=str(booking_data["selected_date"]),
            booking_time=str(booking_data.get("selected_slot_time") or ""),
        )
    except BookingServiceError as exc:
        logger.warning(
            "Booking slot recheck failed: operation=confirm_booking_recheck service_id=%s master_id=%s date=%s slot_time=%s error_class=%s",
            booking_data.get("selected_service_id"),
            booking_data.get("selected_master_id"),
            booking_data.get("selected_date"),
            booking_data.get("selected_slot_time"),
            type(exc).__name__,
        )
        await _send_booking_service_error(context, exc, operation="load slots")
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        return
    selected_slot_time = str(booking_data.get("selected_slot_time") or "")
    if not slot_is_valid:
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
        release_action_lock(lock_key)
        await context.send_text(BOOKING_STALE_SLOT_TEXT)
        await _open_booking_slots(context, str(booking_data["selected_date"]), push_current=False, stale_if_empty=True)
        return
    try:
        created = await booking_service.create_booking(
            yclients_service_id=str(booking_data["selected_service_id"]),
            yclients_master_id=str(booking_data["selected_master_id"]),
            booking_date=str(booking_data["selected_date"]),
            booking_slot=str(booking_data["selected_slot_time"]),
            selected_datetime=_optional_state_text(booking_data.get("selected_datetime")),
            client_name=_user_full_name(user),
            client_phone=booking_phone,
            comment=apply_lost_client_discount_comment(
                apply_birthday_warning(
                    MAX_REPEAT_BOOKING_COMMENT_MARKER if booking_data.get("entry_mode") == _ENTRY_MODE_REPEAT else MAX_BOOKING_COMMENT_MARKER,
                    booking_source=_optional_state_text(booking_data.get("booking_source")),
                    birthday_discount_context=bool(booking_data.get("birthday_discount_context")),
                ),
                booking_origin_type=_optional_state_text(booking_data.get("booking_origin_type")),
                lost_days=booking_data.get("lost_days") if isinstance(booking_data.get("lost_days"), int) else None,
            ),
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
        logger.info(
            "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s error_type=%s",
            _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "failed", type(exc).__name__,
        )
        await _send_booking_service_error(context, exc, operation="create booking", fallback_text=BOOKING_CREATE_ERROR_TEXT)
    finally:
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)

    if 'created' not in locals():
        release_action_lock(lock_key)
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, created.yclients_record_id)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    logger.info(
        "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s yclients_record_id=%s",
        _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "created", created.yclients_record_id,
    )
    birthday_event_id = booking_data.get("birthday_event_id")
    if booking_data.get("booking_source") == "birthday_funnel" and isinstance(birthday_event_id, int):
        BirthdayFunnelEventsRepository(_database_path()).mark_status(
            birthday_event_id, "booked_from_birthday_gift", booking_id=created.yclients_record_id
        )
    _save_attribution_safely(
        platform_user_id=user.platform_user_id,
        yclients_record_id=created.yclients_record_id,
        yclients_client_id=created.yclients_client_id or user.yclients_client_id,
        marker=build_yclients_action_comment(
            MAX_REPEAT_BOOKING_COMMENT_MARKER if booking_data.get("entry_mode") == _ENTRY_MODE_REPEAT else MAX_BOOKING_COMMENT_MARKER,
            timezone_name=booking_service.get_branch_timezone(),
            action_type="local_booking_attribution",
        ),
        booking_phone=booking_phone,
        source="booking_created_from_max",
    )
    if created.datetime_iso:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, created.datetime_iso)
    try:
        await _send_immediate_confirmation_safely(context, created=created, user=user, booking_data=booking_data)
    finally:
        release_action_lock(lock_key)
        logger.info(
            "MAX booking confirm duplicate diagnostic: max_user_id=%s draft_id=%s lock_key=%s callback=%s action=%s yclients_record_id=%s",
            _user_id(context), _chat_id(context), lock_key, context.event.callback_payload, "released", created.yclients_record_id,
        )


async def handle_booking_home(context: RouterContext) -> None:
    """Handle Home from booking hub with Telegram-style booking state cleanup."""

    await context.answer_callback()
    if _is_active_booking_screen(context):
        _clear_booking_state(context)
    await show_home(context)


async def handle_booking_back(context: RouterContext) -> None:
    """Navigate back inside booking without affecting other flows."""

    await context.answer_callback()
    current_screen = state.get_current_screen(_user_id(context), _chat_id(context))
    entry_mode = _entry_mode(context)
    if current_screen == state.BOOKING_HUB_SCREEN:
        _clear_booking_state(context)
        await show_home(context)
        return
    if current_screen == state.BOOKING_CATEGORIES_SCREEN:
        if entry_mode == _ENTRY_MODE_REPEAT:
            await _show_repeat_source_screen(context)
            return
        if entry_mode == _ENTRY_MODE_STAFF_FIRST:
            masters = _masters(context)
            if masters is not None:
                await _show_masters(context, masters, push_current=False)
                return
            await _open_staff_first_masters(context, push_current=False)
            return
        if entry_mode == _ENTRY_MODE_DATETIME_FIRST:
            booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
            if isinstance(booking_date, str) and booking_date:
                slots = _slots(context)
                if slots is not None:
                    await _show_slots(context, slots, push_current=False)
                    return
                await _open_datetime_first_slots(context, booking_date, push_current=False, stale_if_empty=True)
                return
            await _open_datetime_first_dates(context, push_current=False)
            return
        await _show_booking_hub(context, push_current=False)
        return
    if current_screen == state.BOOKING_SERVICES_SCREEN:
        if entry_mode == _ENTRY_MODE_REPEAT:
            await _show_repeat_source_screen(context)
            return
        if entry_mode == _ENTRY_MODE_STAFF_FIRST and _state_value(context, _SELECTED_MASTER_STATE_KEY):
            masters = _masters(context)
            if masters is not None:
                await _show_masters(context, masters, push_current=False)
                return
            await _open_staff_first_masters(context, push_current=False)
            return
        if entry_mode == _ENTRY_MODE_DATETIME_FIRST:
            booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
            if isinstance(booking_date, str) and booking_date:
                await _open_datetime_first_slots(context, booking_date, push_current=False, stale_if_empty=True)
                return
        catalog = _catalog(context)
        if catalog and catalog.categories:
            await _show_categories(context, catalog.categories, push_current=False)
            return
        await show_home(context)
        return
    if current_screen == state.BOOKING_MASTERS_SCREEN:
        if entry_mode == _ENTRY_MODE_REPEAT:
            await _show_repeat_source_screen(context)
            return
        if entry_mode == _ENTRY_MODE_STAFF_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
            await _show_booking_hub(context, push_current=False)
            return
        await _show_selected_category_services(context)
        return
    if current_screen == state.BOOKING_DATES_SCREEN:
        if entry_mode == _ENTRY_MODE_REPEAT:
            await _show_repeat_source_screen(context)
            return
        if entry_mode == _ENTRY_MODE_DATETIME_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
            await _show_booking_hub(context, push_current=False)
            return
        if entry_mode == _ENTRY_MODE_STAFF_FIRST:
            await _show_selected_category_services(context)
            return
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
        if entry_mode == _ENTRY_MODE_DATETIME_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
            await _open_datetime_first_dates(context, push_current=False)
            return
        await _show_booking_dates(context, push_current=False)
        return
    if current_screen == state.BOOKING_PHONE_SCREEN:
        booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
        if isinstance(booking_date, str) and booking_date:
            slots = _slots(context)
            if slots is not None:
                await _show_slots(context, slots, push_current=False)
                return
            await _open_booking_slots(context, booking_date, push_current=False, stale_if_empty=True)
            return
        await _show_booking_dates(context, push_current=False)
        return
    if current_screen in {state.BOOKING_SLOT_SELECTED_SCREEN, state.BOOKING_CONFIRMATION_SCREEN}:
        await _show_booking_phone(context, push_current=False)
        return
    if current_screen == state.BOOKING_SUCCESS_SCREEN:
        await show_home(context)
        return
    if current_screen == state.BOOKING_SERVICE_SELECTED_SCREEN:
        await _show_selected_category_services(context)
        return
    await show_home(context)


async def _show_repeat_source_screen(context: RouterContext) -> None:
    source_screen = state.get_state_data_value(_user_id(context), _chat_id(context), _REPEAT_SOURCE_SCREEN_STATE_KEY)
    if source_screen == "my_bookings_history":
        await _show_repeat_history_source_screen(context)
        return

    source_booking = state.get_state_data_value(_user_id(context), _chat_id(context), "my_bookings_selected_booking")
    timezone_name = state.get_state_data_value(_user_id(context), _chat_id(context), "my_bookings_branch_timezone")
    if isinstance(source_booking, dict):
        from max_barbershop_bot.services.my_bookings import format_booking_details_text, is_booking_cancelable, is_booking_reschedulable, is_future_booking

        normalized_timezone = normalize_branch_timezone(str(timezone_name or DEFAULT_BRANCH_TIMEZONE), flow="booking", operation="repeat_back")
        state.set_current_screen(_user_id(context), _chat_id(context), state.MY_BOOKING_DETAILS_SCREEN)
        await context.send_text(
            format_booking_details_text(source_booking, timezone_name=normalized_timezone),
            keyboard=my_booking_details_keyboard(
                can_cancel=is_booking_cancelable(source_booking, timezone_name=normalized_timezone),
                is_active=is_future_booking(source_booking, timezone_name=normalized_timezone),
                can_reschedule=is_booking_reschedulable(source_booking, timezone_name=normalized_timezone),
            ),
        )
        return
    await show_home(context)


async def _show_repeat_history_source_screen(context: RouterContext) -> None:
    from max_barbershop_bot.services.my_bookings import format_visit_history_screen

    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    past_value = state.get_state_data_value(platform_user_id, chat_id, "my_bookings_past_items")
    past = [item for item in past_value if isinstance(item, dict)] if isinstance(past_value, list) else []
    raw_page = state.get_state_data_value(platform_user_id, chat_id, "my_bookings_history_page")
    page = raw_page if isinstance(raw_page, int) else 0
    page_size = 5
    max_page = max((len(past) - 1) // page_size, 0) if past else 0
    page = min(max(page, 0), max_page)
    timezone_name = state.get_state_data_value(platform_user_id, chat_id, "my_bookings_branch_timezone")
    normalized_timezone = normalize_branch_timezone(str(timezone_name or DEFAULT_BRANCH_TIMEZONE), flow="booking", operation="repeat_history_back")
    start = page * page_size
    end = start + page_size
    state.set_state_data_value(platform_user_id, chat_id, "my_bookings_history_page", page)
    state.set_current_screen(platform_user_id, chat_id, "my_bookings_history")
    await context.send_text(
        format_visit_history_screen(past, timezone_name=normalized_timezone, page=page, page_size=page_size),
        keyboard=my_bookings_history_keyboard(page=page, has_next=end < len(past), include_repeat=bool(past)),
    )

def _is_active_booking_screen(context: RouterContext) -> bool:
    return state.get_current_screen(_user_id(context), _chat_id(context)) in {
        state.BOOKING_HUB_SCREEN,
        state.BOOKING_CATEGORIES_SCREEN,
        state.BOOKING_SERVICES_SCREEN,
        state.BOOKING_SERVICE_SELECTED_SCREEN,
        state.BOOKING_MASTERS_SCREEN,
        state.BOOKING_MASTER_SELECTED_SCREEN,
        state.BOOKING_DATES_SCREEN,
        state.BOOKING_SLOTS_SCREEN,
        state.BOOKING_SLOT_SELECTED_SCREEN,
        state.BOOKING_PHONE_SCREEN,
        state.BOOKING_CONFIRMATION_SCREEN,
        state.BOOKING_SUCCESS_SCREEN,
    }



async def _send_booking_service_error(context: RouterContext, exc: BookingServiceError, *, operation: str, fallback_text: str | None = None) -> None:
    diagnostic = dict(getattr(exc, "diagnostic", {}) or {})
    selected_service = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    selected_master = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    selected_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    selected_time = _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY)
    diagnostic.update(
        {
            "operation": diagnostic.get("operation") or operation,
            "platform_user_id": context.event.platform_user_id,
            "chat_id": context.event.chat_id,
            "entry_mode": _entry_mode(context),
            "selected_service_id_present": bool(selected_service),
            "selected_master_id_present": bool(selected_master),
            "selected_date_present": bool(selected_date),
            "selected_time_present": bool(selected_time),
        }
    )
    logger.warning(
        "MAX booking yclients error diagnostic: trace_id=%s error_category=%s error_class=%s http_status=%s "
        "operation=%s endpoint_path=%s method=%s safe_response_snippet=%s entry_mode=%s "
        "selected_service_id_present=%s selected_master_id_present=%s selected_date_present=%s selected_time_present=%s",
        diagnostic.get("trace_id"),
        diagnostic.get("error_category"),
        diagnostic.get("error_class") or type(exc).__name__,
        diagnostic.get("http_status"),
        diagnostic.get("operation"),
        diagnostic.get("endpoint_path"),
        diagnostic.get("method"),
        diagnostic.get("safe_response_snippet"),
        diagnostic.get("entry_mode"),
        diagnostic.get("selected_service_id_present"),
        diagnostic.get("selected_master_id_present"),
        diagnostic.get("selected_date_present"),
        diagnostic.get("selected_time_present"),
    )
    await context.send_text(exc.user_message or fallback_text or BOOKING_CREATE_ERROR_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))

async def _show_booking_hub(context: RouterContext, *, push_current: bool = True) -> None:
    if push_current:
        _push_current_screen(context, state.BOOKING_HUB_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_HUB_SCREEN)
    await context.send_text(await _booking_hub_text(), keyboard=booking_hub_keyboard(back_payload=BOOKING_BACK_PAYLOAD))


async def _booking_hub_text() -> str:
    """Build the Telegram-reference booking hub text with safe MAX fallbacks."""

    branch_title = "барбершоп"
    address: str | None = None
    try:
        contacts = await ContactsService(YClientsSettingsRepository(_database_path())).get_contacts()
    except Exception as exc:  # noqa: BLE001 - contacts must not block booking entry.
        logger.warning("Booking hub contacts lookup failed safely: error_class=%s", type(exc).__name__)
    else:
        branch_title = contacts.title or branch_title
        if contacts.address and contacts.address != "—":
            address = contacts.address

    lines = [f"✂️ Запись в {branch_title}"]
    if address:
        lines.append(f"📍 {address}")
    lines.append("\nВыберите, с чего начать:")
    return "\n".join(lines)


def _clear_booking_state(context: RouterContext) -> None:
    for key in _BOOKING_STATE_KEYS:
        state.set_state_data_value(_user_id(context), _chat_id(context), key, None)


async def _refresh_categories_after_stale(context: RouterContext) -> None:
    """Refresh the logical category/service screen without changing booking branch context."""

    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and _state_value(context, _SELECTED_DATE_STATE_KEY) and _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY):
        await _open_datetime_first_catalog(context, push_current=False)
        return
    await _open_booking_catalog(context, push_current=False)


async def _refresh_services_after_stale(context: RouterContext) -> None:
    """Refresh current services for stale service callbacks while preserving branch selections."""

    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and _state_value(context, _SELECTED_DATE_STATE_KEY) and _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY):
        await _open_datetime_first_catalog(context, push_current=False)
        return
    if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST and _state_value(context, _SELECTED_CATEGORY_STATE_KEY):
        await _show_selected_category_services(context)
        return
    await _open_booking_catalog(context, push_current=False)


async def _refresh_masters_after_stale(context: RouterContext) -> None:
    """Refresh master selection for stale master callbacks branch-by-branch."""

    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and isinstance(service_id, str) and service_id:
        await _open_datetime_first_masters(context, service_id, push_current=False)
        return
    if isinstance(service_id, str) and service_id:
        await _open_booking_masters(context, service_id, push_current=False)
        return
    if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST:
        await _open_staff_first_masters(context, push_current=False)
        return
    await _open_booking_catalog(context, push_current=False)


async def _refresh_dates_after_stale(context: RouterContext) -> None:
    """Refresh date selection for stale date callbacks preserving entry mode."""

    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and not _state_value(context, _SELECTED_SERVICE_STATE_KEY):
        await _open_datetime_first_dates(context, push_current=False)
        return
    await _show_booking_dates(context, push_current=False)


async def _refresh_slots_after_stale(context: RouterContext) -> None:
    """Reload current slots for stale slot callbacks and never fall back to generic catalog."""

    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    if isinstance(booking_date, str) and booking_date:
        if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST and (
            not _state_value(context, _SELECTED_SERVICE_STATE_KEY) or not _state_value(context, _SELECTED_MASTER_STATE_KEY)
        ):
            await _open_datetime_first_slots(context, booking_date, push_current=False, stale_if_empty=True)
            return
        await _open_booking_slots(context, booking_date, push_current=False, stale_if_empty=True)
        return
    await _refresh_dates_after_stale(context)


def _set_cancellation_recovery_attribution(context: RouterContext, *, event_id: int, event: object) -> None:
    platform_user_id = _user_id(context)
    chat_id = _chat_id(context)
    state.set_state_data_value(platform_user_id, chat_id, "booking_source", "cancellation_recovery")
    state.set_state_data_value(platform_user_id, chat_id, "booking_origin_type", "cancellation_recovery")
    state.set_state_data_value(platform_user_id, chat_id, "notification_event_id", event_id)
    state.set_state_data_value(platform_user_id, chat_id, "cancellation_recovery_event_id", event_id)
    state.set_state_data_value(platform_user_id, chat_id, "yclients_client_id", getattr(event, "yclients_client_id", None))
    state.set_state_data_value(platform_user_id, chat_id, "notification_is_test", bool(getattr(event, "is_test", False)))
    state.set_state_data_value(platform_user_id, chat_id, "notification_source", getattr(event, "source", None))


async def _open_booking_catalog(context: RouterContext, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    entry_mode = _entry_mode(context)
    try:
        catalog = await booking_service.get_valid_categories_for_entry_mode(
            entry_mode=entry_mode,
            selected_master_id=_optional_state_text(_state_value(context, _SELECTED_MASTER_STATE_KEY)),
            selected_date=_optional_state_text(_state_value(context, _SELECTED_DATE_STATE_KEY)),
            selected_time=_optional_state_text(_state_value(context, _SELECTED_SLOT_TIME_STATE_KEY)),
        )
    except BookingServiceError as exc:
        if push_current:
            _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
        await _send_booking_service_error(context, exc, operation="load categories")
        return

    preserve_master = entry_mode == _ENTRY_MODE_STAFF_FIRST and bool(_state_value(context, _SELECTED_MASTER_STATE_KEY))
    preserve_datetime = entry_mode == _ENTRY_MODE_DATETIME_FIRST and bool(_state_value(context, _SELECTED_DATE_STATE_KEY)) and bool(_state_value(context, _SELECTED_SLOT_TIME_STATE_KEY))
    state.set_state_data_value(_user_id(context), _chat_id(context), _CATALOG_STATE_KEY, catalog)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_CATEGORY_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_NAME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_PRICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_DURATION_STATE_KEY, None)
    if not preserve_master:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_SPECIALIZATION_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_RATING_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY, None)
    if not preserve_datetime:
        state.set_state_data_value(_user_id(context), _chat_id(context), _DATES_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SLOTS_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_DATE_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_COMPLETED_RECORD_ID_STATE_KEY, None)

    if not has_available_services(catalog):
        if push_current:
            _push_current_screen(context, state.BOOKING_CATEGORIES_SCREEN)
        empty_text = (
            "😔 У этого мастера сейчас нет доступных услуг. Пожалуйста, выберите другого специалиста."
            if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST and _state_value(context, _SELECTED_MASTER_STATE_KEY)
            else "😔 Пока нет доступных категорий услуг."
        )
        await context.send_text(empty_text, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return

    if catalog.categories:
        await _show_categories(context, catalog.categories, push_current=push_current)
        return
    await _show_services(context, catalog.services, category_title=None, push_current=push_current)


async def _open_datetime_first_dates(context: RouterContext, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    try:
        dates = await booking_service.get_datetime_first_available_dates()
    except BookingServiceError as exc:
        logger.warning(
            "MAX booking datetime-first diagnostic: entry_mode=%s telegram_reference_function_used=%s "
            "yclients_settings_found=False api_error_class=%s",
            _ENTRY_MODE_DATETIME_FIRST,
            "_load_datetime_first_dates",
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_DATES_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
        await _send_booking_service_error(context, exc, operation="load dates")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _DATES_STATE_KEY, dates)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_DATE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
    if not dates:
        if push_current:
            _push_current_screen(context, state.BOOKING_DATES_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
        await context.send_text(BOOKING_DATES_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await _show_dates(
        context,
        dates,
        timezone_name=timezone_name,
        push_current=push_current,
        tail="📅 Выберите дату с доступными окнами:",
        include_selected_date=False,
    )


async def _open_datetime_first_slots(
    context: RouterContext,
    booking_date: str | date,
    *,
    push_current: bool = True,
    stale_if_empty: bool = False,
) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slots = await booking_service.get_datetime_first_slots_for_date(booking_date)
    except BookingServiceError as exc:
        logger.warning(
            "MAX booking datetime-first diagnostic: entry_mode=%s telegram_reference_function_used=%s selected_date=%s api_error_class=%s",
            _ENTRY_MODE_DATETIME_FIRST,
            "_load_datetime_first_slots_for_date",
            booking_date,
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_SLOTS_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SLOTS_SCREEN)
        await _send_booking_service_error(context, exc, operation="load slots")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOTS_STATE_KEY, slots)
    if not slots:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, None)
        await _refresh_datetime_first_dates_after_stale_slot(
            context,
            stale_text=BOOKING_STALE_SLOT_TEXT if stale_if_empty else None,
            push_current=push_current,
        )
        return
    await _show_slots(context, slots, push_current=push_current, tail="🕒 Выберите доступное время:")


async def _open_datetime_first_catalog(context: RouterContext, *, push_current: bool = True) -> None:
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    booking_time = _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY)
    if not isinstance(booking_date, str) or not booking_date or not isinstance(booking_time, str) or not booking_time:
        await _open_datetime_first_dates(context, push_current=False)
        return
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        catalog = await booking_service.get_datetime_first_services_for_slot(
            booking_date=booking_date,
            booking_time=booking_time,
        )
    except BookingServiceError as exc:
        await _send_booking_service_error(context, exc, operation="load services")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _CATALOG_STATE_KEY, catalog)
    state.set_state_data_value(
        _user_id(context),
        _chat_id(context),
        _ELIGIBLE_SERVICE_IDS_STATE_KEY,
        [service.yclients_service_id for service in catalog.services],
    )
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SERVICE_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_STATE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_NAME_STATE_KEY, None)
    if not has_available_services(catalog):
        await context.send_text(BOOKING_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    if catalog.categories:
        await _show_categories(context, catalog.categories, push_current=push_current)
        return
    await _show_services(context, catalog.services, category_title=None, push_current=push_current)


async def _open_datetime_first_masters(context: RouterContext, yclients_service_id: str, *, push_current: bool = True) -> None:
    booking_date = _state_value(context, _SELECTED_DATE_STATE_KEY)
    booking_time = _state_value(context, _SELECTED_SLOT_TIME_STATE_KEY)
    if not isinstance(booking_date, str) or not booking_date or not isinstance(booking_time, str) or not booking_time:
        await _open_datetime_first_dates(context, push_current=False)
        return

    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        masters = await booking_service.get_datetime_first_masters_for_slot(
            yclients_service_id=yclients_service_id,
            booking_date=booking_date,
            booking_time=booking_time,
        )
    except BookingServiceError as exc:
        await _send_booking_service_error(context, exc, operation="load masters")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY, masters)
    state.set_state_data_value(
        _user_id(context),
        _chat_id(context),
        _ELIGIBLE_MASTER_IDS_STATE_KEY,
        [master.yclients_master_id for master in masters],
    )
    if not has_available_masters(masters):
        await context.send_text(BOOKING_MASTERS_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await _show_masters(context, masters, push_current=push_current)


async def _refresh_datetime_first_dates_after_stale_slot(
    context: RouterContext,
    *,
    stale_text: str | None,
    push_current: bool,
) -> None:
    if stale_text:
        await context.send_text(stale_text)
    await _open_datetime_first_dates(context, push_current=push_current)


async def _open_staff_first_masters(context: RouterContext, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        masters = await booking_service.get_valid_masters_for_constraints(entry_mode=_ENTRY_MODE_STAFF_FIRST)
    except BookingServiceError as exc:
        logger.warning(
            "Booking staff-first masters screen failed: operation=show_booking_staff_first_masters error_class=%s",
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_MASTERS_SCREEN)
        await _send_booking_service_error(context, exc, operation="load masters")
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


async def _open_booking_masters(context: RouterContext, yclients_service_id: str, *, push_current: bool = True) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        masters = await booking_service.get_valid_masters_for_constraints(yclients_service_id=yclients_service_id, service=_selected_service(context), entry_mode=_entry_mode(context))
    except BookingServiceError as exc:
        logger.warning(
            "Booking masters screen failed: operation=show_booking_masters service_id=%s error_class=%s",
            yclients_service_id,
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_MASTERS_SCREEN)
        await _send_booking_service_error(context, exc, operation="load masters")
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
    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    if not isinstance(service_id, str) or not service_id:
        await _open_booking_catalog(context, push_current=push_current)
        return
    if (not isinstance(master_id, str) or not master_id) and _entry_mode(context) != _ENTRY_MODE_REPEAT:
        await _open_booking_catalog(context, push_current=push_current)
        return

    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    try:
        dates = await booking_service.get_available_dates_for_selection(
            yclients_service_id=service_id,
            yclients_master_id=(
                None
                if master_id == "0" and _entry_mode(context) == _ENTRY_MODE_REPEAT
                else (master_id if isinstance(master_id, str) and master_id else None)
            ),
            days=DATE_LOOKAHEAD_DAYS,
        )
    except BookingServiceError as exc:
        logger.warning(
            "Booking dates screen failed: operation=show_booking_dates entry_mode=%s service_id_present=%s master_id_present=%s error_class=%s",
            _entry_mode(context),
            bool(service_id),
            bool(master_id),
            type(exc).__name__,
        )
        if push_current:
            _push_current_screen(context, state.BOOKING_DATES_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
        await _send_booking_service_error(context, exc, operation="load dates")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _DATES_STATE_KEY, dates)
    if not dates:
        if push_current:
            _push_current_screen(context, state.BOOKING_DATES_SCREEN)
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
        await context.send_text(BOOKING_DATES_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await _show_dates(context, dates, timezone_name=timezone_name, push_current=push_current)


async def _open_booking_slots(context: RouterContext, booking_date: str, *, push_current: bool = True, stale_if_empty: bool = False) -> None:
    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    if not isinstance(service_id, str) or not service_id:
        await _show_booking_dates(context, push_current=False)
        return
    if (not isinstance(master_id, str) or not master_id) and _entry_mode(context) != _ENTRY_MODE_REPEAT:
        await _show_booking_dates(context, push_current=False)
        return

    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slots = await booking_service.get_available_slots(
            yclients_service_id=service_id,
            yclients_master_id=(
                None
                if master_id == "0" and _entry_mode(context) == _ENTRY_MODE_REPEAT
                else (master_id if isinstance(master_id, str) and master_id else None)
            ),
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
        await _send_booking_service_error(context, exc, operation="load slots")
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOTS_STATE_KEY, slots)
    if not slots:
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_TIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_SLOT_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_DATETIME_STATE_KEY, None)
        state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SLOT_RAW_STATE_KEY, None)
        await _refresh_dates_after_stale_slot(context, stale_text=BOOKING_SLOTS_EMPTY_TEXT if stale_if_empty else None, push_current=push_current)
        return
    await _show_slots(context, slots, push_current=push_current)


async def _refresh_dates_after_stale_slot(
    context: RouterContext,
    *,
    stale_text: str | None,
    push_current: bool,
) -> None:
    if stale_text:
        await context.send_text(stale_text)
    await _show_booking_dates(context, push_current=push_current)


async def _show_booking_phone(context: RouterContext, *, push_current: bool = True) -> None:
    user = _current_user(context)
    registered_phone = normalize_phone(user.phone if user is not None else None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _REGISTERED_PHONE_STATE_KEY, registered_phone)
    if push_current:
        _push_current_screen(context, state.BOOKING_PHONE_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_PHONE_SCREEN)
    if registered_phone:
        text = BOOKING_PHONE_TEXT.format(registered_phone=registered_phone)
    else:
        text = BOOKING_PHONE_WITHOUT_REGISTERED_TEXT
    await context.send_text(
        text,
        keyboard=booking_phone_keyboard(
            include_registered_phone=bool(registered_phone),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
    )


async def _save_booking_phone_and_confirm(context: RouterContext, *, phone: str, source: str) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_PHONE_STATE_KEY, phone)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_PHONE_SOURCE_STATE_KEY, source)
    await _show_booking_confirmation(context)


async def _show_booking_confirmation(context: RouterContext) -> None:
    booking_data = _booking_state_snapshot(context)
    if normalize_phone(str(booking_data.get("booking_phone") or "")) is None:
        await context.send_text(BOOKING_PHONE_INVALID_TEXT)
        await _show_booking_phone(context, push_current=False)
        return
    if not booking_data.get("selected_service_id") or not booking_data.get("selected_master_id"):
        await context.send_text(BOOKING_CONFIRMATION_MISSING_DATA_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    if not booking_data.get("selected_date") or not booking_data.get("selected_slot_time"):
        await context.send_text(BOOKING_CONFIRMATION_MISSING_DATA_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        await _show_booking_dates(context, push_current=False)
        return

    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    try:
        slot_is_valid = await booking_service.revalidate_selected_slot(
            yclients_service_id=str(booking_data["selected_service_id"]),
            yclients_master_id=str(booking_data["selected_master_id"]),
            booking_date=str(booking_data["selected_date"]),
            booking_time=str(booking_data.get("selected_slot_time") or ""),
        )
    except BookingServiceError as exc:
        await _send_booking_service_error(context, exc, operation="load slots")
        return
    if not slot_is_valid:
        await context.send_text("Это время уже недоступно 🙏\n\nПожалуйста, выберите другое время.")
        await _open_booking_slots(context, str(booking_data["selected_date"]), push_current=False, stale_if_empty=True)
        return
    timezone_name = booking_service.get_branch_timezone()
    contacts = await _booking_contacts_safely()
    _push_current_screen(context, state.BOOKING_CONFIRMATION_SCREEN)
    await context.send_text(
        format_booking_summary(booking_data, contacts=contacts, timezone_name=timezone_name),
        keyboard=booking_confirmation_keyboard(back_payload=BOOKING_BACK_PAYLOAD),
        attachments=_selected_master_photo_attachment(context),
    )


def _selected_master_photo_attachment(context: RouterContext) -> list[dict[str, object]] | None:
    try:
        service = MasterPhotosService(
            MasterPhotosRepository(_database_path()),
            YClientsSettingsRepository(_database_path()),
        )
        attachment = service.photo_attachments(_optional_state_text(_state_value(context, _SELECTED_MASTER_STATE_KEY)))
    except Exception as exc:  # noqa: BLE001 - photo is optional for confirmation UX.
        logger.warning("Booking master photo skipped safely: error_class=%s", type(exc).__name__)
        return None
    return attachment


async def _booking_contacts_safely():
    try:
        return await ContactsService(YClientsSettingsRepository(_database_path())).get_contacts()
    except Exception as exc:  # noqa: BLE001 - contacts must not block confirmation.
        logger.warning("Booking contacts skipped safely: error_class=%s", type(exc).__name__)
        return None


async def _show_booking_success(context: RouterContext) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SUCCESS_SCREEN)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)
    contacts = await _booking_contacts_safely()
    await context.send_text(
        format_booking_success(_booking_state_snapshot(context), contacts=contacts, timezone_name=timezone_name),
        keyboard=booking_success_keyboard(),
        attachments=_selected_master_photo_attachment(context),
    )


async def _send_immediate_confirmation_safely(context: RouterContext, *, created, user, booking_data: dict) -> None:
    booking_service = BookingService(YClientsSettingsRepository(_database_path()))
    timezone_name = booking_service.get_branch_timezone()
    database_path = _database_path()
    platform_user_id = user.platform_user_id
    yclients_record_id = created.yclients_record_id
    state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SUCCESS_SCREEN)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BOOKING_CREATION_IN_PROGRESS_STATE_KEY, False)

    booking_datetime = _parse_booking_datetime(created.datetime_iso or booking_data.get("selected_datetime"), timezone_name)
    history_existing = get_notification_history(
        database_path,
        platform=PLATFORM_MAX,
        platform_user_id=platform_user_id,
        yclients_record_id=yclients_record_id,
        notification_type=BOOKING_CONFIRMATION_IMMEDIATE,
    )
    contacts = await _booking_contacts_safely()
    success_text = format_booking_success(_booking_state_snapshot(context), contacts=contacts, timezone_name=timezone_name)
    if history_existing is not None:
        logger.info(
            "MAX immediate booking confirmation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s notification_type=%s notifications_enabled=%s history_existing=%s send_attempted=%s send_status=%s delivery_status=%s message_id_present=%s blocked_or_stopped=%s error_class=%s http_status=%s",
            bool(platform_user_id), bool(yclients_record_id), BOOKING_CONFIRMATION_IMMEDIATE, user.notifications_enabled, True, False, history_existing.status, history_existing.status, bool(history_existing.message_id), bool(history_existing.is_blocked or history_existing.is_stopped), None, history_existing.delivery_status_code,
        )
        await context.send_text(success_text, keyboard=booking_success_keyboard(), attachments=_selected_master_photo_attachment(context))
        return
    if not user.notifications_enabled:
        mark_notification_history_skipped(
            database_path,
            platform=PLATFORM_MAX,
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            notification_type=BOOKING_CONFIRMATION_IMMEDIATE,
            scheduled_for=None,
            reason="notifications_disabled",
            metadata={"label": "подтверждение записи"},
        )
        logger.info(
            "MAX immediate booking confirmation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s notification_type=%s notifications_enabled=%s history_existing=%s send_attempted=%s send_status=%s delivery_status=%s message_id_present=%s blocked_or_stopped=%s error_class=%s",
            bool(platform_user_id), bool(yclients_record_id), BOOKING_CONFIRMATION_IMMEDIATE, False, bool(history_existing), False, "skipped", "skipped", False, False, None,
        )
        await context.send_text(success_text, keyboard=booking_success_keyboard(), attachments=_selected_master_photo_attachment(context))
        return
    try:
        history = await send_immediate_confirmation(
            context.sender,
            database_path=database_path,
            platform_user_id=platform_user_id,
            max_user_id=user.max_user_id or context.event.max_user_id,
            chat_id=user.chat_id or context.event.chat_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=created.yclients_client_id or user.yclients_client_id,
            booking_datetime=booking_datetime,
            service_name=str(booking_data.get("selected_service_name") or "услуга"),
            master_name=str(booking_data.get("selected_master_name") or "ваш мастер"),
            timezone_name=timezone_name,
            keyboard=booking_success_keyboard(),
            text_override=success_text,
            attachments=_selected_master_photo_attachment(context),
        )
        logger.info(
            "MAX immediate booking confirmation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s notification_type=%s notifications_enabled=%s history_existing=%s send_attempted=%s send_status=%s delivery_status=%s message_id_present=%s blocked_or_stopped=%s error_class=%s http_status=%s",
            bool(platform_user_id), bool(yclients_record_id), BOOKING_CONFIRMATION_IMMEDIATE, True, bool(history_existing), not (history_existing and history and history.id == history_existing.id), history.status if history else None, history.status if history else None, bool(history and history.message_id), bool(history and (history.is_blocked or history.is_stopped)), None, history.delivery_status_code if history else None,
        )
        if history is None or history.status != "sent":
            await _show_booking_success(context)
    except Exception as exc:  # noqa: BLE001 - booking is already created; keep success flow intact.
        logger.warning(
            "MAX immediate booking confirmation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s notification_type=%s notifications_enabled=%s history_existing=%s send_attempted=%s send_status=%s delivery_status=%s message_id_present=%s blocked_or_stopped=%s error_class=%s",
            bool(platform_user_id), bool(yclients_record_id), BOOKING_CONFIRMATION_IMMEDIATE, user.notifications_enabled, bool(history_existing), True, "failed", "failed", False, False, type(exc).__name__,
        )
        await _show_booking_success(context)


def _parse_booking_datetime(value: object | None, timezone_name: str) -> datetime:
    parsed = localize_datetime(value, timezone_name)
    if parsed is not None:
        return parsed
    logger.warning("Booking datetime parse failed safely: value_present=%s", bool(str(value or "").strip()))
    return datetime.now(zoneinfo_or_default(timezone_name, flow="booking", operation="parse_datetime_fallback"))


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
        "selected_slot_raw": _state_value(context, _SELECTED_SLOT_RAW_STATE_KEY),
        "booking_phone": _state_value(context, _BOOKING_PHONE_STATE_KEY),
        "booking_phone_source": _state_value(context, _BOOKING_PHONE_SOURCE_STATE_KEY),
        "entry_mode": _state_value(context, _ENTRY_MODE_STATE_KEY),
        "booking_source": _state_value(context, "booking_source"),
        "birthday_event_id": _state_value(context, "birthday_event_id"),
        "birthday_discount_context": _state_value(context, "birthday_discount_context"),
        "birthday_is_test": _state_value(context, "birthday_is_test"),
        "birthday_source": _state_value(context, "birthday_source"),
        "birthday_claimed_at_utc": _state_value(context, "birthday_claimed_at_utc"),
        "booking_origin": _state_value(context, "booking_origin"),
        "booking_origin_type": _state_value(context, "booking_origin_type"),
        "lost_client_event_id": _state_value(context, "lost_client_event_id"),
        "notification_event_id": _state_value(context, "notification_event_id"),
        "lost_days": _state_value(context, "lost_days"),
        "notification_is_test": _state_value(context, "notification_is_test"),
        "notification_source": _state_value(context, "notification_source"),
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
    marker: str = MAX_BOOKING_COMMENT_MARKER,
    booking_phone: str | None = None,
    source: str | None = None,
) -> None:
    if not str(yclients_record_id or "").strip():
        logger.warning(
            "MAX booking create payload diagnostic: max_user_id=%s yclients_record_id_present=%s attribution_saved=%s",
            platform_user_id,
            False,
            False,
        )
        return
    try:
        PlatformAttributionRepository(_database_path()).create_if_missing(
            platform=PLATFORM_MAX,
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=yclients_client_id,
            marker=marker,
            booking_phone=booking_phone,
            source=source,
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
    selected_master_id = _state_value(context, _SELECTED_MASTER_STATE_KEY)
    if _entry_mode(context) == _ENTRY_MODE_STAFF_FIRST and isinstance(selected_master_id, str) and selected_master_id:
        booking_service = BookingService(YClientsSettingsRepository(_database_path()))
        try:
            services = await booking_service.get_valid_services_for_constraints(
                entry_mode=_ENTRY_MODE_STAFF_FIRST,
                selected_master_id=selected_master_id,
                services=services,
                category_id=category_id if isinstance(category_id, str) else None,
            )
        except BookingServiceError as exc:
            await _send_booking_service_error(context, exc, operation="load services")
            return
    await _show_services(context, services, category_title=category.title if category else None, push_current=False)


async def _show_categories(context: RouterContext, categories: list, *, page: int = 0, push_current: bool = True) -> None:
    page = _clamp_page(page, len(categories))
    start = page * BOOKING_PAGE_SIZE
    display_categories = categories[start : start + BOOKING_PAGE_SIZE]
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
        await context.send_text("😔 Пока нет доступных категорий услуг.", keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await context.send_text(
        _booking_category_text(context),
        keyboard=booking_categories_keyboard(
            display_categories,
            page=page,
            has_previous=page > 0,
            has_next=(page + 1) * BOOKING_PAGE_SIZE < len(categories),
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
    start = page * BOOKING_PAGE_SIZE
    display_services = services[start : start + BOOKING_PAGE_SIZE]
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
        empty_text = "😕 В этой категории пока нет доступных услуг." if category_title else "😔 Пока нет доступных категорий услуг."
        await context.send_text(empty_text, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    logger.info(
        "Booking services screen shown: service_count=%s category_title_present=%s",
        len(display_services),
        bool(category_title),
    )
    await context.send_text(
        _booking_service_text(context),
        keyboard=booking_services_keyboard(
            display_services,
            format_service_title,
            page=page,
            has_previous=page > 0,
            has_next=(page + 1) * BOOKING_PAGE_SIZE < len(services),
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
    start = page * BOOKING_PAGE_SIZE
    display_masters = masters[start : start + BOOKING_PAGE_SIZE]
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
            has_next=(page + 1) * BOOKING_PAGE_SIZE < len(masters),
            include_any_master=_entry_mode(context) in {_ENTRY_MODE_SERVICE_FIRST, _ENTRY_MODE_DATETIME_FIRST, _ENTRY_MODE_REPEAT}
            and bool(_state_value(context, _SELECTED_SERVICE_STATE_KEY))
            and bool(masters),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
    )


async def _show_dates(
    context: RouterContext,
    dates: list,
    *,
    timezone_name: str,
    page: int = 0,
    push_current: bool = True,
    tail: str = "📅 Выберите дату:",
    include_selected_date: bool = False,
) -> None:
    page = _clamp_page(page, len(dates), page_size=DATE_PAGE_SIZE)
    start = page * DATE_PAGE_SIZE
    display_dates = dates[start : start + DATE_PAGE_SIZE]
    date_payloads = {
        f"{BOOKING_DATE_PAYLOAD_PREFIX}{index}": item.isoformat()
        for index, item in enumerate(display_dates)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _DATE_MAP_STATE_KEY, date_payloads)
    state.set_state_data_value(_user_id(context), _chat_id(context), _DATE_PAGE_STATE_KEY, page)
    if push_current:
        _push_current_screen(context, state.BOOKING_DATES_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_DATES_SCREEN)
    attachment = _selected_master_photo_attachment(context)
    if not display_dates:
        await context.send_text(BOOKING_DATES_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await context.send_text(
        _booking_step_text(context, tail=tail, include_selected_date=include_selected_date),
        keyboard=booking_dates_keyboard(
            display_dates,
            lambda value: format_date_button(value, timezone_name=timezone_name),
            has_previous=page > 0,
            has_next=(page + 1) * DATE_PAGE_SIZE < len(dates),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
        attachments=attachment,
    )


async def _show_slots(
    context: RouterContext,
    slots: list[BookingSlotItem],
    *,
    page: int = 0,
    push_current: bool = True,
    tail: str = "🕐 Выберите удобное время:",
) -> None:
    page = _clamp_page(page, len(slots), page_size=TIME_PAGE_SIZE)
    start = page * TIME_PAGE_SIZE
    display_slots = slots[start : start + TIME_PAGE_SIZE]
    slot_payloads = {
        f"{BOOKING_SLOT_PAYLOAD_PREFIX}{index}": item.time
        for index, item in enumerate(display_slots)
    }
    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOT_MAP_STATE_KEY, slot_payloads)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SLOT_PAGE_STATE_KEY, page)
    if push_current:
        _push_current_screen(context, state.BOOKING_SLOTS_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BOOKING_SLOTS_SCREEN)
    attachment = _selected_master_photo_attachment(context)
    if not display_slots:
        await context.send_text(BOOKING_SLOTS_EMPTY_TEXT, keyboard=navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))
        return
    await context.send_text(
        _booking_step_text(context, tail=tail),
        keyboard=booking_slots_keyboard(
            display_slots,
            format_slot_button,
            has_previous=page > 0,
            has_next=(page + 1) * TIME_PAGE_SIZE < len(slots),
            back_payload=BOOKING_BACK_PAYLOAD,
        ),
        attachments=attachment,
    )


def _clamp_page(page: int, item_count: int, *, page_size: int = BOOKING_PAGE_SIZE) -> int:
    max_page = max((item_count - 1) // page_size, 0)
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
    text = str(price or "").strip()
    if not text:
        return None
    if any(marker in text.lower() for marker in ("₽", "руб")):
        return text
    try:
        amount = float(text.replace(",", "."))
    except ValueError:
        return f"{text} ₽"
    if amount <= 0:
        return None
    normalized = int(amount) if amount.is_integer() else amount
    return f"{normalized} ₽"


def _entry_mode(context: RouterContext) -> str:
    value = _state_value(context, _ENTRY_MODE_STATE_KEY)
    if value in {_ENTRY_MODE_SERVICE_FIRST, _ENTRY_MODE_STAFF_FIRST, _ENTRY_MODE_DATETIME_FIRST, _ENTRY_MODE_REPEAT}:
        return str(value)
    return _ENTRY_MODE_SERVICE_FIRST


def _booking_category_text(context: RouterContext) -> str:
    entry_mode = _entry_mode(context)
    if entry_mode == _ENTRY_MODE_STAFF_FIRST:
        return BOOKING_STAFF_FIRST_CATEGORY_TEXT
    if entry_mode == _ENTRY_MODE_DATETIME_FIRST:
        return BOOKING_DATETIME_FIRST_CATEGORY_TEXT
    return BOOKING_CATEGORY_TEXT


def _booking_service_text(context: RouterContext) -> str:
    if _entry_mode(context) == _ENTRY_MODE_DATETIME_FIRST:
        return BOOKING_DATETIME_FIRST_SERVICE_TEXT
    return BOOKING_SERVICE_TEXT


def _catalog(context: RouterContext) -> BookingCatalog | None:
    value = _state_value(context, _CATALOG_STATE_KEY)
    return value if isinstance(value, BookingCatalog) else None


def _selected_service(context: RouterContext) -> BookingServiceItem | None:
    service_id = _state_value(context, _SELECTED_SERVICE_STATE_KEY)
    catalog = _catalog(context)
    if not isinstance(service_id, str) or catalog is None:
        return None
    return next((item for item in catalog.services if item.yclients_service_id == service_id), None)


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


def _slots(context: RouterContext) -> list[BookingSlotItem] | None:
    value = _state_value(context, _SLOTS_STATE_KEY)
    if isinstance(value, list) and all(isinstance(item, BookingSlotItem) for item in value):
        return value
    return None


def _dates(context: RouterContext) -> list[date] | None:
    value = _state_value(context, _DATES_STATE_KEY)
    if isinstance(value, list) and all(isinstance(item, date) for item in value):
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


def _birthday_event_id_from_payload(payload: str | None) -> int | None:
    raw = (payload or "").removeprefix(BIRTHDAY_BUTTON_BOOK).lstrip(":")
    return int(raw) if raw.isdigit() else None
