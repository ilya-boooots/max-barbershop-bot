from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import clients_directory, menu
from max_barbershop_bot.integrations.yclients.exceptions import YClientsAuthError, YClientsRateLimitError
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import (
    ADMIN_CLIENTS_DIRECTORY_PAYLOAD,
    CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
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
    path = tmp_path / "clients-phone.sqlite3"
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


def _context(*, payload: str | None = None, text: str | None = None, actor: str = "100"):
    sender = FakeSender()
    event = NormalizedEvent(
        update_type="message_callback" if payload is not None else "message_created",
        platform_user_id=actor,
        max_user_id=actor,
        chat_id="900",
        text=text,
        callback_payload=payload,
        callback_id=f"cb-{payload}" if payload is not None else None,
    )
    return RouterContext(event=event, sender=sender), sender


def _buttons(message: dict[str, object]) -> list[tuple[str, str]]:
    keyboard = message["keyboard"]
    return [(button.text, button.payload) for row in keyboard.rows for button in row]


class Layer:
    service = None

    async def __aenter__(self):
        return self.service

    async def __aexit__(self, *_args):
        return None


def test_clients_phone_real_handlers_match_roles_prompt_keyboard_and_env_developer(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100")
    denied, denied_sender = _context(payload=ADMIN_CLIENTS_DIRECTORY_PAYLOAD)
    asyncio.run(clients_directory.handle_clients_directory_menu(denied))
    assert denied_sender.messages[-1]["text"] == clients_directory.NO_ACCESS_TEXT

    for actor, role in (("101", "admin"), ("102", "manager")):
        _user(db, actor, role=role)
        context, sender = _context(payload=ADMIN_CLIENTS_DIRECTORY_PAYLOAD, actor=actor)
        asyncio.run(clients_directory.handle_clients_directory_menu(context))
        assert sender.messages[-1]["text"] == "Введите телефон или имя клиента 📲🙂"
        assert _buttons(sender.messages[-1]) == [
            ("📞 По телефону", CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD),
            ("🔎 По имени", "clients:search_name"),
            ("⬅️ Назад", NAV_BACK_PAYLOAD),
            ("🏠 Главное меню", NAV_HOME_PAYLOAD),
        ]

    _user(db, "103")
    monkeypatch.setenv("DEV_MAX_USER_ID", "103")
    context, sender = _context(payload=CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD, actor="103")
    asyncio.run(clients_directory.handle_search_phone(context))
    assert sender.messages[-1]["text"] == "✍️ Введите телефон клиента в чат, я сразу найду клиента 🙂"
    assert state.get_current_screen("103", "900") == state.CLIENTS_DIRECTORY_SEARCH_SCREEN


def test_clients_phone_real_query_handler_matches_normalization_validation_results_and_limit(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "104", role="manager")
    state.set_state_data_value("104", "900", clients_directory._MODE_KEY, "phone")

    invalid, invalid_sender = _context(text="12345", actor="104")
    asyncio.run(clients_directory.handle_query_text(invalid))
    assert invalid_sender.messages[-1]["text"] == "🙂 Введите телефон подлиннее, хотя бы 6 цифр 📞"

    class Service:
        async def find_client(self, **kwargs):
            assert kwargs["query"] == "+79991234567"
            assert kwargs["by_phone"] is True and kwargs["by_name"] is False and kwargs["count"] == 9
            return [
                SimpleNamespace(id=str(index), name=f"Клиент {index}", phone="+79991234567", raw={})
                for index in range(9)
            ]

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    context, sender = _context(text="8 (999) 123-45-67", actor="104")
    asyncio.run(clients_directory.handle_query_text(context))
    assert sender.messages[-1]["text"] == "👥 Результаты поиска • стр. 1\n\nВыберите клиента ниже 👇"
    buttons = _buttons(sender.messages[-1])
    assert buttons[0] == ("👤 Клиент 0 • 📞 +*******4567", "clients:result:0")
    assert all("ID" not in label and payload != "0" for label, payload in buttons)
    assert ("➡️ След", "clients:page:2") in buttons
    assert state.get_current_screen("104", "900") == state.CLIENTS_DIRECTORY_RESULTS_SCREEN


def test_clients_phone_real_result_handler_opens_exact_compact_card_and_masks_phone(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "105", role="manager")
    state.set_state_data_value("105", "900", clients_directory._MODE_KEY, "phone")
    state.set_state_data_value("105", "900", clients_directory._RESULTS_KEY, [{"id": "42"}])

    class Service:
        async def get_client_card(self, **kwargs):
            assert kwargs["yclients_client_id"] == "42"
            return SimpleNamespace(
                id="42",
                name="Анна",
                phone="+79991234567",
                raw={"last_visit": "01.07.2026", "visits_count": 3, "spent": 4500, "comment": "VIP"},
            )

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    monkeypatch.setattr(clients_directory, "CompanyTimeService", lambda *_args: pytest.fail("phone card must not load future/history"))
    context, sender = _context(payload="clients:result:0", actor="105")
    asyncio.run(clients_directory.handle_result(context))
    assert sender.messages[-1]["text"] == (
        "👤 Карточка клиента\n\n"
        "👤 Имя: Анна\n"
        "📞 Телефон: +*******4567\n"
        "🆔 Client ID: 42\n"
        "🕒 Последний визит: 01.07.2026\n"
        "🧾 Кол-во визитов: 3\n"
        "💳 Потрачено: 4500\n"
        "📝 Заметки: VIP"
    )
    assert _buttons(sender.messages[-1])[-1] == ("🏠 Главное меню", NAV_HOME_PAYLOAD)
    assert state.get_current_screen("105", "900") == state.CLIENTS_DIRECTORY_CARD_SCREEN


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("yclients_not_configured"), clients_directory.NOT_CONFIGURED_TEXT),
        (YClientsAuthError("Bearer secret-token"), "🔐 Ошибка доступа к YClients. Проверьте токены и права 🙂"),
        (YClientsRateLimitError("raw payload"), "⏳ Слишком много запросов. Попробуйте через пару секунд 🙂"),
        (RuntimeError("internal-id"), "🛠️ YClients временно недоступен. Попробуйте чуть позже 🙂"),
    ],
)
def test_clients_phone_errors_are_masked_and_navigable_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: str,
) -> None:
    _user(db, "106", role="manager")
    state.set_state_data_value("106", "900", clients_directory._MODE_KEY, "phone")

    class Service:
        async def find_client(self, **kwargs):
            raise error

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    context, sender = _context(text="89991234567", actor="106")
    asyncio.run(clients_directory.handle_query_text(context))
    assert sender.messages[-1]["text"] == expected
    assert "secret-token" not in expected and "internal-id" not in expected


def test_clients_phone_stale_and_repeated_result_callbacks_are_safe_real_handlers(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "107", role="manager")
    stale, stale_sender = _context(payload="clients:result:7", actor="107")
    asyncio.run(clients_directory.handle_result(stale))
    assert stale_sender.messages[-1]["text"] == clients_directory.STALE_RESULTS_TEXT

    calls = 0
    state.set_state_data_value("107", "900", clients_directory._MODE_KEY, "phone")
    state.set_state_data_value("107", "900", clients_directory._RESULTS_KEY, [{"id": "42"}])

    class Service:
        async def get_client_card(self, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(id="42", name="Анна", phone="+79991234567", raw={})

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    for _ in range(2):
        context, _sender = _context(payload="clients:result:0", actor="107")
        asyncio.run(clients_directory.handle_result(context))
    assert calls == 2


def test_clients_phone_back_and_home_use_real_global_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def back(context):
        calls.append("back")

    async def home(context):
        calls.append("home")

    monkeypatch.setattr(menu, "go_back", back)
    monkeypatch.setattr(menu, "show_home", home)
    for payload, handler in ((NAV_BACK_PAYLOAD, menu.handle_nav_back), (NAV_HOME_PAYLOAD, menu.handle_nav_home)):
        context, sender = _context(payload=payload)
        asyncio.run(handler(context))
        assert sender.callbacks == [f"cb-{payload}"]
    assert calls == ["back", "home"]
