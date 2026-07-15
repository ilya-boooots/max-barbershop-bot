from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import menu, settings
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.support_settings import (
    DEFAULT_SUPPORT_DESCRIPTION,
    DEFAULT_SUPPORT_MAX_USERNAME,
    DEFAULT_SUPPORT_USERNAME,
    SupportSettingsRepository,
)
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import (
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
    SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD,
    SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    SETTINGS_HOME_PAYLOAD,
    SETTINGS_SUPPORT_PAYLOAD,
    SETTINGS_SUPPORT_PREVIEW_PAYLOAD,
    SETTINGS_SUPPORT_RESET_PAYLOAD,
)
from max_barbershop_bot.ui.texts import SETTINGS_NO_ACCESS_TEXT


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "support-settings.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    state._user_states.clear()
    return path


def _user(db: Path, user_id: str, *, role: str = "user") -> None:
    UsersRepository(str(db)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=user_id,
            max_user_id=user_id,
            chat_id="900",
            display_name=user_id,
            first_name=user_id,
            phone="+79990000000",
            birthdate="1990-01-01",
            role=role,
        )
    )
    if role != "user":
        StaffRolesRepository(str(db)).assign_role(
            user_id,
            role,
            assigned_by_platform_user_id="setup",
            platform=PLATFORM_MAX,
        )


def _context(
    *,
    actor: str = "100",
    payload: str | None = SETTINGS_SUPPORT_PAYLOAD,
    text: str | None = None,
) -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent(
        "message_callback" if payload is not None else "message_created",
        actor,
        actor,
        "900",
        text,
        payload,
        f"cb-{payload}" if payload is not None else None,
    )
    return RouterContext(event, sender), sender


def _buttons(keyboard: MaxInlineKeyboard):
    return [button for row in keyboard.rows for button in row]


def _seed_support(db: Path, *, telegram: str, max_username: str, description: str) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO support_settings (
                support_username, support_max_username, support_description, is_active
            )
            VALUES (?, ?, ?, 1)
            """,
            (telegram, max_username, description),
        )


def _audit_count(db: Path, action: str) -> int:
    with sqlite3.connect(db) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM settings_audit_log WHERE action = ?",
                (action,),
            ).fetchone()[0]
        )


def _trace_support_updates(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    updates: list[str] = []
    original_connect = SupportSettingsRepository._connect

    def traced_connect(repository: SupportSettingsRepository) -> sqlite3.Connection:
        connection = original_connect(repository)
        connection.set_trace_callback(
            lambda statement: updates.append(statement)
            if statement.lstrip().upper().startswith("UPDATE SUPPORT_SETTINGS")
            else None
        )
        return connection

    monkeypatch.setattr(SupportSettingsRepository, "_connect", traced_connect)
    return updates


def test_support_editor_routes_and_role_gate_use_real_handlers(db: Path) -> None:
    router = Router()
    settings.register_settings_routes(router)

    assert router._callback_handlers[SETTINGS_SUPPORT_PAYLOAD] is settings.handle_settings_support  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD] is settings.handle_settings_support_edit_username  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD] is settings.handle_settings_support_edit_description  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_SUPPORT_PREVIEW_PAYLOAD] is settings.handle_settings_support_preview  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_SUPPORT_RESET_PAYLOAD] is settings.handle_settings_support_reset  # noqa: SLF001

    _user(db, "100")
    context, sender = _context()
    asyncio.run(settings.handle_settings_support(context))

    assert sender.callbacks == [f"cb-{SETTINGS_SUPPORT_PAYLOAD}"]
    assert sender.messages == [(SETTINGS_NO_ACCESS_TEXT, None)]
    assert state.get_current_screen("100", "900") != state.SETTINGS_SUPPORT_SCREEN

    for user_id, role in (("101", ROLE_ADMIN), ("102", ROLE_MANAGER), ("103", ROLE_DEVELOPER)):
        _user(db, user_id, role=role)
        allowed_context, allowed_sender = _context(actor=user_id)
        asyncio.run(settings.handle_settings_support(allowed_context))
        assert allowed_sender.callbacks == [f"cb-{SETTINGS_SUPPORT_PAYLOAD}"]
        assert state.get_current_screen(user_id, "900") == state.SETTINGS_SUPPORT_SCREEN


def test_support_edit_prompts_are_exact_and_callbacks_answered(db: Path) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    username_context, username_sender = _context(payload=SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD)

    asyncio.run(settings.handle_settings_support_edit_username(username_context))

    assert username_sender.callbacks == [f"cb-{SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD}"]
    assert username_sender.messages[-1][0] == "👤 Введите username аккаунта поддержки (можно с @):"
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN

    description_context, description_sender = _context(payload=SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD)
    asyncio.run(settings.handle_settings_support_edit_description(description_context))

    assert description_sender.callbacks == [f"cb-{SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD}"]
    assert description_sender.messages[-1][0] == "✏️ Введите новое описание для раздела поддержки:"
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN


def test_support_editor_text_keyboard_and_current_screen_match_visible_matrix(db: Path) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="telegram_help", max_username="max_help", description="Поможем в MAX")
    context, sender = _context()

    asyncio.run(settings.handle_settings_support(context))

    text, keyboard = sender.messages[-1]
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_SCREEN
    assert text == (
        '🛠 Настройка раздела "Поддержка"\n\n'
        "📝 Текущее описание:\nПоможем в MAX\n\n"
        "👤 Текущий аккаунт: @max_help\n"
        "🔗 Ссылка: https://max.ru/max_help"
    )
    assert [button.text for button in _buttons(keyboard)] == [
        "✏️ Изменить описание",
        "👤 Изменить аккаунт поддержки",
        "👁️ Предпросмотр",
        "♻️ Сбросить к значениям по умолчанию",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_support_username_handler_normalizes_link_updates_max_cta_and_clears_stale_navigation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="old_telegram", max_username="old_max", description="Описание")
    updates = _trace_support_updates(monkeypatch)
    nav = state._get_state("100", "900")
    nav.current_screen = state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN
    nav.screen_stack[:] = [state.SETTINGS_MENU_SCREEN, state.SETTINGS_SUPPORT_SCREEN]
    nav.state_data["stale_token"] = "must-disappear"
    context, sender = _context(payload=None, text="  https://max.ru/new_name  ")

    asyncio.run(settings.handle_settings_support_username_input(context))
    asyncio.run(settings.handle_settings_support_username_input(context))

    stored = SupportSettingsRepository(str(db)).get_active()
    assert stored is not None
    assert stored.support_username == "new_name"
    assert stored.support_max_username == "new_name"
    assert [text for text, _ in sender.messages].count("✅ Аккаунт поддержки обновлён: @new_name") == 2
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_SCREEN
    assert nav.screen_stack == []
    assert nav.state_data == {}
    assert len(updates) == 1
    assert _audit_count(db, "support_account_changed") == 1
    assert sender.messages[-1][0].endswith("🔗 Ссылка: https://max.ru/new_name")


def test_support_username_handler_rejects_malformed_stale_input_without_mutation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="telegram_help", max_username="max_help", description="Описание")
    updates = _trace_support_updates(monkeypatch)
    state.set_current_screen("100", "900", state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN)
    context, sender = _context(payload=None, text="https://max.ru/wrong/path?internal_id=42")

    asyncio.run(settings.handle_settings_support_username_input(context))

    stored = SupportSettingsRepository(str(db)).get_active()
    assert stored is not None
    assert stored.support_username == "telegram_help"
    assert stored.support_max_username == "max_help"
    assert sender.messages[-1][0] == (
        "⚠️ Некорректный username. Укажите MAX username (5-32 символа, латиница/цифры/_)"
    )
    assert "internal_id" not in sender.messages[-1][0]
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN
    assert updates == []
    assert _audit_count(db, "support_account_changed") == 0


def test_support_description_handler_validates_and_persists_once(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="telegram_help", max_username="max_help", description="Старое описание")
    updates = _trace_support_updates(monkeypatch)
    state.set_current_screen("100", "900", state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN)
    empty_context, empty_sender = _context(payload=None, text="   ")

    asyncio.run(settings.handle_settings_support_description_input(empty_context))

    assert empty_sender.messages[-1][0] == "⚠️ Описание не может быть пустым. Введите текст ещё раз."
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN

    context, sender = _context(payload=None, text="  Новое описание  ")
    asyncio.run(settings.handle_settings_support_description_input(context))
    asyncio.run(settings.handle_settings_support_description_input(context))

    stored = SupportSettingsRepository(str(db)).get_active()
    assert stored is not None
    assert stored.support_description == "Новое описание"
    assert stored.support_username == "telegram_help"
    assert stored.support_max_username == "max_help"
    assert [text for text, _ in sender.messages].count("✅ Описание поддержки обновлено") == 2
    assert len(updates) == 1
    assert _audit_count(db, "support_description_changed") == 1


def test_support_preview_uses_public_renderer_keyboard_and_safe_back_state(db: Path) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="telegram_help", max_username="max_help", description="Свяжитесь с нами")
    state.set_current_screen("100", "900", state.SETTINGS_SUPPORT_SCREEN)
    context, sender = _context(payload=SETTINGS_SUPPORT_PREVIEW_PAYLOAD)

    asyncio.run(settings.handle_settings_support_preview(context))
    asyncio.run(settings.handle_settings_support_preview(context))

    nav = state._get_state("100", "900")
    preview_text, preview_keyboard = sender.messages[-1]
    preview_buttons = _buttons(preview_keyboard)
    assert nav.current_screen == state.SUPPORT_SCREEN
    assert nav.screen_stack == [state.SETTINGS_SUPPORT_SCREEN]
    assert preview_text == "🆘 Поддержка\n\nСвяжитесь с нами"
    assert [button.text for button in preview_buttons] == [
        "🆘 Написать в поддержку",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]
    assert preview_buttons[0].url == "https://max.ru/max_help"
    assert preview_buttons[1].payload == NAV_BACK_PAYLOAD
    assert preview_buttons[2].payload == NAV_HOME_PAYLOAD
    assert sender.callbacks == [f"cb-{SETTINGS_SUPPORT_PREVIEW_PAYLOAD}"] * 2

    back_context, back_sender = _context(payload=NAV_BACK_PAYLOAD)
    asyncio.run(menu.handle_nav_back(back_context))

    assert back_sender.callbacks == [f"cb-{NAV_BACK_PAYLOAD}"]
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_SCREEN
    assert back_sender.messages[-1][0].startswith('🛠 Настройка раздела "Поддержка"')


def test_support_reset_restores_defaults_without_repeated_mutation_or_audit(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _seed_support(db, telegram="telegram_help", max_username="max_help", description="Другое описание")
    updates = _trace_support_updates(monkeypatch)
    context, sender = _context(payload=SETTINGS_SUPPORT_RESET_PAYLOAD)

    asyncio.run(settings.handle_settings_support_reset(context))
    asyncio.run(settings.handle_settings_support_reset(context))

    stored = SupportSettingsRepository(str(db)).get_active()
    assert stored is not None
    assert stored.support_username == DEFAULT_SUPPORT_USERNAME
    assert stored.support_max_username == DEFAULT_SUPPORT_MAX_USERNAME
    assert stored.support_description == DEFAULT_SUPPORT_DESCRIPTION
    assert [text for text, _ in sender.messages].count(
        '♻️ Раздел "Поддержка" сброшен к значениям по умолчанию.'
    ) == 2
    assert len(updates) == 1
    assert _audit_count(db, "support_reset") == 1
    assert sender.callbacks == [f"cb-{SETTINGS_SUPPORT_RESET_PAYLOAD}"] * 2


def test_support_input_back_and_home_restore_expected_screens(db: Path) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    state.set_current_screen("100", "900", state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN)
    back_context, back_sender = _context(payload=NAV_BACK_PAYLOAD)

    asyncio.run(settings.handle_settings_back(back_context))

    assert back_sender.callbacks == [f"cb-{NAV_BACK_PAYLOAD}"]
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_SCREEN
    assert back_sender.messages[-1][0].startswith('🛠 Настройка раздела "Поддержка"')

    state.set_state_data_value("100", "900", "temporary", "value")
    home_context, home_sender = _context(payload=NAV_HOME_PAYLOAD)
    asyncio.run(settings.handle_settings_home(home_context))

    assert home_sender.callbacks == [f"cb-{NAV_HOME_PAYLOAD}"]
    assert state.get_current_screen("100", "900") == state.MAIN_MENU_SCREEN
    assert state._get_state("100", "900").state_data == {}
    assert home_sender.messages[-1][0] == "✨ 100, выберите действие в меню ниже 👇"


def test_support_repository_error_is_masked_and_keeps_input_screen(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    state.set_current_screen("100", "900", state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN)

    def fail_save(self: SupportSettingsRepository, support_username: str | None, support_description: str | None):
        raise RuntimeError("partner_token=SECRET raw_response=private internal_id=42")

    monkeypatch.setattr(SupportSettingsRepository, "upsert_active", fail_save)
    context, sender = _context(payload=None, text="valid_name")

    asyncio.run(settings.handle_settings_support_username_input(context))

    text, keyboard = sender.messages[-1]
    assert text == "⚠️ Не удалось сохранить настройки поддержки. Попробуйте ещё раз."
    assert not any(marker in text for marker in ("SECRET", "raw_response", "internal_id", "42"))
    assert state.get_current_screen("100", "900") == state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN
    assert [button.payload for button in _buttons(keyboard)] == [SETTINGS_BACK_PAYLOAD, SETTINGS_HOME_PAYLOAD]
