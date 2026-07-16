from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import menu, settings
from max_barbershop_bot.repositories.diagnostics import DiagnosticsRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.services.developer_diagnostics import NO_ACCESS_TEXT
from max_barbershop_bot.ui.buttons import (
    DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
    SETTINGS_DIAGNOSTICS_PAYLOAD,
)


DEV_ID = "900"
CHAT_ID = "9900"


@dataclass
class Sender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)
    files: list[dict[str, object]] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)

    async def send_file_bytes_to_chat(self, chat_id: int, content: bytes, *, filename: str, text=None, keyboard=None):
        self.files.append({"content": content, "filename": filename, "text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_file_bytes_to_user(self, user_id: int, content: bytes, *, filename: str, text=None, keyboard=None):
        return await self.send_file_bytes_to_chat(user_id, content, filename=filename, text=text, keyboard=keyboard)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "developer-diagnostics.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEV_MAX_USER_ID", DEV_ID)
    UsersRepository(str(path)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=DEV_ID,
            max_user_id=DEV_ID,
            chat_id=CHAT_ID,
            first_name="Разработчик",
            phone="+79990000900",
            birthdate="1990-01-02",
        )
    )
    state._user_states.clear()
    return path


def _context(
    *,
    user_id: str = DEV_ID,
    payload: str | None = None,
    text: str | None = None,
    callback_id: str | None = "cb",
    sender: Sender | None = None,
) -> tuple[RouterContext, Sender]:
    actual_sender = sender or Sender()
    return (
        RouterContext(
            event=NormalizedEvent(
                update_type="message_callback" if payload is not None else "message_created",
                platform_user_id=user_id,
                max_user_id=user_id,
                chat_id=CHAT_ID,
                text=text,
                callback_payload=payload,
                callback_id=callback_id if payload is not None else None,
            ),
            sender=actual_sender,
        ),
        actual_sender,
    )


def _event_count(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM user_events").fetchone()[0])


def test_developer_diagnostics_real_handler_is_hidden_from_every_non_developer_role(db: Path) -> None:
    for user_id, role in (("101", "user"), ("102", "manager"), ("103", "admin")):
        if role != "user":
            StaffRolesRepository(str(db)).assign_role(
                user_id,
                role,
                assigned_by_platform_user_id="permission-test",
                platform=PLATFORM_MAX,
            )
        context, sender = _context(user_id=user_id, payload=SETTINGS_DIAGNOSTICS_PAYLOAD)
        asyncio.run(settings.handle_settings_diagnostics(context))
        assert sender.callbacks == ["cb"]
        assert [message["text"] for message in sender.messages] == [NO_ACCESS_TEXT]

    developer, developer_sender = _context(payload=SETTINGS_DIAGNOSTICS_PAYLOAD)
    asyncio.run(settings.handle_settings_diagnostics(developer))
    assert developer_sender.callbacks == ["cb"]
    assert "🛠 Разработка: Диагностика" in developer_sender.messages[-1]["text"]
    assert state.get_current_screen(DEV_ID, CHAT_ID) == state.SETTINGS_DIAGNOSTICS_SCREEN


def test_developer_bot_logs_real_handlers_paginate_clamp_and_stay_read_only(db: Path) -> None:
    repository = DiagnosticsRepository(str(db))
    for index in range(45):
        repository.log_bot_event(
            level="INFO",
            source="pagination-test",
            message=f"line-{index:02d} " + ("safe diagnostic text " * 10),
        )
    before = len(repository.get_recent_bot_logs(200))
    sender = Sender()
    context, _ = _context(payload="devdiag:bot_logs", sender=sender)
    asyncio.run(settings.handle_settings_diagnostics_bot_logs(context))
    assert "Страница 1/" in sender.messages[-1]["text"]

    for index in range(20):
        next_context, _ = _context(
            payload=DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD,
            callback_id=f"next-{index}",
            sender=sender,
        )
        asyncio.run(settings.handle_settings_diagnostics_log_pagination(next_context))
    final_text = str(sender.messages[-1]["text"])
    page_label = final_text.split("Страница ", 1)[1].split("\n", 1)[0]
    current_page, total_pages = (int(part) for part in page_label.split("/"))
    assert current_page == total_pages
    assert len(repository.get_recent_bot_logs(200)) == before


def test_developer_bot_logs_csv_real_handler_exports_masked_file(db: Path) -> None:
    DiagnosticsRepository(str(db)).log_bot_event(
        level="ERROR",
        source="csv-test",
        message="Authorization: Bearer super-secret-token phone +79991234567",
        details={"user_token": "hidden-token-value", "phone": "+79997654321"},
    )
    context, sender = _context(payload="devdiag:bot_logs_csv")
    asyncio.run(settings.handle_settings_diagnostics_bot_logs_csv(context))

    assert sender.callbacks == ["cb"]
    assert sender.files[0]["filename"] == "bot_logs_last_200.csv"
    content = bytes(sender.files[0]["content"]).decode("utf-8")
    assert "super-secret" not in content and "hidden-token-value" not in content
    assert "+79991234567" not in content and "+79997654321" not in content
    assert "***" in content


def test_developer_user_search_real_handler_masks_output_and_handles_empty_error(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DiagnosticsRepository(str(db))
    repository.log_user_event(
        platform_user_id="123456789",
        max_user_id="123456789",
        chat_id="987654321",
        username="alice",
        phone="+79991234567",
        event_type="message_callback",
        event_name="profile:open",
        screen="profile",
        payload={"callback_payload": "client:internal-42", "user_token": "hidden-value"},
    )
    context, sender = _context(text="123456789", callback_id=None)
    asyncio.run(settings.handle_settings_diagnostics_user_logs_input(context))
    output = str(sender.messages[-1]["text"])
    assert "***6789" in output and "123456789" not in output
    assert "secret-token-value" not in output and "+79997654321" not in output
    assert state.get_current_screen(DEV_ID, CHAT_ID) == state.SETTINGS_DIAGNOSTICS_SCREEN

    state.set_current_screen(DEV_ID, CHAT_ID, state.SETTINGS_DIAGNOSTICS_USER_LOGS_INPUT_SCREEN)
    empty, empty_sender = _context(text="   ", callback_id=None)
    asyncio.run(settings.handle_settings_diagnostics_user_logs_input(empty))
    assert "Введите user_id или @username" in empty_sender.messages[-1]["text"]
    assert state.get_current_screen(DEV_ID, CHAT_ID) == state.SETTINGS_DIAGNOSTICS_USER_LOGS_INPUT_SCREEN

    def fail_search(*_args, **_kwargs):
        raise RuntimeError("Authorization: Bearer must-not-leak +79990000000")

    monkeypatch.setattr(DiagnosticsRepository, "find_user_events", fail_search)
    failed, failed_sender = _context(text="123456789", callback_id=None)
    asyncio.run(settings.handle_settings_diagnostics_user_logs_input(failed))
    assert failed_sender.messages[-1]["text"] == (
        "⚠️ Не удалось выполнить поиск по логам пользователя. Попробуйте ещё раз."
    )
    assert "must-not-leak" not in failed_sender.messages[-1]["text"]


def test_developer_event_search_real_handler_handles_result_empty_and_repeat(db: Path) -> None:
    repository = DiagnosticsRepository(str(db))
    repository.log_user_event(
        platform_user_id="77778888",
        max_user_id="77778888",
        chat_id=CHAT_ID,
        username="bob",
        phone=None,
        event_type="message_callback",
        event_name="booking:open",
        screen="booking_hub",
    )
    before = _event_count(db)
    sender = Sender()
    for _ in range(2):
        context, _ = _context(text="booking:open", callback_id=None, sender=sender)
        asyncio.run(settings.handle_settings_diagnostics_event_search_input(context))
        assert "booking:open" in sender.messages[-1]["text"]
        assert "77778888" not in sender.messages[-1]["text"]
    assert _event_count(db) == before

    missing, missing_sender = _context(text="definitely-missing", callback_id=None)
    asyncio.run(settings.handle_settings_diagnostics_event_search_input(missing))
    assert missing_sender.messages[-1]["text"].endswith("Ничего не найдено 🙏")


def test_developer_diagnostics_back_home_real_handlers_clear_owned_state(db: Path) -> None:
    state.set_current_screen(DEV_ID, CHAT_ID, state.SETTINGS_DIAGNOSTICS_SCREEN)
    state.push_screen(DEV_ID, CHAT_ID, state.SETTINGS_MENU_SCREEN)
    state.set_state_data_value(DEV_ID, CHAT_ID, "developer_search_query", "booking")
    back, back_sender = _context(payload=NAV_BACK_PAYLOAD)
    asyncio.run(menu.handle_nav_back(back))
    assert back_sender.callbacks == ["cb"]
    assert state.get_current_screen(DEV_ID, CHAT_ID) == state.SETTINGS_MENU_SCREEN

    home, home_sender = _context(payload=NAV_HOME_PAYLOAD)
    asyncio.run(menu.handle_nav_home(home))
    assert home_sender.callbacks == ["cb"]
    assert state.get_current_screen(DEV_ID, CHAT_ID) == state.MAIN_MENU_SCREEN
    assert state.get_state_data_value(DEV_ID, CHAT_ID, "developer_search_query") is None
