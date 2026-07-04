from __future__ import annotations

import asyncio
from dataclasses import dataclass

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import _locks, acquire_action_lock
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import booking
from max_barbershop_bot.services.booking import BookingServiceError, CreatedBooking
from max_barbershop_bot.ui.buttons import BOOKING_CONFIRM_PAYLOAD


@dataclass
class _User:
    platform_user_id: str = "u1"
    max_user_id: str = "u1"
    chat_id: str = "100"
    yclients_client_id: str | None = "client1"
    notifications_enabled: bool = False
    first_name: str | None = "Иван"
    last_name: str | None = "Иванов"
    display_name: str | None = "Иван Иванов"


class _Sender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id, text, keyboard=None, attachments=None):
        self.texts.append(text)

    async def send_to_user(self, user_id, text, keyboard=None, attachments=None):
        self.texts.append(text)

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


class _FakeBookingService:
    create_calls = 0
    fail = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_branch_timezone(self) -> str:
        return "Europe/Moscow"

    async def revalidate_selected_slot(self, **kwargs) -> bool:
        return True

    async def create_booking(self, **kwargs) -> CreatedBooking:
        type(self).create_calls += 1
        if type(self).fail:
            raise BookingServiceError("create failed")
        return CreatedBooking(yclients_record_id="rec-1", yclients_client_id="client1", datetime_iso="2026-07-05T10:00:00+03:00")


def _context(sender: _Sender | None = None) -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id="u1",
            max_user_id="u1",
            chat_id="100",
            text=None,
            callback_payload=BOOKING_CONFIRM_PAYLOAD,
            callback_id="cb1",
        ),
        sender=sender or _Sender(),
    )


def _prepare(monkeypatch):
    _locks.clear()
    state.clear_state_data("u1", "100")
    state.set_current_screen("u1", "100", state.BOOKING_CONFIRMATION_SCREEN)
    for key, value in {
        booking._SELECTED_SERVICE_STATE_KEY: "svc1",
        booking._SELECTED_SERVICE_NAME_STATE_KEY: "Стрижка",
        booking._SELECTED_MASTER_STATE_KEY: "m1",
        booking._SELECTED_MASTER_NAME_STATE_KEY: "Мастер",
        booking._SELECTED_DATE_STATE_KEY: "2026-07-05",
        booking._SELECTED_SLOT_TIME_STATE_KEY: "10:00",
        booking._SELECTED_SLOT_DATETIME_STATE_KEY: "2026-07-05T10:00:00+03:00",
        booking._BOOKING_PHONE_STATE_KEY: "+79991234567",
    }.items():
        state.set_state_data_value("u1", "100", key, value)
    _FakeBookingService.create_calls = 0
    _FakeBookingService.fail = False
    monkeypatch.setattr(booking, "BookingService", _FakeBookingService)
    monkeypatch.setattr(booking, "_current_user", lambda context: _User())
    monkeypatch.setattr(booking, "_save_attribution_safely", lambda **kwargs: None)

    async def _success(context, *, created, user, booking_data):
        state.set_current_screen("u1", "100", state.BOOKING_SUCCESS_SCREEN)
        await context.send_text("✅ Готово! Вы записаны 💈")

    monkeypatch.setattr(booking, "_send_immediate_confirmation_safely", _success)


def test_booking_confirm_double_tap_creates_yclients_booking_once(monkeypatch):
    _prepare(monkeypatch)
    sender = _Sender()
    context = _context(sender)

    asyncio.run(booking.handle_booking_confirm(context))
    state.set_current_screen("u1", "100", state.BOOKING_CONFIRMATION_SCREEN)
    asyncio.run(booking.handle_booking_confirm(context))

    assert _FakeBookingService.create_calls == 1
    assert state.get_state_data_value("u1", "100", booking._BOOKING_COMPLETED_RECORD_ID_STATE_KEY) == "rec-1"


def test_booking_confirm_active_lock_gets_friendly_response(monkeypatch):
    _prepare(monkeypatch)
    sender = _Sender()
    context = _context(sender)
    assert acquire_action_lock(booking._confirm_lock_key(context), ttl_seconds=30)

    asyncio.run(booking.handle_booking_confirm(context))

    assert _FakeBookingService.create_calls == 0
    assert "⏳ Уже создаём запись, секундочку 🙂" in sender.texts


def test_booking_confirm_create_failure_releases_lock_and_allows_retry(monkeypatch):
    _prepare(monkeypatch)
    sender = _Sender()
    context = _context(sender)
    _FakeBookingService.fail = True

    asyncio.run(booking.handle_booking_confirm(context))
    _FakeBookingService.fail = False
    asyncio.run(booking.handle_booking_confirm(context))

    assert _FakeBookingService.create_calls == 2
    assert state.get_state_data_value("u1", "100", booking._BOOKING_COMPLETED_RECORD_ID_STATE_KEY) == "rec-1"


def test_booking_confirm_already_created_stale_callback_does_not_create(monkeypatch):
    _prepare(monkeypatch)
    state.set_state_data_value("u1", "100", booking._BOOKING_COMPLETED_RECORD_ID_STATE_KEY, "rec-existing")
    sender = _Sender()

    asyncio.run(booking.handle_booking_confirm(_context(sender)))

    assert _FakeBookingService.create_calls == 0
    assert sender.texts == []
