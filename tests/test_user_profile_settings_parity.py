from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import settings as settings_flow
from max_barbershop_bot.flows.settings import (
    handle_settings_menu,
    handle_settings_profile,
    handle_settings_profile_edit_name,
    handle_settings_profile_edit_phone,
    handle_settings_profile_name_input,
    handle_settings_profile_phone_input,
    handle_settings_profile_save_name,
    handle_settings_profile_save_phone,
)
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import (
    ADMIN_SETTINGS_PAYLOAD,
    SETTINGS_PROFILE_EDIT_NAME_PAYLOAD,
    SETTINGS_PROFILE_EDIT_PHONE_PAYLOAD,
    SETTINGS_PROFILE_PAYLOAD,
    SETTINGS_PROFILE_RETRY_NAME_PAYLOAD,
    SETTINGS_PROFILE_RETRY_PHONE_PAYLOAD,
    SETTINGS_PROFILE_SAVE_NAME_PAYLOAD,
    SETTINGS_PROFILE_SAVE_PHONE_PAYLOAD,
    main_menu_keyboard,
    settings_menu_keyboard,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Sent:
    text: str
    keyboard: object | None


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.answered = 0

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent(text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent(text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="chat", recipient_id=str(chat_id))

    async def answer_callback(self, callback_id):
        self.answered += 1
        return None


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "bot.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.delenv("DEV_MAX_USER_ID", raising=False)
    yield path
    for user_id in ("user", "new", "manager", "admin", "developer", "other"):
        state.clear_user_state(user_id, "900")


def _event(payload: str = ADMIN_SETTINGS_PAYLOAD, *, user: str = "user", text: str | None = None, attachments: list[object] | None = None) -> NormalizedEvent:
    update_type = "message_created" if text is not None or attachments else "message_callback"
    return NormalizedEvent(update_type, user, user, "900", text, payload if update_type == "message_callback" else None, "cb" if update_type == "message_callback" else None, attachments=attachments or [])


def _ctx(sender: FakeSender, payload: str = ADMIN_SETTINGS_PAYLOAD, *, user: str = "user", text: str | None = None, attachments: list[object] | None = None) -> RouterContext:
    return RouterContext(_event(payload, user=user, text=text, attachments=attachments), sender)


def _buttons(keyboard) -> list[tuple[str, str | None]]:
    return [(button.text, button.payload) for row in keyboard.rows for button in row]


def _create_user(db_path: Path, platform_user_id: str = "user", *, phone: str = "+79990000001", first_name: str = "Иван", birthdate: str | None = "1990-01-01"):
    return UsersRepository(str(db_path)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=platform_user_id,
            max_user_id=platform_user_id,
            chat_id="900",
            first_name=first_name,
            display_name=first_name,
            last_name="Старый",
            username="old_username",
            phone=phone,
            birthdate=birthdate,
            yclients_client_id="yc-keep",
        )
    )


def test_telegram_reference_proves_user_settings_and_profile_callbacks() -> None:
    menu = (ROOT / "telegram_reference/app/keyboards/menu.py").read_text(encoding="utf-8")
    profile = (ROOT / "telegram_reference/app/handlers/master_photos_settings.py").read_text(encoding="utf-8")
    assert "def user_main_menu_kb" in menu
    assert "tail_buttons.append(SETTINGS_BTN)" in menu
    for marker in ("CB_PROFILE_ROOT", "CB_PROFILE_EDIT_NAME", "CB_PROFILE_EDIT_PHONE"):
        assert marker in profile


def test_registered_user_can_open_settings_and_unregistered_is_prompted(db) -> None:
    _create_user(db)
    sender = FakeSender()
    asyncio.run(handle_settings_menu(_ctx(sender)))
    assert sender.sent[-1].text.startswith("⚙️ Настройки")
    assert ("👤 Мои данные", SETTINGS_PROFILE_PAYLOAD) in _buttons(sender.sent[-1].keyboard)

    _create_user(db, "new", phone="+79990000002", birthdate=None)
    denied = FakeSender()
    asyncio.run(handle_settings_menu(_ctx(denied, user="new")))
    assert denied.sent[-1].text == "Сначала пройдите регистрацию: нажмите /start"


def test_operator_settings_sections_are_preserved(db) -> None:
    _create_user(db, "manager", phone="+79990000003")
    StaffRolesRepository(str(db)).assign_role("manager", "manager", assigned_by_platform_user_id="owner")
    buttons = _buttons(settings_menu_keyboard("manager"))
    assert ("👤 Мои данные", SETTINGS_PROFILE_PAYLOAD) in buttons
    assert ("🧩 YClients", "settings:yclients") in buttons
    assert ("🖼️ Редактировать фото мастеров", "settings:master_photos") in buttons
    assert ("👥 Роли", "settings:roles") in buttons

    user_buttons = _buttons(settings_menu_keyboard("user"))
    assert user_buttons == [
        ("👤 Мои данные", SETTINGS_PROFILE_PAYLOAD),
        ("⬅️ Назад", "settings:back"),
        ("🏠 Главное меню", "settings:home"),
    ]


def test_profile_root_shows_name_phone_and_buttons(db) -> None:
    _create_user(db, first_name="Анна", phone="+79990000004")
    sender = FakeSender()
    asyncio.run(handle_settings_profile(_ctx(sender, SETTINGS_PROFILE_PAYLOAD)))
    assert "👤 Мои данные" in sender.sent[-1].text
    assert "Имя: Анна" in sender.sent[-1].text
    assert "Телефон: +79990000004" in sender.sent[-1].text
    assert "Эти данные используются для записи." in sender.sent[-1].text
    assert _buttons(sender.sent[-1].keyboard) == [
        ("✏️ Изменить имя", SETTINGS_PROFILE_EDIT_NAME_PAYLOAD),
        ("📱 Изменить телефон", SETTINGS_PROFILE_EDIT_PHONE_PAYLOAD),
        ("⬅️ Назад", "settings:profile:back"),
        ("🏠 Главное меню", "settings:home"),
    ]


def test_name_edit_validation_retry_and_save_preserves_other_fields(db) -> None:
    original = _create_user(db, phone="+79990000005", birthdate="1988-02-03")
    sender = FakeSender()
    asyncio.run(handle_settings_profile_edit_name(_ctx(sender, SETTINGS_PROFILE_EDIT_NAME_PAYLOAD)))
    assert "👤 Текущее имя: Иван" in sender.sent[-1].text

    asyncio.run(handle_settings_profile_name_input(_ctx(sender, user="user", text="!!!")))
    assert sender.sent[-1].text == "⚠️ Укажите корректное имя (от 2 до 60 символов)."

    asyncio.run(handle_settings_profile_name_input(_ctx(sender, user="user", text="  Пётр  Петров  ")))
    assert sender.sent[-1].text == "Проверьте новое имя:\nПётр Петров"
    assert ("✅ Сохранить", SETTINGS_PROFILE_SAVE_NAME_PAYLOAD) in _buttons(sender.sent[-1].keyboard)
    assert ("✏️ Ввести заново", SETTINGS_PROFILE_RETRY_NAME_PAYLOAD) in _buttons(sender.sent[-1].keyboard)

    asyncio.run(handle_settings_profile_save_name(_ctx(sender, SETTINGS_PROFILE_SAVE_NAME_PAYLOAD)))
    updated = UsersRepository(str(db)).find_by_platform_user_id("user")
    assert updated is not None
    assert updated.first_name == "Пётр Петров"
    assert updated.display_name == "Пётр Петров"
    assert updated.phone == original.phone
    assert updated.birthdate == original.birthdate
    assert updated.role == original.role
    assert updated.last_name == original.last_name
    assert updated.username == original.username
    assert updated.yclients_client_id == original.yclients_client_id
    assert sender.sent[-2].text == "✅ Имя обновлено, Пётр Петров"
    assert "👤 Мои данные" in sender.sent[-1].text


def test_phone_edit_validation_duplicate_retry_contact_and_save_preserves_other_fields(db) -> None:
    original = _create_user(db, phone="+79990000006", birthdate="1988-02-03")
    _create_user(db, "other", phone="+79990000007", first_name="Другой")
    sender = FakeSender()
    asyncio.run(handle_settings_profile_edit_phone(_ctx(sender, SETTINGS_PROFILE_EDIT_PHONE_PAYLOAD)))
    assert "📱 Текущий телефон: +79990000006" in sender.sent[-1].text

    asyncio.run(handle_settings_profile_phone_input(_ctx(sender, user="user", text="abc")))
    assert sender.sent[-1].text == "⚠️ Номер телефона указан некорректно.\nПроверьте номер и попробуйте ещё раз."

    asyncio.run(handle_settings_profile_phone_input(_ctx(sender, user="user", text="+79990000007")))
    assert sender.sent[-1].text == "⚠️ Этот номер уже привязан к другому пользователю. Укажите другой телефон."

    asyncio.run(handle_settings_profile_phone_input(_ctx(sender, user="user", text="8 (999) 000-00-08")))
    assert "Новый телефон: +79990000008" in sender.sent[-1].text
    assert ("✅ Сохранить", SETTINGS_PROFILE_SAVE_PHONE_PAYLOAD) in _buttons(sender.sent[-1].keyboard)
    assert ("📱 Ввести заново", SETTINGS_PROFILE_RETRY_PHONE_PAYLOAD) in _buttons(sender.sent[-1].keyboard)

    asyncio.run(handle_settings_profile_save_phone(_ctx(sender, SETTINGS_PROFILE_SAVE_PHONE_PAYLOAD)))
    updated = UsersRepository(str(db)).find_by_platform_user_id("user")
    assert updated is not None
    assert updated.phone == "+79990000008"
    assert updated.first_name == original.first_name
    assert updated.display_name == original.display_name
    assert updated.birthdate == original.birthdate
    assert updated.role == original.role
    assert updated.yclients_client_id == original.yclients_client_id
    assert sender.sent[-2].text == "✅ Телефон обновлён"
    assert "Телефон: +79990000008" in sender.sent[-1].text

    asyncio.run(handle_settings_profile_edit_phone(_ctx(sender, SETTINGS_PROFILE_EDIT_PHONE_PAYLOAD)))
    asyncio.run(handle_settings_profile_phone_input(_ctx(sender, user="user", attachments=[{"type": "contact", "payload": {"phone_number": "+79990000009"}}])))
    assert "Новый телефон: +79990000009" in sender.sent[-1].text


def test_router_registration_and_scope_safety(db) -> None:
    router = Router()
    settings_flow.register_settings_routes(router)
    source = inspect.getsource(settings_flow.register_settings_routes)
    for payload_name in (
        "SETTINGS_PROFILE_PAYLOAD",
        "SETTINGS_PROFILE_EDIT_NAME_PAYLOAD",
        "SETTINGS_PROFILE_SAVE_NAME_PAYLOAD",
        "SETTINGS_PROFILE_EDIT_PHONE_PAYLOAD",
        "SETTINGS_PROFILE_SAVE_PHONE_PAYLOAD",
    ):
        assert payload_name in source
    assert "✨ Записаться" in [button.text for row in main_menu_keyboard("user").rows for button in row]
    target_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "max_barbershop_bot/flows/settings.py",
            "max_barbershop_bot/core/permissions.py",
            "max_barbershop_bot/repositories/users.py",
            "max_barbershop_bot/ui/buttons.py",
            "max_barbershop_bot/ui/texts.py",
        )
    )
    assert "from aiogram" not in target_sources
    assert "import aiogram" not in target_sources
    assert "role_onboarding" not in target_sources
