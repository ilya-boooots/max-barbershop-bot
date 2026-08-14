from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import admin_bookings, menu
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.services import admin_bookings as service
from max_barbershop_bot.services.admin_bookings import (
    AdminBookingsAuthError,
    AdminBookingsFilter,
    AdminBookingsLoadError,
    AdminBookingsRateLimitError,
    AdminBookingsResult,
    AdminBookingsSettingsMissingError,
)
from max_barbershop_bot.ui.buttons import ADMIN_BOOKINGS_OPEN_PAYLOAD, NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD
from max_barbershop_bot.ui.texts import (
    ADMIN_BOOKINGS_AUTH_ERROR_TEXT,
    ADMIN_BOOKINGS_GENERIC_ERROR_TEXT,
    ADMIN_BOOKINGS_NOT_CONFIGURED_TEXT,
    ADMIN_BOOKINGS_RATE_LIMIT_TEXT,
    ADMIN_BOOKINGS_UNAVAILABLE_TEXT,
    STATISTICS_NO_ACCESS_TEXT,
)


@dataclass
class FakeSender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"text": text, "keyboard": keyboard})

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"text": text, "keyboard": keyboard})

    async def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "admin-bookings.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.delenv("DEV_MAX_USER_ID", raising=False)
    state._user_states.clear()
    return path


def _user(db: Path, user_id: str, *, role: str = "user") -> None:
    UsersRepository(str(db)).create(
        UserCreate(platform=PLATFORM_MAX, platform_user_id=user_id, max_user_id=user_id, chat_id="900")
    )
    if role != "user":
        StaffRolesRepository(str(db)).assign_role(
            user_id, role, assigned_by_platform_user_id="setup", platform=PLATFORM_MAX
        )


def _context(payload: str = ADMIN_BOOKINGS_OPEN_PAYLOAD, *, actor: str = "100") -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent(
        update_type="message_callback",
        platform_user_id=actor,
        max_user_id=actor,
        chat_id="900",
        text=None,
        callback_payload=payload,
        callback_id=f"cb-{payload}",
    )
    return RouterContext(event=event, sender=sender), sender


def _buttons(message: dict[str, object]) -> list[tuple[str, str]]:
    return [(button.text, button.payload) for row in message["keyboard"].rows for button in row]


def _result(*, rows: list[dict[str, object]] | None = None) -> AdminBookingsResult:
    bookings = rows if rows is not None else [
        {
            "id": "secret-record-id",
            "datetime": "2026-07-16T09:30:00+04:00",
            "status": "confirmed",
            "staff_name": "Анна",
            "services": [{"title": "Услуга"}],
            "phone": "+79991234567",
        }
    ]
    statuses = sorted({str(row.get("status")) for row in bookings if row.get("status")})
    return AdminBookingsResult(
        title="Сегодня",
        bookings=bookings,
        page_bookings=bookings,
        statuses=statuses,
        counters={"confirmed": len(bookings) if statuses else 0, "pending": 0, "cancelled": 0},
        page=0,
        max_page=0,
    )


def test_admin_bookings_today_real_handler_matches_roles_text_keyboard_and_screen(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100")
    denied_context, denied_sender = _context()
    asyncio.run(admin_bookings.handle_admin_bookings_open(denied_context))
    assert denied_sender.messages[-1]["text"] == STATISTICS_NO_ACCESS_TEXT

    async def load(filters, *, actor_platform_user_id_present=False):
        assert filters == AdminBookingsFilter()
        return _result()

    monkeypatch.setattr(admin_bookings, "load_admin_bookings", load)
    for actor, role in (("101", "admin"), ("102", "manager"), ("103", "developer")):
        _user(db, actor, role=role)
        context, sender = _context(actor=actor)
        asyncio.run(admin_bookings.handle_admin_bookings_open(context))
        assert sender.messages[-1]["text"] == (
            "📋 Записи — Сегодня\n✅ Подтверждено: 1 | ⏳ Ожидает: 0 | ❌ Отменено: 0"
        )
        buttons = _buttons(sender.messages[-1])
        assert buttons[:5] == [
            ("📅 Сегодня", "admbook:day:today"),
            ("📅 Завтра", "admbook:day:tomorrow"),
            ("👤 Мастер", "admbook:filter:master"),
            ("🧾 Статус", "admbook:filter:status"),
            ("🔄 Обновить", "admbook:refresh"),
        ]
        assert buttons[-2:] == [("⬅️ Назад", NAV_BACK_PAYLOAD), ("🏠 Главное меню", NAV_HOME_PAYLOAD)]
        assert "secret-record-id" not in " ".join(payload for _, payload in buttons)
        assert state.get_current_screen(actor, "900") == state.ADMIN_BOOKINGS_LIST_SCREEN


def test_admin_bookings_today_env_developer_and_empty_status_behavior_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "104")
    monkeypatch.setenv("DEV_MAX_USER_ID", "104")

    async def empty(filters, *, actor_platform_user_id_present=False):
        return _result(rows=[])

    monkeypatch.setattr(admin_bookings, "load_admin_bookings", empty)
    context, sender = _context(actor="104")
    asyncio.run(admin_bookings.handle_admin_bookings_open(context))
    assert sender.messages[-1]["text"] == "📋 Записи — Сегодня\n\n😌 На выбранный день записей нет."

    rows_without_status = [{"id": "1", "datetime": "2026-07-16T12:00:00+04:00"}]
    assert service.format_admin_bookings_list(_result(rows=rows_without_status)) == "📋 Записи — Сегодня"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AdminBookingsSettingsMissingError(), ADMIN_BOOKINGS_NOT_CONFIGURED_TEXT),
        (AdminBookingsAuthError(), ADMIN_BOOKINGS_AUTH_ERROR_TEXT),
        (AdminBookingsRateLimitError(), ADMIN_BOOKINGS_RATE_LIMIT_TEXT),
        (AdminBookingsLoadError(), ADMIN_BOOKINGS_UNAVAILABLE_TEXT),
        (RuntimeError("token raw payload"), ADMIN_BOOKINGS_GENERIC_ERROR_TEXT),
    ],
)
def test_admin_bookings_today_errors_are_exact_masked_and_navigable_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: str,
) -> None:
    _user(db, "105", role="manager")

    async def failed(filters, *, actor_platform_user_id_present=False):
        raise error

    monkeypatch.setattr(admin_bookings, "load_admin_bookings", failed)
    context, sender = _context(actor="105")
    asyncio.run(admin_bookings.handle_admin_bookings_open(context))
    assert sender.messages[-1]["text"] == expected
    assert "token" not in expected.lower()
    assert _buttons(sender.messages[-1])[-2:] == [
        ("⬅️ Назад", NAV_BACK_PAYLOAD),
        ("🏠 Главное меню", NAV_HOME_PAYLOAD),
    ]


def test_admin_bookings_today_stale_and_repeat_callbacks_do_not_mutate_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "106", role="manager")
    calls = 0

    async def load(filters, *, actor_platform_user_id_present=False):
        nonlocal calls
        calls += 1
        return _result()

    monkeypatch.setattr(admin_bookings, "load_admin_bookings", load)
    for _ in range(2):
        context, sender = _context("admbook:refresh", actor="106")
        asyncio.run(admin_bookings.handle_admin_bookings_refresh(context))
        assert sender.callbacks == ["cb-admbook:refresh"]
    assert calls == 2

    stale_context, stale_sender = _context("admbook:item:29", actor="106")
    asyncio.run(admin_bookings.handle_admin_bookings_detail(stale_context))
    assert stale_sender.messages[-1]["text"] == service.STALE_LIST_TEXT


def test_admin_bookings_today_service_uses_branch_date_yclients_order_and_no_local_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Settings:
        company_id = "company"

    class Clock:
        def today(self):
            return date(2026, 7, 16)

        def localize_datetime(self, value):
            return datetime.fromisoformat(value)

        def now(self):
            return datetime(2026, 7, 16, 0, 0)

        def format_datetime(self, value):
            return "16.07 11:00"

    class Client:
        async def close(self):
            pass

    calls = []

    async def list_rows(client, **kwargs):
        calls.append(kwargs)
        return {"data": [{"id": "2", "datetime": "2026-07-16T11:00:00", "status": "pending"}]}

    monkeypatch.setattr(service, "_active_settings", lambda: Settings())
    monkeypatch.setattr(service, "has_required_yclients_credentials", lambda settings: True)
    monkeypatch.setattr(service, "CompanyTimeService", lambda repository: Clock())
    monkeypatch.setattr(service, "build_yclients_client_from_active_settings", lambda settings: Client())
    monkeypatch.setattr(service, "list_bookings_by_date_range", list_rows)

    result = asyncio.run(service.load_admin_bookings(AdminBookingsFilter()))
    assert calls[0]["date_from"] == calls[0]["date_to"] == "2026-07-16"
    assert result.page_bookings[0]["id"] == "2"
    assert "_platform_source" not in result.page_bookings[0]
    assert "Источник" not in service.format_admin_booking_card(result.page_bookings[0])


def test_admin_bookings_today_back_and_home_use_real_global_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def back(context):
        calls.append("back")

    async def home(context):
        calls.append("home")

    monkeypatch.setattr(menu, "go_back", back)
    monkeypatch.setattr(menu, "show_home", home)
    for payload, handler in ((NAV_BACK_PAYLOAD, menu.handle_nav_back), (NAV_HOME_PAYLOAD, menu.handle_nav_home)):
        context, sender = _context(payload)
        asyncio.run(handler(context))
        assert sender.callbacks == [f"cb-{payload}"]
    assert calls == ["back", "home"]
