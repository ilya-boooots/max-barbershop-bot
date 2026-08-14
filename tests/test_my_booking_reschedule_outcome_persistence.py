from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import _locks
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import my_bookings as flow
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.services.booking import BookingSlotItem
from max_barbershop_bot.services import my_bookings as svc
from max_barbershop_bot.ui.buttons import MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD, MY_BOOKINGS_RESCHEDULE_START_PAYLOAD

USER_ID = "1001"
CHAT_ID = "2001"
OLD_DT = "2026-07-20 10:00:00"
NEW_DT = "2026-07-21 11:30:00"
SUCCESS_TEXT = "Запись перенесена ✅\n\nНовая дата: 21.07.2026\nНовое время: 11:30"
PARTIAL_TEXT = svc.MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT


@dataclass
class FakeSender:
    messages: list[tuple[str, MaxInlineKeyboard | None]]
    callbacks: list[str]

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


def context(payload: str = MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD) -> RouterContext:
    sender = FakeSender([], [])
    event = NormalizedEvent(
        update_type="message_callback",
        platform_user_id=USER_ID,
        max_user_id=USER_ID,
        chat_id=CHAT_ID,
        text=None,
        callback_payload=payload,
        callback_id=f"cb-{payload}",
    )
    return RouterContext(event=event, sender=sender)


def sent(ctx: RouterContext) -> list[str]:
    return [text for text, _ in ctx.sender.messages]


def keyboards(ctx: RouterContext) -> list[MaxInlineKeyboard | None]:
    return [keyboard for _, keyboard in ctx.sender.messages]


def reschedule_context(record_id: str = "old-1") -> dict[str, object]:
    return {
        "yclients_record_id": record_id,
        "service_id": "svc-1",
        "service_ids": ["svc-1"],
        "service_name": "Услуга",
        "staff_id": "staff-1",
        "staff_name": "Максим",
        "client_data": {"id": "client-1", "phone": "+79991234567", "name": "Иван"},
        "seance_length": 60,
        "old_date": "20.07.2026",
        "old_time": "10:00",
        "old_datetime": OLD_DT,
        "branch_timezone": "Europe/Samara",
    }


def selected_booking(record_id: str = "old-1") -> dict[str, object]:
    return {
        "yclients_record_id": record_id,
        "id": record_id,
        "service_name": "Услуга",
        "master_name": "Максим",
        "datetime": OLD_DT,
        "date": "20.07.2026",
        "time": "10:00",
        "status": "active",
    }


def seed_confirm_state() -> None:
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_CONTEXT_STATE_KEY, reschedule_context())
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_DATES_STATE_KEY, ["2026-07-21"])
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_SLOTS_STATE_KEY, [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)])
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_DATE_STATE_KEY, "2026-07-21")
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_SLOT_STATE_KEY, {"new_datetime": NEW_DT, "new_time": "11:30", "new_date": "21.07.2026"})
    state.set_state_data_value(USER_ID, CHAT_ID, flow._SELECTED_BOOKING_STATE_KEY, selected_booking())
    state.set_current_screen(USER_ID, CHAT_ID, state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)


class FakeBookingService:
    slots = [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)]
    dates = [date(2026, 7, 21)]
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, repo):
        self.repo = repo

    async def get_available_slots(self, **kwargs):
        self.calls.append(("slots", kwargs))
        return list(self.slots)

    async def get_available_dates(self, **kwargs):
        self.calls.append(("dates", kwargs))
        return list(self.dates)

    async def get_available_dates_for_selection(self, **kwargs):
        self.calls.append(("dates", kwargs))
        return list(self.dates)


class FakeMyBookingsService:
    calls: list[tuple[str, dict[str, object]]] = []
    mutation_error: Exception | None = None

    def __init__(self, repo):
        self.repo = repo

    async def prepare_reschedule_context(self, user, *, yclients_record_id, platform_user_id=None):
        self.calls.append(("prepare", {"record_id": yclients_record_id}))
        return reschedule_context(str(yclients_record_id))

    async def revalidate_reschedule_source(self, user, *, reschedule_context, platform_user_id=None):
        self.calls.append(("revalidate", dict(reschedule_context)))
        return dict(reschedule_context)

    async def reschedule_booking_for_user(self, user, *, reschedule_context, new_datetime_iso, platform_user_id=None):
        self.calls.append(("mutate", {"context": dict(reschedule_context), "new_datetime_iso": new_datetime_iso}))
        if self.mutation_error:
            raise self.mutation_error
        return {"old_record_id": "old-1", "new_record_id": "new-1", "new_datetime": new_datetime_iso}


@pytest.fixture(autouse=True)
def patch_flow(monkeypatch):
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    FakeBookingService.calls = []
    FakeMyBookingsService.calls = []
    FakeMyBookingsService.mutation_error = None
    monkeypatch.setattr(flow, "BookingService", FakeBookingService)
    monkeypatch.setattr(flow, "MyBookingsService", FakeMyBookingsService)
    monkeypatch.setattr(flow, "_current_user", lambda ctx: type("User", (), {"id": 1, "phone": "+79991234567", "yclients_client_id": "client-1"})())
    monkeypatch.setattr(flow, "_booking_master_photo_attachment", lambda booking: None)
    yield
    _locks.clear()
    state.clear_user_state(USER_ID, CHAT_ID)


def test_first_full_success_stores_success_outcome_and_result():
    seed_confirm_state()
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert [name for name, _ in FakeMyBookingsService.calls] == ["revalidate", "mutate"]
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY) == flow._RESCHEDULE_OUTCOME_SUCCESS
    result = state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY)
    assert result["outcome"] == flow._RESCHEDULE_OUTCOME_SUCCESS
    assert result["text"] == sent(ctx)[-1]
    assert "Запись перенесена ✅" in result["text"]
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_RESCHEDULE_SUCCESS_SCREEN
    assert not _locks


def test_repeated_callback_after_full_success_uses_saved_result_without_mutation():
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY, flow._RESCHEDULE_OUTCOME_SUCCESS)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY, {"outcome": flow._RESCHEDULE_OUTCOME_SUCCESS, "text": SUCCESS_TEXT, "old_record_id": "old-1", "new_record_id": "new-1"})
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert FakeMyBookingsService.calls == []
    assert sent(ctx) == [SUCCESS_TEXT]
    assert keyboards(ctx)[-1] is not None
    assert "old-1" not in sent(ctx)[0] and "new-1" not in sent(ctx)[0]
    assert "—" not in sent(ctx)[0]


def test_first_partial_failure_stores_partial_outcome_and_message():
    seed_confirm_state()
    FakeMyBookingsService.mutation_error = svc.MyBookingRescheduleError(PARTIAL_TEXT, diagnostic={"new_record_id": "new-partial", "partial_failure": True})
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert [name for name, _ in FakeMyBookingsService.calls] == ["revalidate", "mutate"]
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY) == flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY) == "old-1"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY) == "new-partial"
    result = state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY)
    assert result["outcome"] == flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE
    assert result["text"] == PARTIAL_TEXT
    assert PARTIAL_TEXT in sent(ctx)[-1]
    assert "Запись перенесена ✅" not in sent(ctx)[-1]
    assert not _locks


def test_repeated_callback_after_partial_failure_uses_saved_partial_result():
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY, flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY, {"outcome": flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE, "text": PARTIAL_TEXT, "old_record_id": "old-1", "new_record_id": "new-partial"})
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert FakeMyBookingsService.calls == []
    assert sent(ctx) == [PARTIAL_TEXT]
    assert "Запись перенесена ✅" not in sent(ctx)[0]
    assert keyboards(ctx)[-1] is not None


def test_new_reschedule_start_clears_old_outcome_and_initializes_new_context():
    state.clear_user_state(USER_ID, CHAT_ID)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._SELECTED_BOOKING_STATE_KEY, selected_booking("old-2"))
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY, flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY, {"outcome": flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE, "text": PARTIAL_TEXT})
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY, "old-1")
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY, "new-partial")
    ctx = context(MY_BOOKINGS_RESCHEDULE_START_PAYLOAD)

    asyncio.run(flow.handle_my_booking_reschedule_start(ctx))

    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_CONTEXT_STATE_KEY)["yclients_record_id"] == "old-2"


def test_in_progress_callback_regression():
    seed_confirm_state()
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_IN_PROGRESS_STATE_KEY, True)
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert sent(ctx) == [svc.MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT]
    assert FakeMyBookingsService.calls == []


def test_full_success_cleanup_regression_keeps_persisted_outcome():
    seed_confirm_state()
    ctx = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))

    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_CONTEXT_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_DATES_STATE_KEY) == []
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_SLOTS_STATE_KEY) == []
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_SLOT_STATE_KEY) is None
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY) == flow._RESCHEDULE_OUTCOME_SUCCESS
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_RESULT_STATE_KEY)["text"] == sent(ctx)[-1]


def test_partial_failure_regression_no_second_create_and_ids_retained():
    seed_confirm_state()
    FakeMyBookingsService.mutation_error = svc.MyBookingRescheduleError(PARTIAL_TEXT, diagnostic={"new_record_id": "new-partial", "partial_failure": True})
    ctx = context()
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))
    before = list(FakeMyBookingsService.calls)
    ctx_repeat = context()

    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx_repeat))

    assert FakeMyBookingsService.calls == before
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY) == "old-1"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY) == "new-partial"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_OUTCOME_STATE_KEY) == flow._RESCHEDULE_OUTCOME_PARTIAL_FAILURE
    assert sent(ctx_repeat) == [PARTIAL_TEXT]


def test_scope_safety_repeat_cancel_and_no_aiogram_unchanged():
    source = __import__("pathlib").Path("max_barbershop_bot/flows/my_bookings.py").read_text()
    assert "async def handle_my_booking_repeat_start" in source
    assert "async def handle_my_booking_cancel_start" in source
    assert "async def handle_my_booking_cancel_confirm" in source
    assert "from aiogram" not in source and "import aiogram" not in source
