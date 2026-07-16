from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import menu, statistics
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.services import statistics as statistics_service
from max_barbershop_bot.services.statistics import BusinessSummary, StatisticsLoadError
from max_barbershop_bot.ui.buttons import (
    ADMIN_STATISTICS_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
)
from max_barbershop_bot.ui.texts import STATISTICS_LOAD_ERROR_TEXT, STATISTICS_NO_ACCESS_TEXT


@dataclass
class FakeSender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard})

    async def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "statistics.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.delenv("DEV_MAX_USER_ID", raising=False)
    state._user_states.clear()
    return path


def _user(db: Path, user_id: str, *, role: str = "user") -> None:
    UsersRepository(str(db)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=user_id,
            max_user_id=user_id,
            chat_id="900",
            first_name="Иван",
            birthdate="1990-01-01",
        )
    )
    if role != "user":
        StaffRolesRepository(str(db)).assign_role(
            user_id,
            role,
            assigned_by_platform_user_id="setup",
            platform=PLATFORM_MAX,
        )


def _context(payload: str, *, actor: str = "100") -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    return (
        RouterContext(
            event=NormalizedEvent(
                update_type="message_callback",
                platform_user_id=actor,
                max_user_id=actor,
                chat_id="900",
                text=None,
                callback_payload=payload,
                callback_id=f"cb-{payload}",
            ),
            sender=sender,
        ),
        sender,
    )


def _buttons(message: dict[str, object]) -> list[tuple[str, str]]:
    keyboard = message["keyboard"]
    return [(button.text, button.payload) for row in keyboard.rows for button in row]


def _summary() -> BusinessSummary:
    return BusinessSummary(2, 2, 3, 12345.6, 4115.2, 1, 1, 1)


def test_statistics_summary_real_handler_matches_access_text_keyboard_and_current_screen(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = Router()
    statistics.register_statistics_routes(router)
    assert router._callback_handlers[ADMIN_STATISTICS_PAYLOAD] is statistics.handle_statistics_menu
    assert dict(router._callback_prefix_handlers)["stats:"] is statistics.handle_statistics_period

    _user(db, "100")
    denied_context, denied_sender = _context(ADMIN_STATISTICS_PAYLOAD)
    asyncio.run(statistics.handle_statistics_menu(denied_context))
    assert denied_sender.messages[-1]["text"] == STATISTICS_NO_ACCESS_TEXT

    async def summary() -> BusinessSummary:
        return _summary()

    monkeypatch.setattr(statistics, "get_business_summary", summary)
    for actor, role in (("101", "manager"), ("102", "developer")):
        _user(db, actor, role=role)
        context, sender = _context(ADMIN_STATISTICS_PAYLOAD, actor=actor)
        asyncio.run(statistics.handle_statistics_menu(context))
        assert sender.messages[-1]["text"] == (
            "📊 Статистика\n\n"
            "👤 Клиентов зарегистрировано в боте: 2\n"
            "👥 Клиентов всего: 2\n"
            "🧾 Записей всего: 3\n"
            "💰 Выручка за весь период: 12 346 ₽\n"
            "💳 Средний чек: 4 115 ₽\n"
            "✅ Дошли/оплатили: 1\n"
            "❌ Отмены: 1\n"
            "🚫 Не пришли: 1"
        )
        assert _buttons(sender.messages[-1]) == [
            ("⬅️ Назад", NAV_BACK_PAYLOAD),
            ("🏠 Главное меню", NAV_HOME_PAYLOAD),
        ]
        assert state.get_current_screen(actor, "900") == state.STATISTICS_RESULT_SCREEN


def test_statistics_summary_env_developer_and_zero_empty_behavior_use_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "103")
    monkeypatch.setenv("DEV_MAX_USER_ID", "103")

    async def empty_summary() -> BusinessSummary:
        return BusinessSummary(0, 0, 0, 0, 0, 0, 0, 0)

    monkeypatch.setattr(statistics, "get_business_summary", empty_summary)
    context, sender = _context(ADMIN_STATISTICS_PAYLOAD, actor="103")
    asyncio.run(statistics.handle_statistics_menu(context))
    assert "🧾 Записей всего: 0" in sender.messages[-1]["text"]
    assert "💰 Выручка за весь период: 0 ₽" in sender.messages[-1]["text"]
    assert "пока нет" not in sender.messages[-1]["text"]


def test_statistics_summary_stale_and_repeated_callback_are_read_only_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "104", role="manager")
    calls = 0

    async def summary() -> BusinessSummary:
        nonlocal calls
        calls += 1
        return _summary()

    monkeypatch.setattr(statistics, "get_business_summary", summary)
    for _ in range(2):
        context, sender = _context("stats:legacy-malformed", actor="104")
        asyncio.run(statistics.handle_statistics_period(context))
        assert sender.messages[-1]["text"].startswith("📊 Статистика")
        assert sender.callbacks == ["cb-stats:legacy-malformed"]
    assert calls == 2


def test_statistics_summary_error_is_masked_and_keeps_navigation_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "105", role="manager")

    async def failed() -> BusinessSummary:
        raise StatisticsLoadError("secret-token internal-id")

    monkeypatch.setattr(statistics, "get_business_summary", failed)
    context, sender = _context(ADMIN_STATISTICS_PAYLOAD, actor="105")
    asyncio.run(statistics.handle_statistics_menu(context))
    assert sender.messages[-1]["text"] == STATISTICS_LOAD_ERROR_TEXT
    assert "secret" not in sender.messages[-1]["text"]
    assert _buttons(sender.messages[-1]) == [
        ("⬅️ Назад", NAV_BACK_PAYLOAD),
        ("🏠 Главное меню", NAV_HOME_PAYLOAD),
    ]


def test_statistics_summary_service_matches_attribution_metrics_and_branch_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_periods = []
    settings = YClientsSettings(
        company_id="company",
        partner_token="partner",
        user_token="user",
        branch_timezone="Asia/Vladivostok",
    )
    monkeypatch.setattr(statistics_service, "load_active_yclients_settings", lambda *args, **kwargs: settings)
    monkeypatch.setattr(statistics_service, "has_required_yclients_credentials", lambda value: True)

    class Users:
        def __init__(self, path: str) -> None:
            pass

        def list_users_for_broadcast_audience(self, *, platform: str):
            return [
                SimpleNamespace(first_name="Анна", birthdate="1990-01-01"),
                SimpleNamespace(first_name=None, birthdate=None),
            ]

    class Attribution:
        def __init__(self, path: str) -> None:
            pass

        def list_active_yclients_record_ids(self, *, platform: str, yclients_record_ids):
            assert list(yclients_record_ids) == ["1", "2", "3", "4"]
            return ["2", "4"]

    class ClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Service:
        def __init__(self, client, *, company_id: str) -> None:
            pass

    async def records(service, *, period):
        captured_periods.append(period)
        return [
            {"id": "1", "comment": "Клиент записался из MAX бота"},
            {"id": "2"},
            {"id": "3", "comment": "другой канал"},
            {"id": "4"},
        ]

    async def details(service, rows):
        assert [row["id"] for row in rows] == ["1", "2", "4"]
        return [
            {"id": "1", "attendance": 1, "final_price": 1000},
            {"id": "2", "status": "cancelled", "amount": 500},
            {"id": "4", "attendance": -1, "price": 1500},
        ]

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, timezone=None):
            assert str(timezone) == "Asia/Vladivostok"
            return cls(2026, 7, 17, 0, 30, tzinfo=timezone)

    monkeypatch.setattr(statistics_service, "UsersRepository", Users)
    monkeypatch.setattr(statistics_service, "PlatformAttributionRepository", Attribution)
    monkeypatch.setattr(statistics_service, "build_yclients_client_from_active_settings", lambda value: ClientContext())
    monkeypatch.setattr(statistics_service, "YClientsServiceLayer", Service)
    monkeypatch.setattr(statistics_service, "get_records_for_period_from_yclients", records)
    monkeypatch.setattr(statistics_service, "_load_record_details", details)
    monkeypatch.setattr(statistics_service, "datetime", FixedDateTime)

    result = asyncio.run(statistics_service.get_business_summary())
    assert captured_periods[0].start.isoformat() == "2000-01-01"
    assert captured_periods[0].end.isoformat() == "2026-07-17"
    assert result == BusinessSummary(1, 1, 3, 3000, 1000, 1, 1, 1)
    assert statistics_service._is_completed({"attendance": 0, "status": "completed"})
    assert statistics_service._is_no_show({"attendance": 0, "status": "no_show"})


def test_statistics_summary_back_and_home_use_real_global_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def go_back(context: RouterContext) -> None:
        calls.append("back")

    async def show_home(context: RouterContext) -> None:
        calls.append("home")

    monkeypatch.setattr(menu, "go_back", go_back)
    monkeypatch.setattr(menu, "show_home", show_home)
    for payload, handler in ((NAV_BACK_PAYLOAD, menu.handle_nav_back), (NAV_HOME_PAYLOAD, menu.handle_nav_home)):
        context, sender = _context(payload)
        asyncio.run(handler(context))
        assert sender.callbacks == [f"cb-{payload}"]
    assert calls == ["back", "home"]
