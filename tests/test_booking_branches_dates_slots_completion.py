from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import booking
from max_barbershop_bot.services import booking as booking_service_module
from max_barbershop_bot.services.booking import (
    BOOKING_DATES_EMPTY_TEXT,
    BookingCatalog,
    BookingCategory,
    BookingMasterItem,
    BookingServiceError,
    BookingServiceItem,
    BookingSlotItem,
    filter_available_slots,
    format_date_button,
    format_slot_button,
)
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BOOKING_CATEGORY_NEXT_PAYLOAD,
    BOOKING_CATEGORY_PAYLOAD_PREFIX,
    BOOKING_DATE_PAYLOAD_PREFIX,
    BOOKING_HUB_DATETIME_PAYLOAD,
    BOOKING_HUB_SERVICE_PAYLOAD,
    BOOKING_HUB_STAFF_PAYLOAD,
    BOOKING_MASTER_NEXT_PAYLOAD,
    BOOKING_MASTER_ANY_PAYLOAD,
    BOOKING_MASTER_PAYLOAD_PREFIX,
    BOOKING_SERVICE_PAYLOAD_PREFIX,
    BOOKING_SLOT_PAYLOAD_PREFIX,
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


def _context(payload: str, *, user: str = "u35", chat: str = "35") -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id=user,
            max_user_id=user,
            chat_id=chat,
            text=None,
            callback_payload=payload,
            callback_id=f"cb-{payload}",
        ),
        sender=_Sender(),
    )


def _payloads(ctx: RouterContext) -> list[str]:
    keyboard = ctx.sender.messages[-1]["keyboard"]
    return [button.payload for row in keyboard.rows for button in row]


def _texts(ctx: RouterContext) -> list[str]:
    keyboard = ctx.sender.messages[-1]["keyboard"]
    return [button.text for row in keyboard.rows for button in row]


def _cat(cid="c1", title="Стрижки"):
    return BookingCategory(yclients_category_id=cid, title=title)


def _service(sid="s1", cid="c1", title="Мужская стрижка", staff_ids=("m1",)):
    return BookingServiceItem(
        yclients_service_id=sid,
        title=title,
        yclients_category_id=cid,
        category_title="Стрижки",
        duration="60 мин",
        raw={"staff_ids": list(staff_ids), "bookable": True, "active": True},
    )


def _master(mid="m1", title="Иван"):
    return BookingMasterItem(yclients_master_id=mid, title=title, specialization="Барбер", rating="5.0", raw={"bookable": True, "active": True})


def _slot(time="10:00"):
    return BookingSlotItem(time=time, datetime_iso=f"2026-07-15T{time}:00+04:00")


def setup_function() -> None:
    state.clear_user_state("u35", "35")


@pytest.mark.parametrize("payload,expected_screen", [
    (BOOKING_HUB_SERVICE_PAYLOAD, state.BOOKING_CATEGORIES_SCREEN),
    (BOOKING_HUB_STAFF_PAYLOAD, state.BOOKING_MASTERS_SCREEN),
    (BOOKING_HUB_DATETIME_PAYLOAD, state.BOOKING_DATES_SCREEN),
])
def test_three_booking_branches_open_telegram_ordered_first_screen(monkeypatch, payload, expected_screen) -> None:
    catalog = BookingCatalog(categories=[_cat()], services=[_service()])

    async def fake_catalog(self, **kwargs):
        return catalog

    async def fake_masters(self, **kwargs):
        return [_master()]

    async def fake_dates(self, **kwargs):
        return [datetime(2026, 7, 15).date()]

    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fake_catalog)
    monkeypatch.setattr(booking.BookingService, "get_valid_masters_for_constraints", fake_masters)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_available_dates", fake_dates)
    monkeypatch.setattr(booking.BookingService, "get_branch_timezone", lambda self: "Europe/Samara")

    ctx = _context(payload)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    handler = {
        BOOKING_HUB_SERVICE_PAYLOAD: booking.handle_booking_hub_service,
        BOOKING_HUB_STAFF_PAYLOAD: booking.handle_booking_hub_staff,
        BOOKING_HUB_DATETIME_PAYLOAD: booking.handle_booking_hub_datetime,
    }[payload]

    asyncio.run(handler(ctx))

    assert state.get_current_screen("u35", "35") == expected_screen
    assert BOOKING_BACK_PAYLOAD in _payloads(ctx)
    assert NAV_HOME_PAYLOAD in _payloads(ctx)


def test_service_first_full_selection_persists_service_filters_staff_dates_slots_and_back(monkeypatch) -> None:
    compatible = _master("m1", "Иван")
    unavailable = _master("m2", "Пётр")
    catalog = BookingCatalog(categories=[_cat()], services=[_service("s1", staff_ids=("m1",))])

    async def fake_catalog(self, **kwargs):
        assert kwargs["entry_mode"] == booking._ENTRY_MODE_SERVICE_FIRST
        return catalog

    async def fake_masters(self, **kwargs):
        assert kwargs["yclients_service_id"] == "s1"
        return [compatible]

    async def fake_dates(self, **kwargs):
        assert kwargs == {"yclients_service_id": "s1", "yclients_master_id": "m1", "days": booking.DATE_LOOKAHEAD_DAYS}
        return [datetime(2026, 7, 15).date()]

    async def fake_slots(self, **kwargs):
        assert kwargs["yclients_master_id"] == "m1"
        return [_slot("11:00")]

    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fake_catalog)
    monkeypatch.setattr(booking.BookingService, "get_valid_masters_for_constraints", fake_masters)
    monkeypatch.setattr(booking.BookingService, "get_available_dates_for_selection", fake_dates)
    monkeypatch.setattr(booking.BookingService, "get_available_slots", fake_slots)
    monkeypatch.setattr(booking.BookingService, "get_branch_timezone", lambda self: "Europe/Samara")

    ctx = _context(BOOKING_HUB_SERVICE_PAYLOAD)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    asyncio.run(booking.handle_booking_hub_service(ctx))
    asyncio.run(booking.handle_booking_category(_context(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}0")))
    asyncio.run(booking.handle_booking_service(_context(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}0")))

    assert state.get_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY) == "s1"
    assert state.get_state_data_value("u35", "35", booking._MASTERS_STATE_KEY) == [compatible]
    assert unavailable not in state.get_state_data_value("u35", "35", booking._MASTERS_STATE_KEY)

    asyncio.run(booking.handle_booking_master(_context(f"{BOOKING_MASTER_PAYLOAD_PREFIX}0")))
    date_ctx = _context(f"{BOOKING_DATE_PAYLOAD_PREFIX}0")
    asyncio.run(booking.handle_booking_date(date_ctx))
    assert state.get_current_screen("u35", "35") == state.BOOKING_SLOTS_SCREEN
    assert "11:00" in _texts(date_ctx)

    back = _context(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking.handle_booking_back(back))
    assert state.get_current_screen("u35", "35") == state.BOOKING_DATES_SCREEN


def test_staff_first_master_context_filters_services_and_back_returns_to_masters(monkeypatch) -> None:
    compatible = _service("s1", staff_ids=("m1",))
    incompatible = _service("s2", title="Борода", staff_ids=("m2",))
    catalog = BookingCatalog(categories=[_cat()], services=[compatible])

    async def fake_masters(self, **kwargs):
        return [_master("m1")]

    async def fake_catalog(self, **kwargs):
        assert kwargs["entry_mode"] == booking._ENTRY_MODE_STAFF_FIRST
        assert kwargs["selected_master_id"] == "m1"
        return catalog

    monkeypatch.setattr(booking.BookingService, "get_valid_masters_for_constraints", fake_masters)
    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fake_catalog)

    ctx = _context(BOOKING_HUB_STAFF_PAYLOAD)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    asyncio.run(booking.handle_booking_hub_staff(ctx))
    asyncio.run(booking.handle_booking_master(_context(f"{BOOKING_MASTER_PAYLOAD_PREFIX}0")))

    assert state.get_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY) == "m1"
    assert state.get_state_data_value("u35", "35", booking._CATALOG_STATE_KEY).services == [compatible]
    assert incompatible not in state.get_state_data_value("u35", "35", booking._CATALOG_STATE_KEY).services

    asyncio.run(booking.handle_booking_category(_context(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}0")))
    assert state.get_current_screen("u35", "35") == state.BOOKING_SERVICES_SCREEN
    back = _context(BOOKING_BACK_PAYLOAD)
    asyncio.run(booking.handle_booking_back(back))
    assert state.get_current_screen("u35", "35") == state.BOOKING_MASTERS_SCREEN


def test_datetime_first_keeps_preferred_date_time_then_service_master(monkeypatch) -> None:
    catalog = BookingCatalog(categories=[], services=[_service("s1", staff_ids=("m1",))])

    async def fake_dates(self, **kwargs):
        return [datetime(2026, 7, 15).date()]

    async def fake_dt_slots(self, booking_date, **kwargs):
        return [_slot("12:30")]

    async def fake_services_for_slot(self, **kwargs):
        assert kwargs["booking_date"] == "2026-07-15"
        assert kwargs["booking_time"] == "12:30"
        return catalog

    async def fake_masters_for_slot(self, **kwargs):
        assert kwargs["booking_date"] == "2026-07-15"
        assert kwargs["booking_time"] == "12:30"
        assert kwargs["yclients_service_id"] == "s1"
        return [_master("m1")]

    monkeypatch.setattr(booking.BookingService, "get_datetime_first_available_dates", fake_dates)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_slots_for_date", fake_dt_slots)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_services_for_slot", fake_services_for_slot)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_masters_for_slot", fake_masters_for_slot)
    monkeypatch.setattr(booking.BookingService, "get_branch_timezone", lambda self: "Europe/Samara")

    ctx = _context(BOOKING_HUB_DATETIME_PAYLOAD)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    asyncio.run(booking.handle_booking_hub_datetime(ctx))
    asyncio.run(booking.handle_booking_date(_context(f"{BOOKING_DATE_PAYLOAD_PREFIX}0")))
    asyncio.run(booking.handle_booking_slot(_context(f"{BOOKING_SLOT_PAYLOAD_PREFIX}0")))
    asyncio.run(booking.handle_booking_service(_context(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}0")))

    assert state.get_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY) == booking._ENTRY_MODE_DATETIME_FIRST
    assert state.get_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY) == "2026-07-15"
    assert state.get_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY) == "12:30"
    assert state.get_current_screen("u35", "35") == state.BOOKING_MASTERS_SCREEN


def test_empty_states_and_pagination_for_all_selection_screens() -> None:
    ctx = _context("empty")
    asyncio.run(booking._show_categories(ctx, []))
    assert "нет доступных категорий" in ctx.sender.messages[-1]["text"]
    asyncio.run(booking._show_services(ctx, [], category_title=None))
    assert "нет доступных" in ctx.sender.messages[-1]["text"]
    asyncio.run(booking._show_masters(ctx, []))
    assert ctx.sender.messages[-1]["text"] == booking.BOOKING_MASTERS_EMPTY_TEXT
    asyncio.run(booking._show_dates(ctx, [], timezone_name="Europe/Samara"))
    assert ctx.sender.messages[-1]["text"] == BOOKING_DATES_EMPTY_TEXT
    asyncio.run(booking._show_slots(ctx, []))
    assert ctx.sender.messages[-1]["text"] == booking.BOOKING_SLOTS_EMPTY_TEXT

    cats = [_cat(f"c{i}", f"Категория {i}") for i in range(10)]
    asyncio.run(booking._show_categories(ctx, cats))
    assert BOOKING_CATEGORY_NEXT_PAYLOAD in _payloads(ctx)
    masters = [_master(f"m{i}", f"Мастер {i}") for i in range(10)]
    asyncio.run(booking._show_masters(ctx, masters))
    assert BOOKING_MASTER_NEXT_PAYLOAD in _payloads(ctx)


def test_slot_normalization_dedup_order_and_branch_timezone_fixed_now(monkeypatch) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 15, 0, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr(booking_service_module, "datetime", FixedDatetime)
    slots = filter_available_slots(
        [
            {"time": "2026-07-14T23:00:00+04:00", "available": True},
            {"time": "2026-07-15T04:45:00+04:00", "available": True},
            {"time": "bad", "available": True},
        ],
        booking_date="2026-07-15",
        timezone_name="Europe/Samara",
    )
    assert [slot.time for slot in slots] == ["04:45"]


def test_stale_callbacks_are_friendly_and_preserve_branch_context(monkeypatch) -> None:
    async def fake_catalog(ctx, *, push_current=True):
        assert state.get_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY) == booking._ENTRY_MODE_STAFF_FIRST
        assert state.get_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY) == "m1"

    async def fake_masters(ctx, *args, **kwargs):
        assert state.get_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY) == "s1"

    monkeypatch.setattr(booking, "_open_booking_catalog", fake_catalog)
    monkeypatch.setattr(booking, "_open_booking_masters", fake_masters)
    state.set_current_screen("u35", "35", state.BOOKING_CATEGORIES_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, booking._ENTRY_MODE_STAFF_FIRST)
    state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, "m1")
    stale_category = _context(f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_category(stale_category))
    assert "Список категорий уже обновился" in stale_category.sender.messages[0]["text"]

    state.set_current_screen("u35", "35", state.BOOKING_MASTERS_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, booking._ENTRY_MODE_SERVICE_FIRST)
    state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, "s1")
    stale_master = _context(f"{BOOKING_MASTER_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_master(stale_master))
    assert "Список мастеров уже обновился" in stale_master.sender.messages[0]["text"]


def test_selection_screen_error_mapping_is_friendly_and_masked(monkeypatch) -> None:
    secret = "Authorization: Bearer partner_token user_token raw_response traceback"
    exc = BookingServiceError(
        "Сервис записи временно недоступен 🙏\n\nПопробуйте позже.",
        diagnostic={"error_category": "server", "http_status": 500, "safe_response_snippet": "masked"},
    )

    async def fail_catalog(self, **kwargs):
        raise exc

    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fail_catalog)
    ctx = _context(BOOKING_HUB_SERVICE_PAYLOAD)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    asyncio.run(booking.handle_booking_hub_service(ctx))

    text = ctx.sender.messages[-1]["text"]
    assert "временно недоступен" in text
    for token in secret.split():
        assert token not in text
    assert BOOKING_BACK_PAYLOAD in _payloads(ctx)
    assert NAV_HOME_PAYLOAD in _payloads(ctx)


def test_scope_safety_forbidden_handlers_and_reference_files_untouched() -> None:
    source = Path("max_barbershop_bot/flows/booking.py").read_text(encoding="utf-8")
    max_sources = "\n".join(path.read_text(encoding="utf-8") for path in Path("max_barbershop_bot").rglob("*.py"))
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
        assert token in source
    assert "from aiogram" not in max_sources
    assert "import aiogram" not in max_sources


@pytest.mark.parametrize("entry_mode,screen,payload,expected_method", [
    (booking._ENTRY_MODE_SERVICE_FIRST, state.BOOKING_CATEGORIES_SCREEN, f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}9", "catalog"),
    (booking._ENTRY_MODE_STAFF_FIRST, state.BOOKING_CATEGORIES_SCREEN, f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}9", "catalog"),
    (booking._ENTRY_MODE_DATETIME_FIRST, state.BOOKING_CATEGORIES_SCREEN, f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}9", "dt_catalog"),
])
def test_stale_category_refresh_is_branch_aware(monkeypatch, entry_mode, screen, payload, expected_method) -> None:
    # Telegram behavior: stale category keeps branch context and refreshes the logical category/service screen.
    calls = []
    catalog = BookingCatalog(categories=[_cat()], services=[_service()])

    async def fake_catalog(self, **kwargs):
        calls.append(("catalog", kwargs))
        return catalog

    async def fake_dt_services(self, **kwargs):
        calls.append(("dt_catalog", kwargs))
        return catalog

    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fake_catalog)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_services_for_slot", fake_dt_services)
    state.set_current_screen("u35", "35", screen)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
    state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, "m1")
    state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
    state.set_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY, "12:00")

    ctx = _context(payload)
    asyncio.run(booking.handle_booking_category(ctx))

    assert "Список категорий уже обновился" in ctx.sender.messages[0]["text"]
    assert calls and calls[-1][0] == expected_method
    assert state.get_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY) == entry_mode
    if entry_mode == booking._ENTRY_MODE_STAFF_FIRST:
        assert state.get_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY) == "m1"
    if entry_mode == booking._ENTRY_MODE_DATETIME_FIRST:
        assert state.get_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY) == "2026-07-15"
        assert state.get_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY) == "12:00"


@pytest.mark.parametrize("entry_mode,expected_method", [
    (booking._ENTRY_MODE_SERVICE_FIRST, "catalog"),
    (booking._ENTRY_MODE_STAFF_FIRST, "selected_category"),
    (booking._ENTRY_MODE_DATETIME_FIRST, "dt_catalog"),
])
def test_stale_service_refresh_is_branch_aware(monkeypatch, entry_mode, expected_method) -> None:
    # Telegram behavior: stale service does not select another service and reloads valid services for current branch.
    calls = []
    catalog = BookingCatalog(categories=[_cat()], services=[_service()])

    async def fake_valid_services(self, **kwargs):
        calls.append(("selected_category", kwargs))
        return [_service()]

    async def fake_dt_services(self, **kwargs):
        calls.append(("dt_catalog", kwargs))
        return catalog

    async def fake_catalog(self, **kwargs):
        calls.append(("catalog", kwargs))
        return catalog

    monkeypatch.setattr(booking.BookingService, "get_valid_services_for_constraints", fake_valid_services)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_services_for_slot", fake_dt_services)
    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fake_catalog)
    state.set_current_screen("u35", "35", state.BOOKING_SERVICES_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
    state.set_state_data_value("u35", "35", booking._SELECTED_CATEGORY_STATE_KEY, "c1")
    state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, "m1")
    state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
    state.set_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY, "12:00")
    state.set_state_data_value("u35", "35", booking._CATALOG_STATE_KEY, catalog)

    ctx = _context(f"{BOOKING_SERVICE_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_service(ctx))

    assert "Список услуг уже обновился" in ctx.sender.messages[0]["text"]
    assert calls and calls[-1][0] == expected_method
    assert state.get_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY) is None


@pytest.mark.parametrize("entry_mode,expected_method", [
    (booking._ENTRY_MODE_SERVICE_FIRST, "masters"),
    (booking._ENTRY_MODE_STAFF_FIRST, "staff_masters"),
    (booking._ENTRY_MODE_DATETIME_FIRST, "dt_masters"),
])
def test_stale_master_refresh_is_branch_aware(monkeypatch, entry_mode, expected_method) -> None:
    # Telegram behavior: stale master refreshes the current master-selection branch and keeps valid context.
    calls = []

    async def fake_masters(self, **kwargs):
        calls.append(("staff_masters" if kwargs.get("entry_mode") == booking._ENTRY_MODE_STAFF_FIRST else "masters", kwargs))
        return [_master()]

    async def fake_dt_masters(self, **kwargs):
        calls.append(("dt_masters", kwargs))
        return [_master()]

    monkeypatch.setattr(booking.BookingService, "get_valid_masters_for_constraints", fake_masters)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_masters_for_slot", fake_dt_masters)
    state.set_current_screen("u35", "35", state.BOOKING_MASTERS_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
    state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, "s1")
    state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
    state.set_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY, "12:00")

    ctx = _context(f"{BOOKING_MASTER_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_master(ctx))

    assert "Список мастеров уже обновился" in ctx.sender.messages[0]["text"]
    assert calls and calls[-1][0] == expected_method
    assert state.get_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY) is None


@pytest.mark.parametrize("entry_mode,expected_method", [
    (booking._ENTRY_MODE_SERVICE_FIRST, "dates"),
    (booking._ENTRY_MODE_STAFF_FIRST, "dates"),
    (booking._ENTRY_MODE_DATETIME_FIRST, "dt_dates"),
])
def test_stale_date_refresh_is_branch_aware(monkeypatch, entry_mode, expected_method) -> None:
    # Telegram behavior: stale date refreshes available dates for current branch without changing selections.
    calls = []

    async def fake_dates(self, **kwargs):
        calls.append(("dates", kwargs))
        return [datetime(2026, 7, 15).date()]

    async def fake_dt_dates(self, **kwargs):
        calls.append(("dt_dates", kwargs))
        return [datetime(2026, 7, 15).date()]

    monkeypatch.setattr(booking.BookingService, "get_available_dates_for_selection", fake_dates)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_available_dates", fake_dt_dates)
    monkeypatch.setattr(booking.BookingService, "get_branch_timezone", lambda self: "Europe/Samara")
    state.set_current_screen("u35", "35", state.BOOKING_DATES_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
    state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, None if entry_mode == booking._ENTRY_MODE_DATETIME_FIRST else "s1")
    state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, None if entry_mode == booking._ENTRY_MODE_DATETIME_FIRST else "m1")

    ctx = _context(f"{BOOKING_DATE_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_date(ctx))

    assert "свободного времени нет" in ctx.sender.messages[0]["text"]
    assert calls and calls[-1][0] == expected_method
    assert state.get_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY) == entry_mode


@pytest.mark.parametrize("entry_mode,expected_method", [
    (booking._ENTRY_MODE_SERVICE_FIRST, "slots"),
    (booking._ENTRY_MODE_STAFF_FIRST, "slots"),
    (booking._ENTRY_MODE_DATETIME_FIRST, "dt_slots"),
])
def test_stale_slot_refresh_is_branch_aware(monkeypatch, entry_mode, expected_method) -> None:
    # Telegram behavior: stale slot reloads slots for the same date and never selects another slot.
    calls = []

    async def fake_slots(self, **kwargs):
        calls.append(("slots", kwargs))
        return [_slot("15:00")]

    async def fake_dt_slots(self, booking_date, **kwargs):
        calls.append(("dt_slots", {"booking_date": str(booking_date)}))
        return [_slot("15:00")]

    monkeypatch.setattr(booking.BookingService, "get_available_slots", fake_slots)
    monkeypatch.setattr(booking.BookingService, "get_datetime_first_slots_for_date", fake_dt_slots)
    state.set_current_screen("u35", "35", state.BOOKING_SLOTS_SCREEN)
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
    state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
    state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, None if entry_mode == booking._ENTRY_MODE_DATETIME_FIRST else "s1")
    state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, None if entry_mode == booking._ENTRY_MODE_DATETIME_FIRST else "m1")

    ctx = _context(f"{BOOKING_SLOT_PAYLOAD_PREFIX}9")
    asyncio.run(booking.handle_booking_slot(ctx))

    assert "окно уже неактуально" in ctx.sender.messages[0]["text"]
    assert calls and calls[-1][0] == expected_method
    assert state.get_state_data_value("u35", "35", booking._SELECTED_SLOT_TIME_STATE_KEY) is None
    assert state.get_current_screen("u35", "35") == state.BOOKING_SLOTS_SCREEN


def test_back_navigation_matrices_for_all_entry_modes(monkeypatch) -> None:
    # Telegram behavior: Back follows explicit branch matrix and preserves selected context.
    async def fake_dates(ctx, *, push_current=True):
        state.set_current_screen("u35", "35", state.BOOKING_DATES_SCREEN)
        await ctx.send_text("dates", keyboard=booking.navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))

    async def fake_slots(ctx, *args, **kwargs):
        state.set_current_screen("u35", "35", state.BOOKING_SLOTS_SCREEN)
        await ctx.send_text("slots", keyboard=booking.navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))

    async def fake_catalog(ctx, *, push_current=True):
        state.set_current_screen("u35", "35", state.BOOKING_CATEGORIES_SCREEN)
        await ctx.send_text("catalog", keyboard=booking.navigation_keyboard(back_payload=BOOKING_BACK_PAYLOAD))

    monkeypatch.setattr(booking, "_show_booking_dates", fake_dates)
    monkeypatch.setattr(booking, "_open_datetime_first_slots", fake_slots)
    monkeypatch.setattr(booking, "_open_booking_catalog", fake_catalog)

    for entry_mode, edges in {
        booking._ENTRY_MODE_SERVICE_FIRST: [(state.BOOKING_CATEGORIES_SCREEN, state.BOOKING_HUB_SCREEN), (state.BOOKING_SLOTS_SCREEN, state.BOOKING_DATES_SCREEN)],
        booking._ENTRY_MODE_STAFF_FIRST: [(state.BOOKING_SERVICES_SCREEN, state.BOOKING_MASTERS_SCREEN), (state.BOOKING_SLOTS_SCREEN, state.BOOKING_DATES_SCREEN)],
        booking._ENTRY_MODE_DATETIME_FIRST: [(state.BOOKING_DATES_SCREEN, state.BOOKING_HUB_SCREEN), (state.BOOKING_SERVICES_SCREEN, state.BOOKING_SLOTS_SCREEN)],
    }.items():
        for current, expected in edges:
            state.clear_user_state("u35", "35")
            state.set_current_screen("u35", "35", current)
            state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, entry_mode)
            state.set_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY, "m1")
            selected_service = None if (entry_mode == booking._ENTRY_MODE_DATETIME_FIRST and current == state.BOOKING_DATES_SCREEN) else "s1"
            state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, selected_service)
            state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
            state.set_state_data_value("u35", "35", booking._MASTERS_STATE_KEY, [_master("m1")])
            ctx = _context(BOOKING_BACK_PAYLOAD)
            asyncio.run(booking.handle_booking_back(ctx))
            assert state.get_current_screen("u35", "35") == expected
            assert state.get_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY) == entry_mode


def test_any_master_visibility_and_selection_semantics(monkeypatch) -> None:
    # Telegram behavior: master list starts with “Любой специалист”; selecting it uses any-master YClients staff semantics.
    async def fake_dates(self, **kwargs):
        assert kwargs["yclients_master_id"] == "0"
        return [datetime(2026, 7, 15).date()]

    monkeypatch.setattr(booking.BookingService, "get_available_dates_for_selection", fake_dates)
    monkeypatch.setattr(booking.BookingService, "get_branch_timezone", lambda self: "Europe/Samara")
    ctx = _context("show-masters")
    state.set_state_data_value("u35", "35", booking._ENTRY_MODE_STATE_KEY, booking._ENTRY_MODE_SERVICE_FIRST)
    state.set_state_data_value("u35", "35", booking._SELECTED_SERVICE_STATE_KEY, "s1")
    asyncio.run(booking._show_masters(ctx, [_master("m1")]))
    assert _texts(ctx)[0] == "👤 Любой специалист"
    assert BOOKING_MASTER_ANY_PAYLOAD in _payloads(ctx)

    select = _context(BOOKING_MASTER_ANY_PAYLOAD)
    state.set_current_screen("u35", "35", state.BOOKING_MASTERS_SCREEN)
    asyncio.run(booking.handle_booking_master_any(select))
    assert state.get_state_data_value("u35", "35", booking._SELECTED_MASTER_STATE_KEY) == "0"
    assert state.get_current_screen("u35", "35") == state.BOOKING_DATES_SCREEN


def test_real_service_master_filter_removes_incompatible_and_unavailable(monkeypatch) -> None:
    # Telegram behavior: real BookingService filtering removes incompatible/no-future staff and avoids all-staff fallback.
    service = _service("s1", staff_ids=("m1", "m2", "m3"))
    masters = [_master("m1"), _master("m2"), _master("m3")]
    svc = booking.BookingService(None)

    async def fake_can_book(service_arg, staff_id, **kwargs):
        return staff_id != "m2"

    async def fake_future(service_id, *, staff_id=None):
        return staff_id == "m1"

    monkeypatch.setattr(svc, "_staff_can_book_service", fake_can_book)
    monkeypatch.setattr(svc, "_service_has_future_slot", fake_future)
    result = asyncio.run(svc.get_valid_masters_for_constraints(yclients_service_id="s1", service=service, entry_mode="service_first", masters=masters))
    assert [item.yclients_master_id for item in result] == ["m1"]


def test_date_and_slot_pagination_boundaries_and_invalid_pages() -> None:
    # Telegram behavior: date/slot prev-next clamps stale pages and keeps Back/Home.
    dates = [datetime(2026, 7, day).date() for day in range(1, 24)]
    slots = [BookingSlotItem(time=f"{8 + i // 4:02d}:{(i % 4) * 15:02d}") for i in range(31)]
    ctx = _context("dates")
    asyncio.run(booking._show_dates(ctx, dates, timezone_name="Europe/Samara", page=1))
    assert "⬅️" in _texts(ctx) and "➡️" in _texts(ctx)
    asyncio.run(booking._show_dates(ctx, dates, timezone_name="Europe/Samara", page=99))
    assert "➡️" not in _texts(ctx)
    state.set_state_data_value("u35", "35", booking._SELECTED_DATE_STATE_KEY, "2026-07-15")
    asyncio.run(booking._show_slots(ctx, slots, page=1))
    assert "⬅️" in _texts(ctx) and "➡️" in _texts(ctx)
    asyncio.run(booking._show_slots(ctx, slots, page=99))
    assert "➡️" not in _texts(ctx)


@pytest.mark.parametrize("category,status,text", [
    ("credentials", None, "настроена"),
    ("auth", 401, "доступ"),
    ("auth", 403, "доступ"),
    ("rate_limit", 429, "много запросов"),
    ("server", 500, "временно"),
    ("transport", None, "связаться"),
    ("malformed", None, "Попробуйте позже"),
])
def test_yclients_error_categories_are_masked_through_real_handler(monkeypatch, category, status, text) -> None:
    # Telegram behavior: selection-screen YClients failures are masked and keep navigation.
    exc = BookingServiceError(
        f"Не удалось загрузить данные. {text} 🙏\n\nПопробуйте позже.",
        diagnostic={"error_category": category, "http_status": status, "safe_response_snippet": "masked"},
    )

    async def fail_catalog(self, **kwargs):
        raise exc

    monkeypatch.setattr(booking.BookingService, "get_valid_categories_for_entry_mode", fail_catalog)
    state.set_current_screen("u35", "35", state.BOOKING_HUB_SCREEN)
    ctx = _context(BOOKING_HUB_SERVICE_PAYLOAD)
    asyncio.run(booking.handle_booking_hub_service(ctx))
    rendered = ctx.sender.messages[-1]["text"]
    assert text in rendered
    for forbidden in ("Authorization", "partner_token", "user_token", "raw_response", "traceback"):
        assert forbidden not in rendered
    assert BOOKING_BACK_PAYLOAD in _payloads(ctx)
    assert NAV_HOME_PAYLOAD in _payloads(ctx)
