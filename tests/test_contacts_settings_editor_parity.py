from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.permissions import ROLE_ADMIN
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import settings
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.repositories.audit_log import AuditLogRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactInfo, ContactsService
from max_barbershop_bot.ui.buttons import (
    SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD,
    SETTINGS_CONTACTS_MAP_HIDE_PREFIX,
    SETTINGS_CONTACTS_PAYLOAD,
    SETTINGS_CONTACTS_PREVIEW_PAYLOAD,
    SETTINGS_CONTACTS_RESET_PAYLOAD,
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
    path = tmp_path / "contacts-settings.sqlite3"
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
    payload: str | None = SETTINGS_CONTACTS_PAYLOAD,
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


def _button_texts(keyboard: MaxInlineKeyboard) -> list[str]:
    return [button.text for row in keyboard.rows for button in row]


async def _resolved_contacts(self: ContactsService) -> ContactInfo:
    return ContactInfo(
        address="Адрес из YClients",
        phone="+7 999 000-00-00",
        schedule="10:00–22:00",
        source="yclients",
    )


def test_contacts_editor_routes_bind_real_handlers() -> None:
    router = Router()
    settings.register_settings_routes(router)

    assert router._callback_handlers[SETTINGS_CONTACTS_PAYLOAD] is settings.handle_settings_contacts  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD] is settings.handle_settings_contacts_edit_address  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_CONTACTS_PREVIEW_PAYLOAD] is settings.handle_settings_contacts_preview  # noqa: SLF001
    assert router._callback_handlers[SETTINGS_CONTACTS_RESET_PAYLOAD] is settings.handle_settings_contacts_reset  # noqa: SLF001


def test_contacts_editor_role_gate_invokes_real_handler(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100")
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    context, sender = _context()

    asyncio.run(settings.handle_settings_contacts(context))

    assert sender.callbacks == [f"cb-{SETTINGS_CONTACTS_PAYLOAD}"]
    assert sender.messages == [(SETTINGS_NO_ACCESS_TEXT, None)]
    assert state.get_current_screen("100", "900") != state.SETTINGS_CONTACTS_SCREEN


def test_contacts_editor_visible_matrix_and_current_screen(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    context, sender = _context()

    asyncio.run(settings.handle_settings_contacts(context))

    text, keyboard = sender.messages[-1]
    assert state.get_current_screen("100", "900") == state.SETTINGS_CONTACTS_SCREEN
    assert text.startswith("✏️ Редактирование контактов")
    assert _button_texts(keyboard) == [
        "🏠 Изменить адрес",
        "📞 Изменить телефон",
        "⏰ Изменить режим работы",
        "🗺 Яндекс Карты",
        "🗺 2GIS",
        "🗺 Google Maps",
        "♻️ Сбросить к данным YClients",
        "👁️ Предпросмотр",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_contact_field_handler_trims_persists_and_allows_fallback_clear(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    repository = YClientsSettingsRepository(str(db))
    repository.create_settings(
        company_id="company",
        partner_token=None,
        user_token=None,
        contacts_override_json='{"address": "Старый адрес", "phone": "+7 111"}',
    )
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)

    context, sender = _context(payload=None, text="  Новый адрес  ")
    asyncio.run(settings.handle_settings_contacts_address_input(context))
    assert repository.get_contacts_override()["address"] == "Новый адрес"
    assert sender.messages[0][0] == "✅ Контакты обновлены"

    context, _ = _context(payload=None, text="   ")
    asyncio.run(settings.handle_settings_contacts_address_input(context))
    assert "address" not in repository.get_contacts_override()
    before = len(
        [entry for entry in AuditLogRepository(str(db)).list_recent(limit=100) if entry.event_type == "contacts_override_address_updated"]
    )
    asyncio.run(settings.handle_settings_contacts_address_input(context))
    after = len(
        [entry for entry in AuditLogRepository(str(db)).list_recent(limit=100) if entry.event_type == "contacts_override_address_updated"]
    )
    assert after == before


def test_contacts_preview_handler_uses_resolved_values_without_raw_errors(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    context, sender = _context(payload=SETTINGS_CONTACTS_PREVIEW_PAYLOAD)

    asyncio.run(settings.handle_settings_contacts_preview(context))

    text, keyboard = sender.messages[-1]
    assert state.get_current_screen("100", "900") == state.SETTINGS_CONTACTS_SCREEN
    assert "🏠 Адрес: Адрес из YClients" in text
    assert "📞 Телефон: +7 999 000-00-00" in text
    assert "None" not in text
    assert not any(marker in text for marker in ("Traceback", "token", "raw_response"))
    assert "👁️ Предпросмотр" in _button_texts(keyboard)


def test_contacts_reset_handler_clears_fields_and_map_overrides_once(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    repository = YClientsSettingsRepository(str(db))
    repository.create_settings(
        company_id="company",
        partner_token=None,
        user_token=None,
        contacts_override_json='{"address": "Локальный адрес"}',
        yandex_maps_url="https://yandex.example/map",
        yandex_maps_enabled=False,
        twogis_url="https://2gis.example/map",
        google_maps_url="https://google.example/map",
    )
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    calls = 0
    original_update = YClientsSettingsRepository.update_settings

    def counted_update(self: YClientsSettingsRepository, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_update(self, *args, **kwargs)

    monkeypatch.setattr(YClientsSettingsRepository, "update_settings", counted_update)
    context, sender = _context(payload=SETTINGS_CONTACTS_RESET_PAYLOAD)

    asyncio.run(settings.handle_settings_contacts_reset(context))
    asyncio.run(settings.handle_settings_contacts_reset(context))

    active = repository.get_active()
    assert active is not None
    assert active.contacts_override_json is None
    assert active.yandex_maps_url is None and active.yandex_maps_enabled
    assert active.twogis_url is None and active.twogis_enabled
    assert active.google_maps_url is None and active.google_maps_enabled
    assert calls == 1
    assert sum(text.startswith("♻️ Локальные правки") for text, _ in sender.messages) == 1


def test_map_handlers_gate_access_and_reject_stale_without_mutation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100")
    repository = YClientsSettingsRepository(str(db))
    repository.create_settings(
        company_id="company",
        partner_token=None,
        user_token=None,
        yandex_maps_url="https://yandex.example/map",
        yandex_maps_enabled=True,
    )
    context, sender = _context(payload=f"{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}yandex")
    asyncio.run(settings.handle_settings_contacts_map_hide(context))
    assert repository.get_contacts_override()["yandex_maps_enabled"] is True
    assert sender.messages == [(SETTINGS_NO_ACCESS_TEXT, None)]

    _user(db, "200", role=ROLE_ADMIN)
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    stale, stale_sender = _context(actor="200", payload=f"{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}unknown")
    asyncio.run(settings.handle_settings_contacts_map_hide(stale))
    assert repository.get_contacts_override()["yandex_maps_enabled"] is True
    assert stale_sender.callbacks == [f"cb-{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}unknown"]
    assert state.get_current_screen("200", "900") == state.SETTINGS_CONTACTS_SCREEN


def test_map_mutations_are_idempotent(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    repository = YClientsSettingsRepository(str(db))
    repository.create_settings(
        company_id="company",
        partner_token=None,
        user_token=None,
        yandex_maps_url="https://yandex.example/map",
        yandex_maps_enabled=True,
    )
    calls = 0
    original_set = YClientsSettingsRepository.set_contacts_override

    def counted_set(self: YClientsSettingsRepository, override: dict[str, object]):
        nonlocal calls
        calls += 1
        return original_set(self, override)

    monkeypatch.setattr(YClientsSettingsRepository, "set_contacts_override", counted_set)
    context, sender = _context(payload=f"{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}yandex")
    asyncio.run(settings.handle_settings_contacts_map_hide(context))
    asyncio.run(settings.handle_settings_contacts_map_hide(context))

    assert repository.get_contacts_override()["yandex_maps_enabled"] is False
    assert calls == 1
    assert sender.callbacks == [
        f"cb-{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}yandex",
        f"cb-{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}yandex",
    ]


def test_contacts_back_and_home_handlers_restore_expected_screens(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    monkeypatch.setattr(ContactsService, "get_contacts", _resolved_contacts)
    state.set_current_screen("100", "900", state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN)
    back, _ = _context(payload="settings:back")
    asyncio.run(settings.handle_settings_back(back))
    assert state.get_current_screen("100", "900") == state.SETTINGS_CONTACTS_SCREEN

    home, _ = _context(payload="settings:home")
    asyncio.run(settings.handle_settings_home(home))
    assert state.get_current_screen("100", "900") == state.MAIN_MENU_SCREEN


def test_contacts_repository_error_is_masked_and_keeps_input_screen(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    state.set_current_screen("100", "900", state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN)

    def fail(self: YClientsSettingsRepository, override: dict[str, object]):
        raise RuntimeError("partner_token=SECRET raw_response=private")

    monkeypatch.setattr(YClientsSettingsRepository, "set_contacts_override", fail)
    context, sender = _context(payload=None, text="Новый адрес")
    asyncio.run(settings.handle_settings_contacts_address_input(context))

    text, keyboard = sender.messages[-1]
    assert text == "⚠️ Не удалось сохранить настройки контактов. Попробуйте ещё раз."
    assert "SECRET" not in text and "raw_response" not in text
    assert state.get_current_screen("100", "900") == state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN
    assert _button_texts(keyboard) == ["⬅️ Назад", "🏠 Главное меню"]
