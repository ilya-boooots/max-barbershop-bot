from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows import booking
from max_barbershop_bot.services.booking import BookingSlotItem, format_date_button, format_slot_button
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_DATE_NEXT_PAYLOAD,
    BOOKING_DATE_PREV_PAYLOAD,
    BOOKING_SLOT_NEXT_PAYLOAD,
    BOOKING_SLOT_PREV_PAYLOAD,
    NAV_HOME_PAYLOAD,
)


@dataclass
class _Sender:
    messages: list[dict] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def _context(payload: str = "test") -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id="u-page",
            max_user_id="u-page",
            chat_id="100",
            text=None,
            callback_payload=payload,
            callback_id="cb-page",
        ),
        sender=_Sender(),
    )


def _rows(context: RouterContext):
    return context.sender.messages[-1]["keyboard"].rows


def _payloads(context: RouterContext) -> list[str]:
    return [button.payload for row in _rows(context) for button in row]


def _texts(context: RouterContext) -> list[str]:
    return [button.text for row in _rows(context) for button in row]


def _dates(count: int) -> list[date]:
    first = date(2026, 7, 4)
    return [first + timedelta(days=index) for index in range(count)]


def _slots(count: int) -> list[BookingSlotItem]:
    return [BookingSlotItem(time=f"{10 + index // 4:02d}:{(index % 4) * 15:02d}") for index in range(count)]


def setup_function() -> None:
    state.clear_user_state("u-page", "100")


def test_date_list_with_more_than_page_size_shows_next_button() -> None:
    context = _context()
    asyncio.run(booking._show_dates(context, _dates(11), timezone_name="Europe/Moscow"))

    assert BOOKING_DATE_NEXT_PAYLOAD in _payloads(context)
    assert BOOKING_DATE_PREV_PAYLOAD not in _payloads(context)
    assert _texts(context)[:10] == [format_date_button(item, timezone_name="Europe/Moscow") for item in _dates(10)]


def test_date_next_page_shows_next_set_of_dates() -> None:
    context = _context()
    asyncio.run(booking._show_dates(context, _dates(12), timezone_name="Europe/Moscow", page=1))

    assert BOOKING_DATE_PREV_PAYLOAD in _payloads(context)
    assert BOOKING_DATE_NEXT_PAYLOAD not in _payloads(context)
    assert _texts(context)[:2] == [format_date_button(item, timezone_name="Europe/Moscow") for item in _dates(12)[10:12]]


def test_date_prev_returns_previous_page() -> None:
    context = _context(BOOKING_DATE_PREV_PAYLOAD)
    state.set_current_screen("u-page", "100", state.BOOKING_DATES_SCREEN)
    state.set_state_data_value("u-page", "100", booking._DATES_STATE_KEY, _dates(12))
    state.set_state_data_value("u-page", "100", booking._DATE_PAGE_STATE_KEY, 1)

    asyncio.run(booking.handle_booking_date_page(context))

    assert BOOKING_DATE_NEXT_PAYLOAD in _payloads(context)
    assert BOOKING_DATE_PREV_PAYLOAD not in _payloads(context)
    assert _texts(context)[:10] == [format_date_button(item, timezone_name="Europe/Moscow") for item in _dates(10)]


def test_slot_list_with_more_than_page_size_shows_next_button() -> None:
    context = _context()
    state.set_state_data_value("u-page", "100", booking._SELECTED_DATE_STATE_KEY, "2026-07-04")
    asyncio.run(booking._show_slots(context, _slots(16)))

    assert BOOKING_SLOT_NEXT_PAYLOAD in _payloads(context)
    assert BOOKING_SLOT_PREV_PAYLOAD not in _payloads(context)
    assert _texts(context)[:15] == [format_slot_button(item) for item in _slots(15)]


def test_slot_next_page_shows_next_set_of_slots() -> None:
    context = _context()
    state.set_state_data_value("u-page", "100", booking._SELECTED_DATE_STATE_KEY, "2026-07-04")
    asyncio.run(booking._show_slots(context, _slots(16), page=1))

    assert BOOKING_SLOT_PREV_PAYLOAD in _payloads(context)
    assert BOOKING_SLOT_NEXT_PAYLOAD not in _payloads(context)
    assert _texts(context)[0] == format_slot_button(_slots(16)[15])


def test_slot_prev_returns_previous_page() -> None:
    context = _context(BOOKING_SLOT_PREV_PAYLOAD)
    state.set_current_screen("u-page", "100", state.BOOKING_SLOTS_SCREEN)
    state.set_state_data_value("u-page", "100", booking._SLOTS_STATE_KEY, _slots(16))
    state.set_state_data_value("u-page", "100", booking._SELECTED_DATE_STATE_KEY, "2026-07-04")
    state.set_state_data_value("u-page", "100", booking._SLOT_PAGE_STATE_KEY, 1)

    asyncio.run(booking.handle_booking_slot_page(context))

    assert BOOKING_SLOT_NEXT_PAYLOAD in _payloads(context)
    assert BOOKING_SLOT_PREV_PAYLOAD not in _payloads(context)
    assert _texts(context)[:15] == [format_slot_button(item) for item in _slots(15)]


def test_stale_page_callback_does_not_crash(monkeypatch) -> None:
    context = _context(BOOKING_DATE_NEXT_PAYLOAD)
    state.set_current_screen("u-page", "100", state.BOOKING_DATES_SCREEN)
    called = {"dates": False}

    async def fake_show_booking_dates(ctx, *, push_current=True):
        called["dates"] = True

    monkeypatch.setattr(booking, "_show_booking_dates", fake_show_booking_dates)

    asyncio.run(booking.handle_booking_date_page(context))

    assert called["dates"] is True
    assert "Список дат уже обновился" in context.sender.messages[0]["text"]


def test_back_home_payloads_remain_on_paginated_date_and_slot_screens() -> None:
    date_context = _context()
    asyncio.run(booking._show_dates(date_context, _dates(12), timezone_name="Europe/Moscow", page=1))
    assert BOOKING_BACK_PAYLOAD in _payloads(date_context)
    assert NAV_HOME_PAYLOAD in _payloads(date_context)

    slot_context = _context()
    state.set_state_data_value("u-page", "100", booking._SELECTED_DATE_STATE_KEY, "2026-07-04")
    asyncio.run(booking._show_slots(slot_context, _slots(16), page=1))
    assert BOOKING_BACK_PAYLOAD in _payloads(slot_context)
    assert NAV_HOME_PAYLOAD in _payloads(slot_context)


def test_pagination_callbacks_are_registered() -> None:
    router = Router()
    booking.register_booking_routes(router)

    assert router._callback_handlers[BOOKING_DATE_PREV_PAYLOAD] is booking.handle_booking_date_page
    assert router._callback_handlers[BOOKING_DATE_NEXT_PAYLOAD] is booking.handle_booking_date_page
    assert router._callback_handlers[BOOKING_SLOT_PREV_PAYLOAD] is booking.handle_booking_slot_page
    assert router._callback_handlers[BOOKING_SLOT_NEXT_PAYLOAD] is booking.handle_booking_slot_page
