from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows import support as max_support
from max_barbershop_bot.repositories.support_settings import (
    DEFAULT_SUPPORT_DESCRIPTION,
    DEFAULT_SUPPORT_MAX_USERNAME,
    SupportSettings,
    SupportSettingsRepository,
    build_max_support_url,
    effective_support_settings,
)
from max_barbershop_bot.ui.buttons import MENU_SUPPORT_PAYLOAD, NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def flatten_keyboard(keyboard):
    return [button for row in keyboard.rows for button in row]


def test_plan_declares_public_support_only_scope() -> None:
    plan = read("docs/max_telegram_parity_plan_v2.md")

    assert "PR-034 — Public support screen parity" in plan
    assert "Public support only." in plan
    assert "support editor" in plan
    assert "contacts/support editors" in plan


def test_active_telegram_reference_support_flow_proof() -> None:
    sections = read("telegram_reference/app/handlers/sections.py")
    service = read("telegram_reference/app/services/support.py")

    assert "async def handle_support" in sections
    assert "render_support_message(support_settings.description)" in sections
    assert "support_screen_kb(username=support_settings.username, include_home=True)" in sections
    assert "def render_support_message" in service
    assert "def support_screen_kb" in service
    assert "🆘 Написать в поддержку" in service


def test_default_public_text_is_exact() -> None:
    settings = effective_support_settings(None)

    assert max_support.render_support_message(settings) == (
        "🆘 Поддержка\n\n"
        "Если у вас возникли вопросы, напишите нам — с удовольствием поможем! 🙂"
    )


def test_custom_description_is_trimmed_and_rendered_below_title() -> None:
    settings = effective_support_settings(SupportSettings(support_description="  Напишите нам в MAX 🙂  "))

    assert max_support.render_support_message(settings) == "🆘 Поддержка\n\nНапишите нам в MAX 🙂"


def test_empty_description_uses_default_description() -> None:
    settings = effective_support_settings(SupportSettings(support_description="   "))

    assert settings.support_description == DEFAULT_SUPPORT_DESCRIPTION
    assert max_support.render_support_message(settings) == f"🆘 Поддержка\n\n{DEFAULT_SUPPORT_DESCRIPTION}"


@pytest.mark.parametrize(
    "forbidden",
    [
        "Конт" + "акт:",
        "@flowbots1sup",
        "https://max.ru",
        "https://" + "t.me",
        "Если кнопка" + " не открывается",
        "скопируйте" + " контакт",
    ],
)
def test_public_message_does_not_duplicate_contact_or_link(forbidden: str) -> None:
    message = max_support.render_support_message(effective_support_settings(None))

    assert forbidden not in message


def test_default_destination_uses_default_max_username() -> None:
    settings = effective_support_settings(None)
    keyboard = max_support._support_keyboard(settings)
    support_button = keyboard.rows[0][0]

    assert support_button.text == "🆘 Написать в поддержку"
    assert support_button.url == build_max_support_url(DEFAULT_SUPPORT_MAX_USERNAME)


def test_custom_max_username_is_used_for_link() -> None:
    settings = effective_support_settings(
        SupportSettings(support_username="telegram_name", support_max_username="max_name")
    )

    assert settings.support_max_username == "max_name"
    assert max_support._support_keyboard(settings).rows[0][0].url == "https://max.ru/max_name"


def test_legacy_support_username_fallback_is_used_for_max_link() -> None:
    settings = effective_support_settings(
        SupportSettings(support_username="@legacy_name", support_max_username=None)
    )

    assert settings.support_max_username == "legacy_name"
    assert max_support._support_keyboard(settings).rows[0][0].url == "https://max.ru/legacy_name"


@pytest.mark.parametrize(
    "settings",
    [
        SupportSettings(support_username="bad name", support_max_username=""),
        SupportSettings(support_username="", support_max_username="https://max.ru/bad/path"),
        SupportSettings(support_username=None, support_max_username=None),
    ],
)
def test_invalid_or_empty_legacy_values_fall_back_to_default_max_username(settings: SupportSettings) -> None:
    effective = effective_support_settings(settings)

    assert effective.support_max_username == DEFAULT_SUPPORT_MAX_USERNAME
    assert max_support._support_keyboard(effective).rows[0][0].url == build_max_support_url(DEFAULT_SUPPORT_MAX_USERNAME)


def test_exact_keyboard_order_and_semantics() -> None:
    keyboard = max_support._support_keyboard(effective_support_settings(None))
    buttons = flatten_keyboard(keyboard)

    assert [button.text for button in buttons] == ["🆘 Написать в поддержку", "⬅️ Назад", "🏠 Главное меню"]
    assert buttons[0].type == "link"
    assert buttons[0].url == build_max_support_url(DEFAULT_SUPPORT_MAX_USERNAME)
    assert buttons[0].payload is None
    assert buttons[1].type == "callback"
    assert buttons[1].payload == NAV_BACK_PAYLOAD
    assert buttons[1].url is None
    assert buttons[2].type == "callback"
    assert buttons[2].payload == NAV_HOME_PAYLOAD
    assert buttons[2].url is None


def test_invalid_direct_settings_omit_broken_link_but_keep_back_home() -> None:
    keyboard = max_support._support_keyboard(SupportSettings(support_max_username="bad/path"))
    buttons = flatten_keyboard(keyboard)

    assert [button.text for button in buttons] == ["⬅️ Назад", "🏠 Главное меню"]
    assert all(button.type == "callback" for button in buttons)


def test_router_registers_menu_support_payload_to_real_handler() -> None:
    router = Router()

    max_support.register_support_routes(router)

    assert router._callback_handlers[MENU_SUPPORT_PAYLOAD] is max_support.handle_support


def test_public_handler_has_no_role_or_registration_gate() -> None:
    source = inspect.getsource(max_support.handle_support)

    assert "role" not in source.lower()
    assert "registration" not in source.lower()
    assert "is_registered" not in source
    assert "NO_ACCESS" not in source


class FakeSender:
    def __init__(self) -> None:
        self.callback_ids: list[str] = []
        self.sent: list[tuple[int, str, object]] = []

    async def answer_callback(self, callback_id: str) -> None:
        self.callback_ids.append(callback_id)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.sent.append((chat_id, text, keyboard))


def test_handle_support_sets_state_and_does_not_duplicate_stack(monkeypatch) -> None:
    monkeypatch.setattr(max_support, "_resolve_support_settings", lambda: effective_support_settings(None))
    sender = FakeSender()
    event = NormalizedEvent("message_callback", "u-pr034", "u-pr034", "9034", None, MENU_SUPPORT_PAYLOAD, "cb-1")
    context = RouterContext(event=event, sender=sender)
    state.clear_user_state("u-pr034", "9034")
    state.set_current_screen("u-pr034", "9034", state.MAIN_MENU_SCREEN)

    import asyncio

    asyncio.run(max_support.handle_support(context))
    key = state.build_state_key("u-pr034", "9034")
    nav = state._user_states[key]
    assert nav.current_screen == state.SUPPORT_SCREEN
    assert nav.screen_stack == [state.MAIN_MENU_SCREEN]

    asyncio.run(max_support.handle_support(context))
    assert nav.current_screen == state.SUPPORT_SCREEN
    assert nav.screen_stack == [state.MAIN_MENU_SCREEN]
    assert sender.callback_ids == ["cb-1", "cb-1"]
    assert sender.sent[-1][1] == f"🆘 Поддержка\n\n{DEFAULT_SUPPORT_DESCRIPTION}"


def test_repository_effective_settings_use_stored_values_and_defaults(tmp_path) -> None:
    db_path = tmp_path / "support.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE support_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                support_username TEXT,
                support_max_username TEXT,
                support_description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO support_settings (support_username, support_max_username, support_description, is_active)
            VALUES ('legacy_db', NULL, '  DB description  ', 1)
            """
        )

    settings = effective_support_settings(SupportSettingsRepository(str(db_path)).get_active())

    assert settings.support_description == "DB description"
    assert settings.support_max_username == "legacy_db"


def test_scope_safety_forbidden_files_and_imports() -> None:
    support_flow = read("max_barbershop_bot/flows/support.py")
    buttons = read("max_barbershop_bot/ui/buttons.py")
    settings_flow = read("max_barbershop_bot/flows/settings.py")
    contacts_flow = read("max_barbershop_bot/flows/contacts.py")
    menu_buttons = read("max_barbershop_bot/ui/buttons.py")
    all_max_python = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "max_barbershop_bot").rglob("*.py"))

    assert "settings_support_keyboard" not in support_flow
    assert "SETTINGS" + "_SUPPORT" not in support_flow
    assert "support" + "_edit" not in support_flow
    assert "upsert" + "_active" not in support_flow
    assert "Редактировать" + " поддержку" not in support_flow
    assert "def settings_support_keyboard" in buttons
    assert "handle_settings_support" in settings_flow
    assert "handle_contacts" in contacts_flow
    assert "MENU_SUPPORT_PAYLOAD" in menu_buttons
    assert "MENU_CONTACTS_PAYLOAD" in menu_buttons
    assert "MENU_BOOKING_PAYLOAD" in menu_buttons
    assert "ADMIN_BROADCASTS_PAYLOAD" in menu_buttons
    assert "from aiogram" not in all_max_python
    assert "import aiogram" not in all_max_python
