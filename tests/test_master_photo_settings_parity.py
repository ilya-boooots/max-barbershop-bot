from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import booking, master_photos, menu, settings
from max_barbershop_bot.integrations.yclients.dto import YClientsStaff
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.services.master_photos import (
    MASTER_PHOTO_NON_PHOTO_TEXT,
    MASTER_PHOTOS_EMPTY_TEXT,
    MASTER_PHOTOS_LOAD_ERROR_TEXT,
    MASTER_PHOTOS_ROOT_TEXT,
    MasterPhotosLoadError,
    MasterPhotosService,
    MasterPhotoStaff,
)
from max_barbershop_bot.ui.buttons import (
    ADMIN_SETTINGS_PAYLOAD,
    MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX,
    MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX,
    MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX,
    MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX,
    NAV_HOME_PAYLOAD,
    SETTINGS_MASTER_PHOTOS_PAYLOAD,
)
from max_barbershop_bot.ui.texts import SETTINGS_NO_ACCESS_TEXT


@dataclass
class FakeSender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append(
            {"chat_id": chat_id, "text": text, "keyboard": keyboard, "attachments": attachments}
        )

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append(
            {"user_id": user_id, "text": text, "keyboard": keyboard, "attachments": attachments}
        )

    async def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "master-photos.sqlite3"
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
    payload: str | None,
    *,
    actor: str = "100",
    text: str | None = None,
    attachments: list[object] | None = None,
) -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent(
        update_type="message_callback" if payload is not None else "message_created",
        platform_user_id=actor,
        max_user_id=actor,
        chat_id="900",
        text=text,
        callback_payload=payload,
        callback_id=f"cb-{payload}" if payload is not None else None,
        attachments=attachments or [],
    )
    return RouterContext(event=event, sender=sender), sender


def _buttons(message: dict[str, object]):
    keyboard = message["keyboard"]
    return [button for row in keyboard.rows for button in row]


def _set_masters(actor: str, masters: list[MasterPhotoStaff]) -> None:
    state.set_state_data_value(actor, "900", master_photos._MASTER_PHOTOS_STATE_KEY, masters)


def _audit_count(db: Path, action: str) -> int:
    with sqlite3.connect(db) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM settings_audit_log WHERE action = ?",
                (action,),
            ).fetchone()[0]
        )


def _trace_photo_mutations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    mutations: list[str] = []
    original_connect = MasterPhotosRepository._connect

    def traced_connect(repository: MasterPhotosRepository) -> sqlite3.Connection:
        connection = original_connect(repository)

        def trace(statement: str) -> None:
            normalized = statement.lstrip().upper()
            if normalized.startswith(("INSERT INTO MASTER_PHOTOS", "UPDATE MASTER_PHOTOS")):
                mutations.append(statement)

        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(MasterPhotosRepository, "_connect", traced_connect)
    return mutations


def test_master_photo_list_real_handler_matches_roles_text_keyboard_and_empty_matrix(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = Router()
    master_photos.register_master_photos_routes(router)
    assert router._callback_handlers[SETTINGS_MASTER_PHOTOS_PAYLOAD] is master_photos.handle_master_photos_menu
    assert dict(router._callback_prefix_handlers)[MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX] is master_photos.handle_master_photos_page

    _user(db, "100")
    denied_context, denied_sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD)
    asyncio.run(master_photos.handle_master_photos_menu(denied_context))
    assert denied_sender.messages[-1]["text"] == SETTINGS_NO_ACCESS_TEXT

    async def list_two(self: MasterPhotosService) -> list[MasterPhotoStaff]:
        return [MasterPhotoStaff("11", "Анна"), MasterPhotoStaff("22", "Борис")]

    monkeypatch.setattr(MasterPhotosService, "list_yclients_masters", list_two)
    for actor, role in (("101", ROLE_ADMIN), ("102", ROLE_MANAGER)):
        _user(db, actor, role=role)
        context, sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD, actor=actor)
        asyncio.run(master_photos.handle_master_photos_menu(context))
        assert sender.messages[-1]["text"] == MASTER_PHOTOS_ROOT_TEXT
        assert [(button.text, button.payload) for button in _buttons(sender.messages[-1])] == [
            ("Анна", f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}11"),
            ("Борис", f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}22"),
            ("⬅️ Назад", ADMIN_SETTINGS_PAYLOAD),
            ("🏠 Главное меню", NAV_HOME_PAYLOAD),
        ]
        assert state.get_current_screen(actor, "900") == state.SETTINGS_MASTER_PHOTOS_SCREEN

    _user(db, "103")
    monkeypatch.setenv("DEV_MAX_USER_ID", "103")
    developer_context, developer_sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD, actor="103")
    asyncio.run(master_photos.handle_master_photos_menu(developer_context))
    assert developer_sender.messages[-1]["text"] == MASTER_PHOTOS_ROOT_TEXT

    async def list_empty(self: MasterPhotosService) -> list[MasterPhotoStaff]:
        return []

    monkeypatch.setattr(MasterPhotosService, "list_yclients_masters", list_empty)
    empty_context, empty_sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD, actor="101")
    asyncio.run(master_photos.handle_master_photos_menu(empty_context))
    assert empty_sender.messages[-1]["text"] == MASTER_PHOTOS_EMPTY_TEXT
    empty_buttons = _buttons(empty_sender.messages[-1])
    assert empty_buttons[-1].payload == NAV_HOME_PAYLOAD


def test_master_photo_service_matches_telegram_active_filter_name_fallback_and_sorting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PhotoRepository:
        def has_photo(self, staff_id: str) -> bool:
            return staff_id == "3"

    class SettingsRepository:
        def get_active(self) -> YClientsSettings:
            return YClientsSettings(company_id="company", partner_token="partner", user_token="user")

    class ClientContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class ServiceLayer:
        def __init__(self, client, *, company_id):
            assert company_id == "company"

        async def get_available_masters(self, *, company_id, bookable_only):
            assert company_id == "company"
            assert bookable_only is False
            return [
                YClientsStaff("2", "Борис", bookable=False),
                YClientsStaff("1", None, raw={"fullname": "Анна"}),
                YClientsStaff("3", None, raw={"title": "Вера"}),
                YClientsStaff("4", "Deleted", raw={"is_deleted": True}),
                YClientsStaff("5", "Inactive", raw={"active": False}),
                YClientsStaff("", "No id"),
            ]

    monkeypatch.setattr(
        "max_barbershop_bot.services.master_photos.build_yclients_client_from_active_settings",
        lambda settings: ClientContext(),
    )
    monkeypatch.setattr("max_barbershop_bot.services.master_photos.YClientsServiceLayer", ServiceLayer)
    service = MasterPhotosService(PhotoRepository(), SettingsRepository())

    result = asyncio.run(service.list_yclients_masters())

    assert [(item.yclients_staff_id, item.name, item.has_photo) for item in result] == [
        ("1", "Анна", False),
        ("2", "Борис", False),
        ("3", "Вера", True),
    ]


def test_master_photo_overflow_real_page_handler_preserves_every_master_without_truncation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    masters = [MasterPhotoStaff(str(index), f"Мастер {index:02d}") for index in range(35)]

    async def list_masters(self: MasterPhotosService) -> list[MasterPhotoStaff]:
        return masters

    monkeypatch.setattr(MasterPhotosService, "list_yclients_masters", list_masters)
    root_context, root_sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD)
    asyncio.run(master_photos.handle_master_photos_menu(root_context))
    first_buttons = _buttons(root_sender.messages[-1])
    assert len(root_sender.messages[-1]["keyboard"].rows) == 30
    assert [button.text for button in first_buttons[:27]] == [item.name for item in masters[:27]]
    assert any(button.payload == f"{MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX}1" for button in first_buttons)

    page_context, page_sender = _context(f"{MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX}1")
    asyncio.run(master_photos.handle_master_photos_page(page_context))
    second_buttons = _buttons(page_sender.messages[-1])
    assert [button.text for button in second_buttons[:8]] == [item.name for item in masters[27:]]
    assert {button.payload for button in first_buttons + second_buttons if button.payload.startswith(MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX)} == {
        f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}{item.yclients_staff_id}" for item in masters
    }
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTOS_SCREEN

    stale_context, stale_sender = _context(f"{MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX}999")
    asyncio.run(master_photos.handle_master_photos_page(stale_context))
    assert stale_sender.messages[0]["text"] == "Не удалось найти мастера 🙂"
    assert stale_sender.messages[-1]["text"] == MASTER_PHOTOS_ROOT_TEXT
    assert state.get_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY) is None


def test_master_photo_select_real_handler_uses_exact_id_and_rejects_stale_without_substitution(
    db: Path,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    masters = [MasterPhotoStaff("11", "Первый"), MasterPhotoStaff("22", "Второй")]
    _set_masters("100", masters)
    context, sender = _context(f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}22")
    asyncio.run(master_photos.handle_master_photo_select(context))
    assert sender.messages[-1]["text"] == "🖼️ Для мастера Второй фото пока не загружено"
    assert state.get_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY) == "22"

    stale_context, stale_sender = _context(f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}missing")
    asyncio.run(master_photos.handle_master_photo_select(stale_context))
    assert stale_sender.messages[0]["text"] == "Не удалось найти мастера 🙂"
    assert state.get_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY) == "22"


def test_master_photo_detail_real_handler_matches_text_attachment_and_all_actions(db: Path) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    MasterPhotosRepository(str(db)).upsert_photo(
        "22",
        "Второй",
        photo_file_id="max-image-token",
        actor_platform_user_id="100",
    )
    master = MasterPhotoStaff("22", "Второй", has_photo=True)
    _set_masters("100", [master])
    context, sender = _context(f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}22")
    asyncio.run(master_photos.handle_master_photo_select(context))

    message = sender.messages[-1]
    assert message["text"] == "🖼️ Фото мастера: Второй\nМожно заменить или удалить фото"
    assert message["attachments"] == [{"type": "image", "payload": {"token": "max-image-token"}}]
    assert [(button.text, button.payload) for button in _buttons(message)] == [
        ("📤 Загрузить / заменить фото", f"{MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX}22"),
        ("🗑️ Удалить фото", f"{MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX}22"),
        ("⬅️ Назад", SETTINGS_MASTER_PHOTOS_PAYLOAD),
        ("🏠 Главное меню", NAV_HOME_PAYLOAD),
    ]
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN


def test_master_photo_upload_real_handlers_validate_photo_and_mutate_once(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    master = MasterPhotoStaff("22", "Второй")
    _set_masters("100", [master])
    state.set_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY, "22")
    state.set_state_data_value("100", "900", master_photos._SELECTED_MASTER_NAME_STATE_KEY, "Второй")

    start_context, start_sender = _context(f"{MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX}22")
    asyncio.run(master_photos.handle_master_photo_upload_start(start_context))
    assert start_sender.messages[-1]["text"] == "📸 Отправьте одно фото для мастера Второй 😊"
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN

    wrong_context, wrong_sender = _context(f"{MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX}11")
    asyncio.run(master_photos.handle_master_photo_upload_start(wrong_context))
    assert wrong_sender.messages[-1]["text"] == "Сначала выберите мастера 🙂"

    non_photo_context, non_photo_sender = _context(None, attachments=[{"type": "file", "payload": {"token": "x"}}])
    asyncio.run(master_photos.handle_master_photo_upload_receive(non_photo_context))
    assert non_photo_sender.messages[-1]["text"] == MASTER_PHOTO_NON_PHOTO_TEXT

    mutations = _trace_photo_mutations(monkeypatch)
    image = {"type": "image", "payload": {"token": "photo-token"}}
    for _ in range(2):
        upload_context, upload_sender = _context(None, attachments=[image])
        asyncio.run(master_photos.handle_master_photo_upload_receive(upload_context))
        assert upload_sender.messages[0]["text"] == "✅ Фото мастера обновлено"
        assert upload_sender.messages[-1]["attachments"] == [image]

    assert len(mutations) == 1
    assert _audit_count(db, "master_photo_changed") == 1


def test_master_photo_delete_real_handler_is_immediate_idempotent_and_keeps_detail(db: Path, monkeypatch) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    repository = MasterPhotosRepository(str(db))
    repository.upsert_photo("22", "Второй", photo_file_id="photo-token", actor_platform_user_id="100")
    master = MasterPhotoStaff("22", "Второй", has_photo=True)
    _set_masters("100", [master])
    state.set_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY, "22")
    state.set_state_data_value("100", "900", master_photos._SELECTED_MASTER_NAME_STATE_KEY, "Второй")
    mutations = _trace_photo_mutations(monkeypatch)

    for _ in range(2):
        context, sender = _context(f"{MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX}22")
        asyncio.run(master_photos.handle_master_photo_delete_start(context))
        assert sender.messages[0]["text"] == "🗑️ Фото мастера удалено"
        assert sender.messages[-1]["text"] == "🖼️ Для мастера Второй фото пока не загружено"
        assert any(button.text == "🗑️ Удалить фото" for button in _buttons(sender.messages[-1]))

    assert len(mutations) == 1
    assert repository.get_by_staff_id("22") is None
    assert _audit_count(db, "master_photo_deleted") == 1
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN


def test_master_photo_back_and_global_home_real_handlers_restore_expected_screens(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    settings_context, settings_sender = _context(ADMIN_SETTINGS_PAYLOAD)
    asyncio.run(settings.handle_settings_menu(settings_context))
    assert state.get_current_screen("100", "900") == state.SETTINGS_MENU_SCREEN
    assert settings_sender.callbacks == [f"cb-{ADMIN_SETTINGS_PAYLOAD}"]

    called: list[str] = []

    async def fake_show_home(context: RouterContext) -> None:
        called.append("home")
        state.clear_user_state("100", "900")
        state.set_current_screen("100", "900", state.MAIN_MENU_SCREEN)

    monkeypatch.setattr(menu, "show_home", fake_show_home)
    home_context, home_sender = _context(NAV_HOME_PAYLOAD)
    asyncio.run(menu.handle_nav_home(home_context))
    assert called == ["home"]
    assert home_sender.callbacks == [f"cb-{NAV_HOME_PAYLOAD}"]
    assert state.get_current_screen("100", "900") == state.MAIN_MENU_SCREEN


def test_booking_dates_real_renderer_uses_stored_master_photo_and_branch_timezone(db: Path) -> None:
    _user(db, "100")
    MasterPhotosRepository(str(db)).upsert_photo(
        "22",
        "Второй",
        photo_file_id="booking-photo-token",
        actor_platform_user_id="100",
    )
    state.set_state_data_value("100", "900", booking._SELECTED_MASTER_STATE_KEY, "22")
    context, sender = _context(None)

    asyncio.run(
        booking._show_dates(
            context,
            [date(2026, 7, 16)],
            timezone_name="Europe/Saratov",
        )
    )

    assert sender.messages[-1]["attachments"] == [
        {"type": "image", "payload": {"token": "booking-photo-token"}}
    ]
    assert state.get_current_screen("100", "900") == state.BOOKING_DATES_SCREEN
    assert any("16" in button.text for button in _buttons(sender.messages[-1]))


def test_master_photo_storage_error_real_handler_masks_diagnostics_without_mutation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    master = MasterPhotoStaff("22", "Второй")
    _set_masters("100", [master])
    state.set_state_data_value("100", "900", master_photos._SELECTED_STAFF_ID_STATE_KEY, "22")
    state.set_state_data_value("100", "900", master_photos._SELECTED_MASTER_NAME_STATE_KEY, "Второй")

    def fail(self, *args, **kwargs):
        raise sqlite3.OperationalError("token=SECRET raw_response=private")

    monkeypatch.setattr(MasterPhotosRepository, "upsert_photo", fail)
    context, sender = _context(None, attachments=[{"type": "image", "payload": {"token": "photo-token"}}])
    asyncio.run(master_photos.handle_master_photo_upload_receive(context))
    visible = " ".join(str(message["text"]) for message in sender.messages)
    assert visible == "⚠️ Не удалось сохранить фото мастера. Попробуйте ещё раз."
    assert "SECRET" not in visible and "raw_response" not in visible
    assert _audit_count(db, "master_photo_changed") == 0


def test_master_photo_detail_storage_error_real_handler_masks_diagnostics_and_keeps_actions(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)
    _set_masters("100", [MasterPhotoStaff("22", "Второй")])

    def fail(self, staff_id: str):
        raise sqlite3.OperationalError("token=SECRET traceback=private")

    monkeypatch.setattr(MasterPhotosRepository, "get_by_staff_id", fail)
    context, sender = _context(f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}22")
    asyncio.run(master_photos.handle_master_photo_select(context))
    message = sender.messages[-1]
    assert message["text"] == "⚠️ Не удалось загрузить фото мастера. Попробуйте ещё раз."
    assert "SECRET" not in str(message) and "traceback" not in str(message)
    assert [button.text for button in _buttons(message)][:2] == [
        "📤 Загрузить / заменить фото",
        "🗑️ Удалить фото",
    ]
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN


def test_master_photo_load_error_real_handler_is_masked_and_keeps_navigation(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user(db, "100", role=ROLE_ADMIN)

    async def fail(self: MasterPhotosService) -> list[MasterPhotoStaff]:
        raise MasterPhotosLoadError(MASTER_PHOTOS_LOAD_ERROR_TEXT)

    monkeypatch.setattr(MasterPhotosService, "list_yclients_masters", fail)
    context, sender = _context(SETTINGS_MASTER_PHOTOS_PAYLOAD)
    asyncio.run(master_photos.handle_master_photos_menu(context))
    assert sender.messages[-1]["text"] == MASTER_PHOTOS_LOAD_ERROR_TEXT
    assert state.get_current_screen("100", "900") == state.SETTINGS_MASTER_PHOTOS_SCREEN
    assert _buttons(sender.messages[-1])[-1].payload == NAV_HOME_PAYLOAD
