import asyncio

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import booking as flow
from max_barbershop_bot.services.booking import BookingCatalog, BookingCategory, BookingMasterItem, BookingServiceItem


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


def ctx():
    return RouterContext(
        event=NormalizedEvent(update_type="message_callback", platform_user_id="u-repeat", max_user_id="u-repeat", chat_id="100", text=None, callback_payload="x", callback_id="cb"),
        sender=Sender(),
    )


class FakeBookingService:
    catalog = BookingCatalog(
        categories=[BookingCategory(yclients_category_id="cat", title="Услуги")],
        services=[BookingServiceItem(yclients_service_id="svc", title="Услуга", yclients_category_id="cat", category_title="Услуги", duration="45 мин")],
    )
    masters = [BookingMasterItem(yclients_master_id="master", title="Макс")]
    create_calls = cancel_calls = reschedule_calls = update_calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def get_valid_categories_for_entry_mode(self, *, entry_mode):
        return self.catalog

    async def get_available_masters_for_service(self, service_id, *, service=None, entry_mode=None):
        return self.masters

    def get_branch_timezone(self):
        return "Europe/Samara"

    async def get_available_dates_for_selection(self, **kwargs):
        from datetime import date
        return [date(2026, 7, 16)]


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    state.clear_state_data("u-repeat", "100")
    monkeypatch.setattr(flow, "BookingService", FakeBookingService)
    yield
    state.clear_state_data("u-repeat", "100")


def test_repeat_valid_service_and_master_opens_dates_and_clears_old_state():
    c = ctx()
    state.set_state_data_value("u-repeat", "100", "selected_booking_date", "2026-01-01")
    state.set_state_data_value("u-repeat", "100", "selected_booking_slot_time", "10:00")
    state.set_state_data_value("u-repeat", "100", "selected_booking_datetime", "old")
    state.set_state_data_value("u-repeat", "100", "selected_booking_slot_raw", {"old": True})

    asyncio.run(flow.start_repeat_booking_with_prefill(c, service_id="svc", service_name="Старая", master_id="master", master_name="Старый", service_price="1000 ₽", service_duration="40 мин", source_screen="my_booking_details"))

    assert state.get_state_data_value("u-repeat", "100", "booking_entry_mode") == "repeat_booking"
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_service_id") == "svc"
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_master_id") == "master"
    assert state.get_state_data_value("u-repeat", "100", "selected_booking_date") is None
    assert state.get_state_data_value("u-repeat", "100", "selected_booking_slot_time") is None
    assert state.get_state_data_value("u-repeat", "100", "selected_booking_datetime") is None
    assert state.get_state_data_value("u-repeat", "100", "selected_booking_slot_raw") is None
    assert "Выберите дату" in c.sender.messages[-1]["text"]
    assert FakeBookingService.create_calls == FakeBookingService.cancel_calls == FakeBookingService.reschedule_calls == FakeBookingService.update_calls == 0


def test_repeat_missing_master_falls_back_to_master_selection_without_first_master_choice():
    c = ctx()
    asyncio.run(flow.start_repeat_booking_with_prefill(c, service_id="svc", service_name="Услуга", master_id="gone", master_name="Старый", source_screen="my_booking_details"))
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_service_id") == "svc"
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_master_id") is None
    assert "Выберите мастера" in c.sender.messages[-1]["text"]


def test_repeat_missing_service_falls_back_to_service_selection_without_wrong_service():
    c = ctx()
    asyncio.run(flow.start_repeat_booking_with_prefill(c, service_id="gone", service_name="Старая", master_id="master", master_name="Макс", source_screen="my_booking_details"))
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_service_id") is None
    assert "Выберите категорию" in c.sender.messages[-1]["text"] or "Выберите услугу" in c.sender.messages[-1]["text"]


def test_repeat_any_master_uses_none_master_and_opens_dates():
    c = ctx()
    asyncio.run(flow.start_repeat_booking_with_prefill(c, service_id="svc", service_name="Услуга", master_id=None, master_name="Любой мастер", source_screen="my_booking_details"))
    assert state.get_state_data_value("u-repeat", "100", "selected_yclients_master_id") == "0"
    assert state.get_state_data_value("u-repeat", "100", "selected_master_name") == "Любой мастер"
    assert "Выберите дату" in c.sender.messages[-1]["text"]
