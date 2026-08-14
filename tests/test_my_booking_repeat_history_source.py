import asyncio
from datetime import date
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import booking as booking_flow
from max_barbershop_bot.flows import my_bookings as my_flow
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.services.booking import BookingCatalog, BookingCategory, BookingMasterItem, BookingServiceItem
from max_barbershop_bot.ui.buttons import BOOKING_BACK_PAYLOAD, MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX, MY_BOOKINGS_REPEAT_START_PAYLOAD

USER_ID = "u-repeat-history"
CHAT_ID = "777001"
HISTORY_SCREEN = "my_bookings_history"


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


def item(record_id, service, day, *, status="completed"):
    return {
        "yclients_record_id": record_id,
        "booking_datetime": f"2026-07-{day:02d}T10:00:00+04:00",
        "service_name": service,
        "master_name": "Макс",
        "yclients_staff_id": "master",
        "status": status,
        "raw_status": status,
        "duration_minutes": "40",
        "price": "900 ₽",
    }


class FakeMyBookingsService:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def prepare_repeat_context(self, user, *, yclients_record_id, platform_user_id=None):
        self.__class__.calls.append(yclients_record_id)
        return {
            "yclients_record_id": yclients_record_id,
            "service_ids": ["svc"],
            "service_id": "svc",
            "service_name": "Current service",
            "staff_id": "master",
            "staff_name": "Макс",
            "price": "1200 ₽",
            "duration_minutes": 45,
            "branch_timezone": "Europe/Samara",
        }


class FakeBookingService:
    created_count = 0

    def __init__(self, *args, **kwargs):
        pass

    def get_branch_timezone(self):
        return "Europe/Samara"

    async def get_valid_categories_for_entry_mode(self, *, entry_mode):
        return BookingCatalog(
            categories=[BookingCategory(yclients_category_id="cat", title="Услуги")],
            services=[BookingServiceItem(yclients_service_id="svc", title="Current service", yclients_category_id="cat", category_title="Услуги")],
        )

    async def get_available_masters_for_service(self, service_id, *, service=None, entry_mode=None):
        return [BookingMasterItem(yclients_master_id="master", title="Макс")]

    async def get_available_dates_for_selection(self, **kwargs):
        return [date(2026, 7, 20)]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    state.clear_state_data(USER_ID, CHAT_ID)
    FakeMyBookingsService.calls = []
    FakeBookingService.created_count = 0
    monkeypatch.setattr(my_flow, "MyBookingsService", FakeMyBookingsService)
    monkeypatch.setattr(booking_flow, "BookingService", FakeBookingService)
    user = User(1, "max", USER_ID, USER_ID, CHAT_ID, "Иван", "Иван", None, None, "+79990000000", None, "user", "client-1", True)
    monkeypatch.setattr(my_flow, "_current_user", lambda context: user)
    yield
    state.clear_state_data(USER_ID, CHAT_ID)


def seed_history(*, page=0, past=None, all_items=None):
    past = past if past is not None else [item("past-1", "Паж 0 первая", 1), item("past-2", "Паж 0 вторая", 2)]
    all_items = all_items if all_items is not None else past
    state.set_current_screen(USER_ID, CHAT_ID, HISTORY_SCREEN)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_past_items", past)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_items", all_items)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_history_page", page)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_branch_timezone", "Europe/Samara")


def buttons(message):
    return [(button.text, button.payload) for row in message["keyboard"].rows for button in row]


def test_telegram_history_repeat_model_is_global_latest_not_per_item_or_detail_first():
    source = Path("telegram_reference/app/handlers/my_bookings.py").read_text(encoding="utf-8")
    assert "[InlineKeyboardButton(text=\"🔂 Повторить запись\", callback_data=CB_HISTORY_REPEAT)]" in source
    assert "@router.callback_query(F.data == CB_HISTORY_REPEAT)" in source
    assert "card = next((c for c in reversed(cards) if c.service_id), None)" in source
    assert "CB_HISTORY_REPEAT:" not in source


def test_history_global_repeat_uses_latest_loaded_booking_not_selected_detail_or_past_last():
    latest = item("future-latest", "Самая новая запись", 30, status="active")
    past = [item("past-first", "История первая", 1), item("past-last", "История последняя", 2)]
    seed_history(page=0, past=past, all_items=past + [latest])
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking", item("wrong-selected", "Нельзя повторять", 3))

    c = ctx()
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))

    assert FakeMyBookingsService.calls == ["future-latest"]
    assert state.get_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking")["yclients_record_id"] == "future-latest"
    assert FakeBookingService.created_count == 0


def test_history_back_restores_full_history_page_zero_text_keyboard_and_state():
    past = [item("p0-a", "Услуга A", 1), item("p0-b", "Уход B", 2)]
    seed_history(page=0, past=past, all_items=past)
    asyncio.run(my_flow.handle_my_booking_repeat_start(ctx()))

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    message = back.sender.messages[-1]
    assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SCREEN
    assert message["text"].startswith("🕘 История визитов")
    assert "Услуга A" in message["text"] and "Уход B" in message["text"]
    assert "📋 Активная запись" not in message["text"]
    payloads = buttons(message)
    assert ("🔂 Повторить запись", MY_BOOKINGS_REPEAT_START_PAYLOAD) in payloads
    assert not any(text == "❌ Отменить запись" for text, _ in payloads)


def test_history_back_restores_page_one_and_pagination_payloads():
    past = [item(f"p{i}", f"Услуга {i}", i + 1) for i in range(7)]
    seed_history(page=1, past=past, all_items=past)
    asyncio.run(my_flow.handle_my_booking_repeat_start(ctx()))

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    message = back.sender.messages[-1]
    assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SCREEN
    assert "Услуга 5" in message["text"] and "Услуга 6" in message["text"]
    assert "Услуга 0" not in message["text"]
    payloads = buttons(message)
    assert ("⬅️", f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}0") in payloads
    assert not any(payload == f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}2" for _, payload in payloads)


def test_history_back_clamps_after_list_shrink_and_empty_history_is_history_ui():
    seed_history(page=3, past=[item("only", "Единственная", 1)], all_items=[item("only", "Единственная", 1)])
    asyncio.run(my_flow.handle_my_booking_repeat_start(ctx()))
    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    assert state.get_state_data_value(USER_ID, CHAT_ID, "my_bookings_history_page") == 0
    assert "Единственная" in back.sender.messages[-1]["text"]

    seed_history(page=1, past=[], all_items=[])
    state.set_state_data_value(USER_ID, CHAT_ID, "repeat_source_screen", HISTORY_SCREEN)
    state.set_state_data_value(USER_ID, CHAT_ID, "booking_entry_mode", "repeat_booking")
    state.set_current_screen(USER_ID, CHAT_ID, state.BOOKING_DATES_SCREEN)
    empty_back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(empty_back))
    assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SCREEN
    assert "История визитов пока пуста" in empty_back.sender.messages[-1]["text"]
    assert not any(text == "🔂 Повторить запись" for text, _ in buttons(empty_back.sender.messages[-1]))


def test_detail_source_back_restores_detail_ui_not_history_keyboard():
    detail = item("detail-1", "Детальная услуга", 5)
    state.set_current_screen(USER_ID, CHAT_ID, state.MY_BOOKING_DETAILS_SCREEN)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_selected_booking", detail)
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_items", [detail])
    state.set_state_data_value(USER_ID, CHAT_ID, "my_bookings_branch_timezone", "Europe/Samara")
    asyncio.run(my_flow.handle_my_booking_repeat_start(ctx()))

    back = ctx(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking_flow.handle_booking_back(back))
    message = back.sender.messages[-1]
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_DETAILS_SCREEN
    assert "Детальная услуга" in message["text"]
    assert not message["text"].startswith("🕘 История визитов")
    assert not any(payload and payload.startswith(MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX) for _, payload in buttons(message))


@pytest.mark.parametrize("fallback", ["service", "master", "dates"])
def test_history_back_from_fallback_screens_restores_history(fallback):
    past = [item("p0", "История", 1)]
    seed_history(page=0, past=past, all_items=past)
    if fallback == "service":
        # make source service unavailable so the first screen is category/service fallback
        original = FakeMyBookingsService.prepare_repeat_context
        async def prepare(self, user, *, yclients_record_id, platform_user_id=None):
            data = await original(self, user, yclients_record_id=yclients_record_id, platform_user_id=platform_user_id)
            data["service_id"] = "missing"
            return data
        FakeMyBookingsService.prepare_repeat_context = prepare
    elif fallback == "master":
        original = FakeMyBookingsService.prepare_repeat_context
        async def prepare(self, user, *, yclients_record_id, platform_user_id=None):
            data = await original(self, user, yclients_record_id=yclients_record_id, platform_user_id=platform_user_id)
            data["staff_id"] = "missing-master"
            return data
        FakeMyBookingsService.prepare_repeat_context = prepare
    try:
        asyncio.run(my_flow.handle_my_booking_repeat_start(ctx()))
        back = ctx(BOOKING_BACK_PAYLOAD)
        asyncio.run(booking_flow.handle_booking_back(back))
        assert state.get_current_screen(USER_ID, CHAT_ID) == HISTORY_SCREEN
        assert back.sender.messages[-1]["text"].startswith("🕘 История визитов")
        assert "История" in back.sender.messages[-1]["text"]
    finally:
        if fallback in {"service", "master"}:
            FakeMyBookingsService.prepare_repeat_context = original


def test_removed_history_target_is_safe_and_does_not_repeat_another_record():
    seed_history(page=0, past=[item("p1", "История", 1)], all_items=[])
    c = ctx()
    asyncio.run(my_flow.handle_my_booking_repeat_start(c))
    assert FakeMyBookingsService.calls == []
    assert "запись" in c.sender.messages[-1]["text"].lower()
    assert "p1" not in c.sender.messages[-1]["text"]
