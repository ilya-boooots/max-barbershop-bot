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
from max_barbershop_bot.flows import clients_directory
from max_barbershop_bot.integrations.yclients.exceptions import YClientsAuthError
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD, NAV_HOME_PAYLOAD


@dataclass
class Sender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "clients-name.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    state._user_states.clear()
    UsersRepository(str(path)).create(
        UserCreate(platform=PLATFORM_MAX, platform_user_id="200", max_user_id="200", chat_id="900")
    )
    StaffRolesRepository(str(path)).assign_role(
        "200", "manager", assigned_by_platform_user_id="setup", platform=PLATFORM_MAX
    )
    return path


def _context(*, payload: str | None = None, text: str | None = None):
    sender = Sender()
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback" if payload else "message_created",
            platform_user_id="200",
            max_user_id="200",
            chat_id="900",
            text=text,
            callback_payload=payload,
            callback_id=f"cb-{payload}" if payload else None,
        ),
        sender=sender,
    ), sender


def _buttons(message):
    return [(button.text, button.payload) for row in message["keyboard"].rows for button in row]


class Layer:
    service = None

    async def __aenter__(self):
        return self.service

    async def __aexit__(self, *_args):
        return None


def test_clients_name_real_handlers_match_prompt_validation_and_access(db: Path) -> None:
    context, sender = _context(payload=CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD)
    asyncio.run(clients_directory.handle_search_name(context))
    assert sender.messages[-1]["text"] == "✍️ Введите имя клиента в чат, я сразу найду клиента 🙂"
    assert state.get_current_screen("200", "900") == state.CLIENTS_DIRECTORY_SEARCH_SCREEN

    invalid, invalid_sender = _context(text="А")
    asyncio.run(clients_directory.handle_query_text(invalid))
    assert invalid_sender.messages[-1]["text"] == "🙂 Введите хотя бы 2 символа имени 🔎"


def test_clients_name_real_query_and_page_handlers_match_selection_masking_and_too_many(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.set_state_data_value("200", "900", clients_directory._MODE_KEY, "name")
    requested_pages = []

    class Service:
        async def find_client(self, **kwargs):
            requested_pages.append(kwargs["page"])
            assert kwargs["query"] == "Анна" and kwargs["by_name"] is True and kwargs["count"] == 9
            count = 9 if kwargs["page"] == 1 else 2
            return [
                SimpleNamespace(id=f"secret-{kwargs['page']}-{index}", name=f"Анна {index}", phone="+79991234567", raw={})
                for index in range(count)
            ]

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    context, sender = _context(text="  Анна  ")
    asyncio.run(clients_directory.handle_query_text(context))
    assert sender.messages[-1]["text"] == "👥 Результаты поиска • стр. 1\n\nВыберите клиента ниже 👇"
    buttons = _buttons(sender.messages[-1])
    assert buttons[0] == ("👤 Анна 0 • 📞 +*******4567", "clients:result:0")
    assert all("secret-" not in label and "ID" not in label for label, _ in buttons)
    assert ("➡️ След", "clients:page:2") in buttons

    page_context, page_sender = _context(payload="clients:page:2")
    asyncio.run(clients_directory.handle_page(page_context))
    assert page_sender.callbacks == ["cb-clients:page:2"]
    assert page_sender.messages[-1]["text"] == "👥 Результаты поиска • стр. 2\n\nВыберите клиента ниже 👇"
    assert ("⬅️ Пред", "clients:page:1") in _buttons(page_sender.messages[-1])
    assert requested_pages == [1, 2]


def test_clients_name_real_result_handler_opens_exact_card_and_repeated_callback_is_read_only(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.set_state_data_value("200", "900", clients_directory._MODE_KEY, "name")
    state.set_state_data_value("200", "900", clients_directory._RESULTS_KEY, [{"id": "42"}])
    calls = 0

    class Service:
        async def get_client_card(self, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                id="42",
                name="Анна",
                phone="+79991234567",
                raw={"last_visit_date": "10.07.2026", "visits_count": 7, "total_spent": 9000, "notes": "—"},
            )

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    for _ in range(2):
        context, sender = _context(payload="clients:result:0")
        asyncio.run(clients_directory.handle_result(context))
        assert sender.messages[-1]["text"] == (
            "👤 Карточка клиента\n\n👤 Имя: Анна\n📞 Телефон: +*******4567\n"
            "🆔 Client ID: 42\n🕒 Последний визит: 10.07.2026\n🧾 Кол-во визитов: 7\n"
            "💳 Потрачено: 9000\n📝 Заметки: —"
        )
        assert _buttons(sender.messages[-1])[-1] == ("🏠 Главное меню", NAV_HOME_PAYLOAD)
    assert calls == 2


def test_clients_name_empty_stale_and_malformed_callbacks_are_friendly_real_handlers(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.set_state_data_value("200", "900", clients_directory._MODE_KEY, "name")

    class Service:
        async def find_client(self, **kwargs):
            return []

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    context, sender = _context(text="Никто")
    asyncio.run(clients_directory.handle_query_text(context))
    assert sender.messages[-1]["text"] == "😔 Клиент не найден. Попробуйте другой запрос 🙂"

    stale, stale_sender = _context(payload="clients:result:7")
    asyncio.run(clients_directory.handle_result(stale))
    assert stale_sender.messages[-1]["text"] == clients_directory.STALE_RESULTS_TEXT
    malformed, malformed_sender = _context(payload="clients:page:bad")
    asyncio.run(clients_directory.handle_page(malformed))
    assert malformed_sender.messages[-1]["text"] == clients_directory.STALE_RESULTS_TEXT
    state.set_state_data_value("200", "900", clients_directory._QUERY_KEY, None)
    old_page, old_page_sender = _context(payload="clients:page:2")
    asyncio.run(clients_directory.handle_page(old_page))
    assert old_page_sender.messages[-1]["text"] == clients_directory.STALE_RESULTS_TEXT


def test_clients_name_error_is_masked_and_keeps_navigation_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.set_state_data_value("200", "900", clients_directory._MODE_KEY, "name")

    class Service:
        async def find_client(self, **kwargs):
            raise YClientsAuthError("Bearer secret-token phone +79991234567")

    Layer.service = Service()
    monkeypatch.setattr(clients_directory, "_yclients_layer", Layer)
    context, sender = _context(text="Анна")
    asyncio.run(clients_directory.handle_query_text(context))
    assert sender.messages[-1]["text"] == "🔐 Ошибка доступа к YClients. Проверьте токены и права 🙂"
    assert "secret-token" not in sender.messages[-1]["text"]
    assert _buttons(sender.messages[-1])[-1] == ("🏠 Главное меню", NAV_HOME_PAYLOAD)
