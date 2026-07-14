from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows import booking
from max_barbershop_bot.services.booking import BookingCatalog, BookingServiceItem
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_CATEGORY_PAYLOAD_PREFIX,
    BOOKING_HUB_DATETIME_PAYLOAD,
    BOOKING_HUB_SERVICE_PAYLOAD,
    BOOKING_HUB_STAFF_PAYLOAD,
    BOOKING_SERVICE_PAYLOAD_PREFIX,
    MENU_BOOKING_PAYLOAD,
    NAV_HOME_PAYLOAD,
    booking_hub_keyboard,
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


def _context(payload: str = MENU_BOOKING_PAYLOAD, *, user: str = "35", chat: str = "35") -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id=user,
            max_user_id=user,
            chat_id=chat,
            text=None,
            callback_payload=payload,
            callback_id="cb35",
        ),
        sender=_Sender(),
    )


def _payloads(keyboard) -> list[str]:
    return [button.payload for row in keyboard.rows for button in row]


def _texts(keyboard) -> list[str]:
    return [button.text for row in keyboard.rows for button in row]


def setup_function() -> None:
    state.clear_user_state("35", "35")


def test_plan_and_telegram_reference_prove_pr_035_scope() -> None:
    plan = Path("docs/max_telegram_parity_plan_v2.md").read_text(encoding="utf-8")
    flow = Path("telegram_reference/app/handlers/booking_flow.py").read_text(encoding="utf-8")
    keyboard = Path("telegram_reference/app/keyboards/booking.py").read_text(encoding="utf-8")

    assert "PR-035 — Booking hub/services/dates parity audit" in plan
    assert "final confirm/create; covered by PR-006" in plan
    for token in ("CB_HUB_SERVICE", "CB_HUB_STAFF", "CB_HUB_DATETIME"):
        assert token in flow
    for token in (
        "_load_categories",
        "_load_services",
        "_show_categories",
        "_show_services",
        "_load_staff",
        "_show_staff",
        "_load_available_dates",
        "_load_slots",
        "_show_date_picker",
        "_show_slots",
        "_slot_is_future_for_company_day",
    ):
        assert token in flow
    assert "Назад" in flow or "Назад" in keyboard
    assert "CB_HOME" in flow and ("HOME" in flow or "HOME" in keyboard)


def test_menu_booking_payload_routes_to_real_hub_start_and_hub_buttons_match_telegram_order() -> None:
    router = Router()
    booking.register_booking_routes(router)

    assert router._callback_handlers[MENU_BOOKING_PAYLOAD] is booking.handle_booking_start

    keyboard = booking_hub_keyboard(back_payload=BOOKING_BACK_PAYLOAD)
    assert _texts(keyboard)[:3] == [
        "👨‍🔧 Выбрать специалиста",
        "📅 Выбрать дату и время",
        "🧾 Выбрать услуги",
    ]
    assert _payloads(keyboard)[:3] == [
        BOOKING_HUB_STAFF_PAYLOAD,
        BOOKING_HUB_DATETIME_PAYLOAD,
        BOOKING_HUB_SERVICE_PAYLOAD,
    ]
    assert BOOKING_BACK_PAYLOAD in _payloads(keyboard)
    assert NAV_HOME_PAYLOAD in _payloads(keyboard)
    assert "placeholder" not in _payloads(keyboard)


def test_fresh_normal_booking_clears_booking_selection_without_erasing_unrelated_state(monkeypatch) -> None:
    context = _context()
    for key, value in {
        booking._SELECTED_CATEGORY_STATE_KEY: "old-cat",
        booking._SELECTED_SERVICE_STATE_KEY: "old-service",
        booking._SELECTED_MASTER_STATE_KEY: "old-master",
        booking._SELECTED_DATE_STATE_KEY: "2026-07-14",
        booking._SELECTED_SLOT_TIME_STATE_KEY: "10:00",
        "booking_source": "old-campaign",
        "unrelated_state": "keep-me",
    }.items():
        state.set_state_data_value("35", "35", key, value)

    async def fake_hub(ctx):
        state.set_current_screen("35", "35", state.BOOKING_HUB_SCREEN)

    monkeypatch.setattr(booking, "_show_booking_hub", fake_hub)

    asyncio.run(booking.handle_booking_start(context))

    assert state.get_state_data_value("35", "35", booking._SELECTED_CATEGORY_STATE_KEY) is None
    assert state.get_state_data_value("35", "35", booking._SELECTED_SERVICE_STATE_KEY) is None
    assert state.get_state_data_value("35", "35", booking._SELECTED_MASTER_STATE_KEY) is None
    assert state.get_state_data_value("35", "35", booking._SELECTED_DATE_STATE_KEY) is None
    assert state.get_state_data_value("35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY) is None
    assert state.get_state_data_value("35", "35", "booking_source") is None
    assert state.get_state_data_value("35", "35", "unrelated_state") == "keep-me"


def test_stale_category_callback_is_friendly_and_refreshes_safe_screen(monkeypatch) -> None:
    context = _context(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}9")
    state.set_current_screen("35", "35", state.BOOKING_CATEGORIES_SCREEN)
    called = {"refresh": False}

    async def fake_open_catalog(ctx, *, push_current=True):
        called["refresh"] = True

    monkeypatch.setattr(booking, "_open_booking_catalog", fake_open_catalog)

    asyncio.run(booking.handle_booking_category(context))

    assert called["refresh"] is True
    assert "Список категорий уже обновился" in context.sender.messages[0]["text"]


def test_stale_service_callback_is_friendly_and_refreshes_safe_screen(monkeypatch) -> None:
    context = _context(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}9")
    state.set_current_screen("35", "35", state.BOOKING_SERVICES_SCREEN)
    called = {"refresh": False}

    async def fake_open_catalog(ctx, *, push_current=True):
        called["refresh"] = True

    monkeypatch.setattr(booking, "_open_booking_catalog", fake_open_catalog)

    asyncio.run(booking.handle_booking_service(context))

    assert called["refresh"] is True
    assert "Список услуг уже обновился" in context.sender.messages[0]["text"]


def test_staff_first_selected_master_filters_incompatible_services(monkeypatch) -> None:
    compatible = BookingServiceItem(yclients_service_id="s1", title="Стрижка", yclients_category_id="c1", category_title="Услуги", raw={"staff_ids": ["m1"]})
    incompatible = BookingServiceItem(yclients_service_id="s2", title="Борода", yclients_category_id="c1", category_title="Услуги", raw={"staff_ids": ["m2"]})
    context = _context()
    state.set_state_data_value("35", "35", booking._ENTRY_MODE_STATE_KEY, booking._ENTRY_MODE_STAFF_FIRST)
    state.set_state_data_value("35", "35", booking._SELECTED_MASTER_STATE_KEY, "m1")
    state.set_state_data_value("35", "35", booking._CATALOG_STATE_KEY, BookingCatalog(categories=[], services=[compatible, incompatible]))

    async def fake_valid(self, **kwargs):
        assert kwargs["selected_master_id"] == "m1"
        return [compatible]

    shown = {}

    async def fake_show_services(ctx, services, *, category_title, page=0, push_current=True):
        shown["ids"] = [item.yclients_service_id for item in services]

    monkeypatch.setattr(booking.BookingService, "get_valid_services_for_constraints", fake_valid)
    monkeypatch.setattr(booking, "_show_services", fake_show_services)

    asyncio.run(booking._show_selected_category_services(context))

    assert shown["ids"] == ["s1"]


def test_scope_safety_no_aiogram_and_final_create_handlers_still_present() -> None:
    max_sources = "\n".join(path.read_text(encoding="utf-8") for path in Path("max_barbershop_bot").rglob("*.py"))
    booking_source = Path("max_barbershop_bot/flows/booking.py").read_text(encoding="utf-8")

    assert "from aiogram" not in max_sources
    assert "import aiogram" not in max_sources
    for token in (
        "async def handle_booking_confirm",
        "async def handle_booking_phone_input",
        "async def handle_booking_phone_use_registered",
        "acquire_action_lock",
        "create_booking(",
        "MAX_BOOKING_COMMENT_MARKER",
        "PlatformAttributionRepository",
        "send_immediate_confirmation",
    ):
        assert token in booking_source
