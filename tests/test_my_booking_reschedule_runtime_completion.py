from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import _locks
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.integrations.yclients.exceptions import (
    YClientsAuthError,
    YClientsError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsValidationError,
)
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.services.booking import BookingServiceError, BookingSlotItem
from max_barbershop_bot.services import my_bookings as svc
from max_barbershop_bot.flows import my_bookings as flow
from max_barbershop_bot.ui.buttons import (
    MY_BOOKINGS_BACK_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX,
    NAV_HOME_PAYLOAD,
    my_booking_reschedule_confirmation_keyboard,
)

TZ = "Europe/Samara"
USER_ID = "1001"
CHAT_ID = "2001"
OLD_DT = "2026-07-20 10:00:00"
NEW_DT = "2026-07-21 11:30:00"


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


def context(payload: str) -> RouterContext:
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


def sent_texts(ctx: RouterContext) -> list[str]:
    return [text for text, _ in ctx.sender.messages]


def base_booking(status: str = "active", minutes: int = 120) -> dict[str, object]:
    dt = datetime.now(ZoneInfo(TZ)) + timedelta(minutes=minutes)
    return {
        "id": "old-1",
        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "services": [{"id": "svc-1", "title": "Стрижка", "seance_length": 60}],
        "staff": {"id": "staff-1", "name": "Максим"},
        "client": {"id": "client-1", "name": "Иван", "phone": "+79991234567"},
        "seance_length": 60,
    }


def reschedule_context(old_datetime: str = OLD_DT) -> dict[str, object]:
    return {
        "yclients_record_id": "old-1",
        "service_id": "svc-1",
        "service_ids": ["svc-1"],
        "service_name": "Стрижка",
        "staff_id": "staff-1",
        "staff_name": "Максим",
        "client_data": {"id": "client-1", "phone": "+79991234567", "name": "Иван"},
        "seance_length": 60,
        "old_date": "20.07.2026",
        "old_time": "10:00",
        "old_datetime": old_datetime,
        "branch_timezone": TZ,
    }


def seed_reschedule_state(*, slots=None, slot_data=None, dates=None, current_screen=None, old_datetime: str = OLD_DT):
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_CONTEXT_STATE_KEY, reschedule_context(old_datetime))
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_DATES_STATE_KEY, dates or ["2026-07-21"])
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_SLOTS_STATE_KEY, slots or [])
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_DATE_STATE_KEY, "2026-07-21")
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_SLOT_STATE_KEY, slot_data)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._SELECTED_BOOKING_STATE_KEY, {"service_name": "Стрижка", "master_name": "Максим", "datetime": OLD_DT, "date": "20.07.2026", "time": "10:00", "yclients_record_id": "old-1"})
    state.set_current_screen(USER_ID, CHAT_ID, current_screen or state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)


class FakeBookingService:
    dates: list[object] = []
    slots: list[object] = []
    calls: list[tuple[str, dict[str, object]]] = []
    error: Exception | None = None

    def __init__(self, repo):
        pass

    async def get_available_dates_for_selection(self, **kwargs):
        self.calls.append(("dates", kwargs))
        if self.error:
            raise self.error
        return self.dates

    async def get_available_slots(self, **kwargs):
        self.calls.append(("slots", kwargs))
        if self.error:
            raise self.error
        return self.slots


class FakeMyBookingsService:
    calls: list[tuple[str, dict[str, object]]] = []
    revalidate_error: Exception | None = None
    mutation_error: Exception | None = None
    result: dict[str, str] = {"old_record_id": "old-1", "new_record_id": "new-1", "new_datetime": NEW_DT}

    def __init__(self, repo):
        pass

    async def revalidate_reschedule_source(self, user, *, reschedule_context, platform_user_id=None):
        self.calls.append(("revalidate", dict(reschedule_context)))
        if self.revalidate_error:
            raise self.revalidate_error
        return dict(reschedule_context)

    async def reschedule_booking_for_user(self, user, *, reschedule_context, new_datetime_iso, platform_user_id=None):
        self.calls.append(("mutate", {"context": dict(reschedule_context), "new_datetime_iso": new_datetime_iso}))
        if self.mutation_error:
            raise self.mutation_error
        return dict(self.result)


@pytest.fixture(autouse=True)
def patch_runtime(monkeypatch):
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()
    FakeBookingService.dates = []
    FakeBookingService.slots = []
    FakeBookingService.calls = []
    FakeBookingService.error = None
    FakeMyBookingsService.calls = []
    FakeMyBookingsService.revalidate_error = None
    FakeMyBookingsService.mutation_error = None
    FakeMyBookingsService.result = {"old_record_id": "old-1", "new_record_id": "new-1", "new_datetime": NEW_DT}
    monkeypatch.setattr(flow, "BookingService", FakeBookingService)
    monkeypatch.setattr(flow, "MyBookingsService", FakeMyBookingsService)
    monkeypatch.setattr(flow, "_current_user", lambda ctx: object())
    monkeypatch.setattr(flow, "_booking_master_photo_attachment", lambda booking: None)
    yield
    state.clear_user_state(USER_ID, CHAT_ID)
    _locks.clear()


def test_eligibility_matrix_covers_allowed_forbidden_empty_unknown_past_and_grace():
    # 1-8
    for status in ["active", "confirmed", "approve", "approved", "pending", "new", "", "mystery", "booked"]:
        assert svc.is_booking_reschedulable(base_booking(status=status), timezone_name=TZ)
    for status in ["cancelled", "canceled", "done", "completed", "visit", "no_show"]:
        assert not svc.is_booking_reschedulable(base_booking(status=status), timezone_name=TZ)
    assert not svc.is_booking_reschedulable(base_booking(minutes=-10), timezone_name=TZ)
    assert svc.is_booking_reschedulable(base_booking(minutes=-3), timezone_name=TZ)


@pytest.mark.parametrize(
    "error",
    [
        None,
        svc.MyBookingReschedulePrepareError(svc.MY_BOOKING_RESCHEDULE_STALE_SOURCE_TEXT),
        svc.MyBookingReschedulePrepareError(svc.MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT),
        svc.MyBookingReschedulePrepareError(svc.MY_BOOKING_NOT_FOUND_TEXT),
    ],
)
def test_source_revalidation_runtime_valid_and_stale_paths(error):
    # 9-16, 70, 74, 80 via real confirm handler and fake service errors.
    FakeBookingService.slots = [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)]
    FakeMyBookingsService.revalidate_error = error
    seed_reschedule_state(slot_data={"new_datetime": NEW_DT, "new_time": "11:30", "new_date": "21.07.2026"})
    ctx = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))
    if error is None:
        assert [name for name, _ in FakeMyBookingsService.calls] == ["revalidate", "mutate"]
        assert "Запись перенесена ✅" in sent_texts(ctx)[-1]
    else:
        assert [name for name, _ in FakeMyBookingsService.calls] == ["revalidate"]
        assert any("🙏" in text for text in sent_texts(ctx))
        assert not any("old-1" in text or "client-1" in text or "traceback" in text or "Authorization" in text for text in sent_texts(ctx))


def test_dates_empty_and_stale_date_reload_preserves_context_and_handles_error():
    # 17-22
    FakeBookingService.dates = []
    seed_reschedule_state(dates=[], current_screen=state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    ctx = context("my_bookings:reschedule:date:99")
    asyncio.run(flow.handle_my_booking_reschedule_date(ctx))
    assert FakeBookingService.calls and FakeBookingService.calls[-1][0] == "dates"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_CONTEXT_STATE_KEY)["service_id"] == "svc-1"
    assert svc.MY_BOOKING_RESCHEDULE_STALE_DATE_TEXT in sent_texts(ctx)[0]
    assert svc.MY_BOOKING_RESCHEDULE_NO_DATES_TEXT in sent_texts(ctx)[-1]
    FakeBookingService.error = BookingServiceError("Ошибка дат 🙏")
    ctx2 = context("my_bookings:reschedule:date:99")
    asyncio.run(flow.handle_my_booking_reschedule_date(ctx2))
    assert "Ошибка дат" in sent_texts(ctx2)[-1]


def test_stale_slot_fresh_reload_replaces_old_state_dedups_sorts_and_handles_empty_and_error():
    # 23-33
    old_slot = BookingSlotItem(time="09:00", datetime_iso="2026-07-21 09:00:00")
    fresh_a = BookingSlotItem(time="12:00", datetime_iso="2026-07-21 12:00:00")
    fresh_b = BookingSlotItem(time="11:00", datetime_iso="2026-07-21 11:00:00")
    duplicate = BookingSlotItem(time="11:00", datetime_iso="2026-07-21 11:00:00")
    invalid = BookingSlotItem(time="", datetime_iso=None)
    FakeBookingService.slots = [fresh_a, duplicate, invalid, fresh_b]
    seed_reschedule_state(slots=[old_slot], current_screen=state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    ctx = context(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}9")
    asyncio.run(flow.handle_my_booking_reschedule_slot(ctx))
    stored = state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_SLOTS_STATE_KEY)
    assert FakeBookingService.calls[-1] == ("slots", {"yclients_service_id": "svc-1", "yclients_master_id": "staff-1", "booking_date": "2026-07-21"})
    assert [slot.time for slot in stored] == ["11:00", "12:00"]
    assert all(slot.time != "09:00" for slot in stored)
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_DATE_STATE_KEY) == "2026-07-21"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_SLOT_STATE_KEY) is None
    assert svc.MY_BOOKING_RESCHEDULE_STALE_SLOT_TEXT in sent_texts(ctx)[0]
    FakeBookingService.slots = []
    ctx_empty = context(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}9")
    asyncio.run(flow.handle_my_booking_reschedule_slot(ctx_empty))
    assert svc.MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT in sent_texts(ctx_empty)[-1]
    FakeBookingService.error = BookingServiceError("Ошибка слотов 🙏")
    ctx_error = context(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}9")
    asyncio.run(flow.handle_my_booking_reschedule_slot(ctx_error))
    assert "Ошибка слотов" in sent_texts(ctx_error)[-1]


def test_same_slot_blocked_before_confirmation_and_before_mutation_zero_calls():
    # 34-38
    same = BookingSlotItem(time="10:00", datetime_iso=OLD_DT)
    FakeBookingService.slots = [same]
    seed_reschedule_state(slots=[same], old_datetime=OLD_DT)
    ctx = context(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}0")
    asyncio.run(flow.handle_my_booking_reschedule_slot(ctx))
    assert svc.MY_BOOKING_RESCHEDULE_SAME_SLOT_TEXT in sent_texts(ctx)[0]
    assert not FakeMyBookingsService.calls
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN
    different = BookingSlotItem(time="11:30", datetime_iso=NEW_DT)
    seed_reschedule_state(slots=[different], old_datetime=OLD_DT)
    ctx2 = context(f"{MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX}0")
    asyncio.run(flow.handle_my_booking_reschedule_slot(ctx2))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN


def test_confirmation_text_buttons_and_back_to_slots_no_mutation():
    # 39-45, 96, 100
    keyboard = my_booking_reschedule_confirmation_keyboard()
    assert [row[0].payload for row in keyboard.rows] == [MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD, MY_BOOKINGS_BACK_PAYLOAD, NAV_HOME_PAYLOAD]
    text = svc.format_reschedule_confirmation_text({"service_name": "Стрижка", "staff_name": "Максим", "old_date": "20.07.2026", "old_time": "10:00", "new_date": "21.07.2026", "new_time": "11:30"})
    for part in ["Стрижка", "Максим", "20.07.2026", "10:00", "21.07.2026", "11:30"]:
        assert part in text
    seed_reschedule_state(slots=[BookingSlotItem(time="11:30", datetime_iso=NEW_DT)], slot_data={"new_datetime": NEW_DT, "new_time": "11:30"}, current_screen=state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    ctx = context(MY_BOOKINGS_BACK_PAYLOAD)
    asyncio.run(flow.handle_my_bookings_back(ctx))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN
    assert not FakeMyBookingsService.calls


def test_success_cleanup_repeated_callback_and_create_cancel_payload_order():
    # 46-63, 82-93
    FakeBookingService.slots = [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)]
    seed_reschedule_state(slot_data={"new_datetime": NEW_DT, "new_time": "11:30", "new_date": "21.07.2026"}, slots=FakeBookingService.slots, current_screen=state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    ctx = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))
    assert [name for name, _ in FakeMyBookingsService.calls] == ["revalidate", "mutate"]
    payload = FakeMyBookingsService.calls[-1][1]
    assert payload["new_datetime_iso"] == NEW_DT
    assert payload["context"]["service_id"] == "svc-1"
    assert payload["context"]["staff_id"] == "staff-1"
    assert payload["context"]["client_data"]["id"] == "client-1"
    assert payload["context"]["seance_length"] == 60
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY) == "old-1"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY) == "new-1"
    for key, expected in [
        (flow._RESCHEDULE_CONTEXT_STATE_KEY, None),
        (flow._RESCHEDULE_DATES_STATE_KEY, []),
        (flow._RESCHEDULE_SLOTS_STATE_KEY, []),
        (flow._RESCHEDULE_NEW_DATE_STATE_KEY, None),
        (flow._RESCHEDULE_NEW_SLOT_STATE_KEY, None),
        (flow._RESCHEDULE_IN_PROGRESS_STATE_KEY, False),
        (flow._BOOKINGS_STATE_KEY, []),
    ]:
        assert state.get_state_data_value(USER_ID, CHAT_ID, key) == expected
    assert "Запись перенесена ✅" in sent_texts(ctx)[-1]
    assert not _locks
    before = len(FakeMyBookingsService.calls)
    ctx_repeat = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx_repeat))
    assert len(FakeMyBookingsService.calls) == before
    assert "Запись перенесена ✅" in sent_texts(ctx_repeat)[-1]


def test_duplicate_in_progress_lock_and_release_on_failures_and_partial_failure_state():
    # 56-69
    FakeBookingService.slots = [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)]
    seed_reschedule_state(slot_data={"new_datetime": NEW_DT, "new_time": "11:30", "new_date": "21.07.2026"}, slots=FakeBookingService.slots, current_screen=state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_IN_PROGRESS_STATE_KEY, True)
    ctx_busy = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx_busy))
    assert svc.MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT in sent_texts(ctx_busy)[0]
    assert not FakeMyBookingsService.calls
    state.set_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_IN_PROGRESS_STATE_KEY, False)
    FakeMyBookingsService.mutation_error = svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT, diagnostic={"new_record_id": "new-partial"})
    ctx_partial = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx_partial))
    assert svc.MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT in sent_texts(ctx_partial)[-1]
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_COMPLETED_OLD_RECORD_STATE_KEY) == "old-1"
    assert state.get_state_data_value(USER_ID, CHAT_ID, flow._RESCHEDULE_NEW_RECORD_STATE_KEY) == "new-partial"
    assert not _locks
    before = len(FakeMyBookingsService.calls)
    ctx_again = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx_again))
    assert len(FakeMyBookingsService.calls) == before


@pytest.mark.parametrize(
    "exc,expected",
    [
        (svc.MyBookingsProfileMissingError(svc.MY_BOOKINGS_NO_PROFILE_TEXT), svc.MY_BOOKINGS_NO_PROFILE_TEXT),
        (svc.MyBookingRescheduleNotAllowedError(svc.MY_BOOKING_RESCHEDULE_NOT_ALLOWED_TEXT), svc.MY_BOOKING_RESCHEDULE_NOT_ALLOWED_TEXT),
        (svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_AUTH_ERROR_TEXT), svc.MY_BOOKING_RESCHEDULE_AUTH_ERROR_TEXT),
        (svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_CONFLICT_TEXT), svc.MY_BOOKING_RESCHEDULE_CONFLICT_TEXT),
        (svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT), svc.MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT),
        (svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_TEMPORARY_ERROR_TEXT), svc.MY_BOOKING_RESCHEDULE_TEMPORARY_ERROR_TEXT),
        (svc.MyBookingRescheduleError(svc.MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT), svc.MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT),
    ],
)
def test_handler_error_mapping_is_friendly_and_masked(exc, expected):
    # 70-81 handler entrypoint, no raw details exposed.
    FakeBookingService.slots = [BookingSlotItem(time="11:30", datetime_iso=NEW_DT)]
    FakeMyBookingsService.mutation_error = exc
    seed_reschedule_state(slot_data={"new_datetime": NEW_DT, "new_time": "11:30", "new_date": "21.07.2026"}, slots=FakeBookingService.slots, current_screen=state.MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN)
    ctx = context(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)
    asyncio.run(flow.handle_my_booking_reschedule_confirm(ctx))
    assert expected in sent_texts(ctx)[-1]
    joined = "\n".join(sent_texts(ctx))
    assert all(secret not in joined for secret in ["Authorization", "token", "endpoint", "traceback", "old-1", "client-1", "http://"])


@pytest.mark.parametrize(
    "yc_exc,expected_type,expected_text",
    [
        (YClientsValidationError("400", status_code=400), svc.MyBookingRescheduleNotAllowedError, svc.MY_BOOKING_RESCHEDULE_NOT_ALLOWED_TEXT),
        (YClientsAuthError("401", status_code=401), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_AUTH_ERROR_TEXT),
        (YClientsAuthError("403", status_code=403), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_AUTH_ERROR_TEXT),
        (YClientsNotFoundError("404", status_code=404), svc.MyBookingRescheduleNotAllowedError, svc.MY_BOOKING_RESCHEDULE_STALE_SOURCE_TEXT),
        (YClientsError("409", status_code=409), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_CONFLICT_TEXT),
        (YClientsRateLimitError("429", status_code=429), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT),
        (YClientsServerError("500", status_code=500), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_TEMPORARY_ERROR_TEXT),
        (YClientsTransportError("timeout"), svc.MyBookingRescheduleError, svc.MY_BOOKING_RESCHEDULE_TEMPORARY_ERROR_TEXT),
    ],
)
def test_service_yclients_error_mapping_entrypoint(monkeypatch, tmp_path, yc_exc, expected_type, expected_text):
    # 71-79 real service mutation entrypoint with faked integration layer.
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    class Layer:
        def __init__(self, client, company_id): pass
        async def create_booking(self, **kwargs): raise yc_exc
    monkeypatch.setattr(svc, "load_active_yclients_settings", lambda repo, operation: type("S", (), {"company_id": "cid", "partner_token": "p", "user_token": "u", "branch_timezone": TZ})())
    monkeypatch.setattr(svc, "has_required_yclients_credentials", lambda settings: True)
    monkeypatch.setattr(svc, "build_yclients_client_from_active_settings", lambda settings: Client())
    monkeypatch.setattr(svc, "YClientsServiceLayer", Layer)
    service = svc.MyBookingsService(type("Repo", (), {"database_path": str(tmp_path / "db.sqlite")})())
    with pytest.raises(expected_type) as raised:
        asyncio.run(service.reschedule_booking_for_user(type("U", (), {"yclients_client_id": "client-1", "phone": "+79991234567", "display_name": "Иван", "first_name": "Иван"})(), reschedule_context=reschedule_context(), new_datetime_iso=NEW_DT, platform_user_id=USER_ID))
    assert raised.value.user_message == expected_text


def test_navigation_dates_slots_home_and_scope_safety_no_aiogram():
    # 94-105
    seed_reschedule_state(slots=[BookingSlotItem(time="11:30", datetime_iso=NEW_DT)], current_screen=state.MY_BOOKING_RESCHEDULE_DATES_SCREEN)
    ctx_dates = context(MY_BOOKINGS_BACK_PAYLOAD)
    asyncio.run(flow.handle_my_bookings_back(ctx_dates))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_DETAILS_SCREEN
    seed_reschedule_state(slots=[BookingSlotItem(time="11:30", datetime_iso=NEW_DT)], current_screen=state.MY_BOOKING_RESCHEDULE_SLOTS_SCREEN)
    ctx_slots = context(MY_BOOKINGS_BACK_PAYLOAD)
    asyncio.run(flow.handle_my_bookings_back(ctx_slots))
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MY_BOOKING_RESCHEDULE_DATES_SCREEN
    assert flow.start_repeat_booking_with_prefill is not None
    source = __import__("pathlib").Path("max_barbershop_bot/flows/my_bookings.py").read_text()
    assert "from aiogram" not in source and "import aiogram" not in source
    assert "async def handle_my_booking_repeat_start" in source
    assert "async def handle_my_booking_cancel_start" in source
    assert "async def handle_my_booking_cancel_confirm" in source
