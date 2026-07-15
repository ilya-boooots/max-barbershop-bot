import asyncio
from datetime import date

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import booking as booking_flow
from max_barbershop_bot.flows import my_bookings as my_flow
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.services.booking import BookingCatalog, BookingCategory, BookingMasterItem, BookingServiceItem, BookingSlotItem, CreatedBooking
from max_barbershop_bot.services.my_bookings import MyBookingReschedulePrepareError, MyBookingsProfileMissingError
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_CONFIRM_PAYLOAD,
    BOOKING_DATE_PAYLOAD_PREFIX,
    BOOKING_PHONE_USE_REGISTERED_PAYLOAD,
    BOOKING_SLOT_PAYLOAD_PREFIX,
    MY_BOOKINGS_REPEAT_START_PAYLOAD,
)

USER_ID = "u-repeat-complete"
CHAT_ID = "100500"
ACTIVE_SOURCE = state.MY_BOOKING_DETAILS_SCREEN
HISTORY_SOURCE = "my_bookings_history"


class Sender:
    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def ctx(payload=MY_BOOKINGS_REPEAT_START_PAYLOAD):
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id=USER_ID,
            max_user_id=USER_ID,
            chat_id=CHAT_ID,
            text=None,
            callback_payload=payload,
            callback_id=f"cb:{payload}",
        ),
        sender=Sender(),
    )


def booking(record_id="rec-1", *, service="Old service", master="Old master", staff_id="master", dt="2026-07-10T10:00:00+04:00"):
    return {
        "yclients_record_id": record_id,
        "booking_datetime": dt,
        "service_name": service,
        "master_name": master,
        "yclients_staff_id": staff_id,
        "status": "completed",
        "raw_status": "completed",
        "duration_minutes": "40",
        "price": "900 ₽",
    }


class FakeMyBookingsService:
    calls = []
    context = {
        "yclients_record_id": "rec-1",
        "service_ids": ["svc", "svc2"],
        "service_id": "svc",
        "service_name": "Source service",
        "staff_id": "master",
        "staff_name": "Source master",
        "price": "1200 ₽",
        "duration_minutes": 45,
        "branch_timezone": "Europe/Samara",
    }
    exc = None

    def __init__(self, *args, **kwargs):
        pass

    async def prepare_repeat_context(self, user, *, yclients_record_id, platform_user_id=None):
        self.__class__.calls.append((yclients_record_id, platform_user_id))
        if self.__class__.exc:
            raise self.__class__.exc
        return dict(self.__class__.context)


class FakeBookingService:
    catalog = BookingCatalog(
        categories=[BookingCategory(yclients_category_id="cat", title="Стрижки")],
        services=[
            BookingServiceItem(yclients_service_id="svc", title="Current service", yclients_category_id="cat", category_title="Стрижки", duration="50 мин", price_min=1500),
            BookingServiceItem(yclients_service_id="svc2", title="Second service", yclients_category_id="cat", category_title="Стрижки", duration="30 мин", price_min=700),
        ],
    )
    masters = [BookingMasterItem(yclients_master_id="master", title="Current master"), BookingMasterItem(yclients_master_id="other", title="Other master")]
    dates_seen = []
    slots_seen = []
    create_payloads = []
    revalidate_payloads = []
    created_count = 0

    def __init__(self, *args, **kwargs):
        pass

    def get_branch_timezone(self):
        return "Europe/Samara"

    async def get_valid_categories_for_entry_mode(self, *, entry_mode):
        return self.catalog

    async def get_available_masters_for_service(self, service_id, *, service=None, entry_mode=None):
        return list(self.masters)

    async def get_available_dates_for_selection(self, **kwargs):
        self.__class__.dates_seen.append(kwargs)
        return [date(2026, 7, 16)]

    async def get_available_slots(self, **kwargs):
        self.__class__.slots_seen.append(kwargs)
        return [BookingSlotItem(time="10:00", datetime_iso="2026-07-16T10:00:00+04:00", raw={"slot": 1})]

    async def revalidate_selected_slot(self, **kwargs):
        self.__class__.revalidate_payloads.append(kwargs)
        return True

    async def create_booking(self, **kwargs):
        self.__class__.created_count += 1
        self.__class__.create_payloads.append(kwargs)
        return CreatedBooking(yclients_record_id="new-1", yclients_client_id="client-1", datetime_iso=kwargs.get("selected_datetime"))


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    state.clear_state_data(USER_ID, CHAT_ID)
    FakeMyBookingsService.calls = []
    FakeMyBookingsService.exc = None
    FakeMyBookingsService.context = {
        "yclients_record_id": "rec-1",
        "service_ids": ["svc", "svc2"],
        "service_id": "svc",
        "service_name": "Source service",
        "staff_id": "master",
        "staff_name": "Source master",
        "price": "1200 ₽",
        "duration_minutes": 45,
        "branch_timezone": "Europe/Samara",
    }
    FakeBookingService.catalog = BookingCatalog(
        categories=[BookingCategory(yclients_category_id="cat", title="Стрижки")],
        services=[
            BookingServiceItem(yclients_service_id="svc", title="Current service", yclients_category_id="cat", category_title="Стрижки", duration="50 мин", price_min=1500),
            BookingServiceItem(yclients_service_id="svc2", title="Second service", yclients_category_id="cat", category_title="Стрижки", duration="30 мин", price_min=700),
        ],
    )
    FakeBookingService.masters = [BookingMasterItem(yclients_master_id="master", title="Current master"), BookingMasterItem(yclients_master_id="other", title="Other master")]
    FakeBookingService.dates_seen = []
    FakeBookingService.slots_seen = []
    FakeBookingService.create_payloads = []
    FakeBookingService.revalidate_payloads = []
    FakeBookingService.created_count = 0
    monkeypatch.setattr(my_flow, "MyBookingsService", FakeMyBookingsService)
    monkeypatch.setattr(booking_flow, "BookingService", FakeBookingService)
    user = User(1, "max", USER_ID, USER_ID, CHAT_ID, "Иван", "Иван", "Петров", None, "+79990000000", None, "user", "client-1", True)
    monkeypatch.setattr(my_flow, "_current_user", lambda context: user)
    monkeypatch.setattr(booking_flow, "_current_user", lambda context: user)

    async def fake_show_home(context):
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.MAIN_MENU_SCREEN)
        await context.send_text("🏠 Главное меню")

    async def fake_immediate(*args, **kwargs):
        return None

    monkeypatch.setattr(booking_flow, "show_home", fake_show_home)
    monkeypatch.setattr(booking_flow, "_send_immediate_confirmation_safely", fake_immediate)
    yield
    state.clear_state_data(USER_ID, CHAT_ID)


def seed_selected(source=ACTIVE_SOURCE, item=None):
    selected = item or booking()
    state.set_current_screen(USER_ID, CHAT_ID, source)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking", selected)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_branch_timezone", "Europe/Samara")
    if source == HISTORY_SOURCE:
        state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_items", [selected])
        state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_past_items", [selected])
        state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_history_page", 0)


def text_messages(c):
    return [m["text"] for m in c.sender.messages]


def test_real_handler_captures_active_source_and_stores_it():
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))

    assert c.sender.callbacks == ["cb:my_bookings:repeat:start"]
    assert FakeMyBookingsService.calls == [("rec-1", USER_ID)]
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == ACTIVE_SOURCE
    assert state.get_state_data_value(USER_ID, CHAT_ID, "booking_entry_mode") == "repeat_booking"
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_service_id") == "svc"
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_master_id") == "master"
    assert "Выберите дату" in text_messages(c)[-1]
    assert FakeBookingService.created_count == 0


def test_real_handler_captures_history_source_and_back_restores_history_detail():
    c = ctx()
    seed_selected(HISTORY_SOURCE, booking(record_id="hist-1", dt="2026-01-10T10:00:00+04:00"))
    FakeMyBookingsService.context["yclients_record_id"] = "hist-1"
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert FakeMyBookingsService.calls == [("hist-1", USER_ID)]
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == HISTORY_SOURCE

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SOURCE
    assert text_messages(back)[-1].startswith("🕘 История визитов")
    assert "Повторить запись" not in "Authorization traceback response_body"


def test_history_without_selected_booking_uses_global_history_repeat_candidate():
    c = ctx()
    state.set_current_screen(USER_ID, CHAT_ID, HISTORY_SOURCE)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_past_items", [booking(record_id="past-1")])
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_items", [booking(record_id="past-1")])
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert FakeMyBookingsService.calls == [("past-1", USER_ID)]
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == HISTORY_SOURCE


@pytest.mark.parametrize(
    "exc",
    [
        MyBookingsProfileMissingError("Не получилось найти ваши данные для записей 🙏"),
        MyBookingReschedulePrepareError("Эта запись уже неактуальна 🙏"),
        RuntimeError("Authorization token traceback raw_response endpoint client-1 rec-1"),
    ],
)
def test_real_handler_source_errors_are_friendly_and_masked(exc):
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    FakeMyBookingsService.exc = exc
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    joined = "\n".join(text_messages(c))
    assert "🙏" in joined
    for forbidden in ("Authorization", "token", "raw_response", "endpoint", "traceback", "client-1", "rec-1"):
        assert forbidden not in joined
    assert FakeBookingService.created_count == 0


def test_missing_selected_booking_and_missing_record_are_safe():
    c = ctx()
    state.set_current_screen(USER_ID, CHAT_ID, ACTIVE_SOURCE)
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert "запись" in text_messages(c)[-1].lower()

    c2 = ctx()
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking", {"service_name": "x"})
    asyncio.run(my_flow.handle_my_booking_repeat_start(c2))
    assert "запись" in text_messages(c2)[-1].lower()
    assert FakeBookingService.created_count == 0


def test_service_unavailable_fallback_clears_ids_and_back_returns_source():
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    FakeMyBookingsService.context["service_id"] = "gone"
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_service_id") is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == ACTIVE_SOURCE
    assert "Выберите" in text_messages(c)[-1]

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    assert state.get_current_screen(USER_ID, CHAT_ID) == ACTIVE_SOURCE
    assert "запись" in text_messages(back)[-1].lower()


def test_master_unavailable_fallback_preserves_service_no_first_master_and_back_returns_source():
    c = ctx()
    seed_selected(HISTORY_SOURCE)
    FakeMyBookingsService.context["staff_id"] = "gone-master"
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_service_id") == "svc"
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_master_id") is None
    assert "Выберите мастера" in text_messages(c)[-1]

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SOURCE


def test_no_source_master_uses_any_master_for_dates_and_slots_then_reaches_confirmation():
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    FakeMyBookingsService.context["staff_id"] = None
    FakeMyBookingsService.context["staff_name"] = None
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_yclients_master_id") == "0"
    assert FakeBookingService.dates_seen[-1]["yclients_master_id"] is None

    date_ctx = ctx(f"{BOOKING_DATE_PAYLOAD_PREFIX}0")
    asyncio.run(booking_flow.handle_booking_date(date_ctx))
    assert FakeBookingService.slots_seen[-1]["yclients_master_id"] is None

    slot_ctx = ctx(f"{BOOKING_SLOT_PAYLOAD_PREFIX}0")
    asyncio.run(booking_flow.handle_booking_slot(slot_ctx))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.BOOKING_PHONE_SCREEN

    phone_ctx = ctx(BOOKING_PHONE_USE_REGISTERED_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_phone_use_registered(phone_ctx))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.BOOKING_CONFIRMATION_SCREEN
    assert "Подтверд" in text_messages(phone_ctx)[-1]


def test_final_create_payload_unchanged_and_no_old_mutation_at_repeat_start():
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert FakeBookingService.created_count == 0

    asyncio.run(booking_flow.handle_booking_date(ctx(f"{BOOKING_DATE_PAYLOAD_PREFIX}0")))
    asyncio.run(booking_flow.handle_booking_slot(ctx(f"{BOOKING_SLOT_PAYLOAD_PREFIX}0")))
    asyncio.run(booking_flow.handle_booking_phone_use_registered(ctx(BOOKING_PHONE_USE_REGISTERED_PAYLOAD)))
    asyncio.run(booking_flow.handle_booking_confirm(ctx(BOOKING_CONFIRM_PAYLOAD)))

    assert FakeBookingService.created_count == 1
    payload = FakeBookingService.create_payloads[-1]
    assert payload["yclients_service_id"] == "svc"
    assert payload["yclients_master_id"] == "master"
    assert payload["booking_date"] == "2026-07-16"
    assert payload["booking_slot"] == "10:00"
    assert "rec-1" not in str(payload)


def test_stale_callbacks_preserve_repeat_source_and_do_not_select_wrong_entity():
    c = ctx()
    seed_selected(HISTORY_SOURCE)
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    source = state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen")

    state.set_state_data_value(USER_ID, CHAT_ID, "booking_date_payloads", {})
    stale_date = ctx(f"{BOOKING_DATE_PAYLOAD_PREFIX}9")
    asyncio.run(booking_flow.handle_booking_date(stale_date))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == source
    assert state.get_state_data_value(USER_ID, CHAT_ID, "booking_entry_mode") == "repeat_booking"
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_booking_date") is None

    state.set_state_data_value(USER_ID, CHAT_ID, "booking_slot_payloads", {})
    stale_slot = ctx(f"{BOOKING_SLOT_PAYLOAD_PREFIX}9")
    asyncio.run(booking_flow.handle_booking_slot(stale_slot))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen") == source
    assert state.get_state_data_value(USER_ID, CHAT_ID, "selected_booking_slot_time") is None


def test_home_clears_repeat_state_safely():
    c = ctx()
    seed_selected(ACTIVE_SOURCE)
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "booking_entry_mode") == "repeat_booking"
    asyncio.run(booking_flow.handle_booking_home(ctx("nav:home")))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "booking_entry_mode") is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking") is not None
