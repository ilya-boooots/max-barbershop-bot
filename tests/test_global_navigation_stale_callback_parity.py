from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import antiflood, state
from max_barbershop_bot.core.config import Config
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.payloads import CallbackPayloadError
from max_barbershop_bot.core.router import Router
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows.booking import register_booking_routes
from max_barbershop_bot.flows.broadcasts import register_broadcast_routes
from max_barbershop_bot.flows.fallback import handle_unknown_callback
from max_barbershop_bot.flows.menu import register_menu_routes
from max_barbershop_bot.flows.settings import register_settings_routes
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import (
    BOOKING_BACK_PAYLOAD,
    BROADCAST_BACK_PAYLOAD,
    BROADCAST_HOME_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    SETTINGS_HOME_PAYLOAD,
)


USER_ID = "501"
CHAT_ID = "9501"
STALE_STATE_TEXT = "⚠️ Данные шага устарели. Пожалуйста, начните заново."
MAIN_MENU_TEXT = "✨ Иван, выберите действие в меню ниже 👇"


@dataclass
class Sender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "navigation.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    UsersRepository(str(path)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=USER_ID,
            max_user_id=USER_ID,
            chat_id=CHAT_ID,
            first_name="Иван",
            last_name="Иванов",
            phone="+79991234567",
            birthdate="1990-01-02",
        )
    )
    state._user_states.clear()
    antiflood._callback_hits.clear()
    return path


def _event(payload: str, *, callback_id: str = "cb") -> NormalizedEvent:
    return NormalizedEvent(
        update_type="message_callback",
        platform_user_id=USER_ID,
        max_user_id=USER_ID,
        chat_id=CHAT_ID,
        text=None,
        callback_payload=payload,
        callback_id=callback_id,
        first_name="Иван",
    )


def _router(db: Path) -> Router:
    return Router(Config(max_bot_token="unused", database_path=str(db)))


def _prepare_root(screen: str) -> None:
    state.set_current_screen(USER_ID, CHAT_ID, screen)
    state.push_screen(USER_ID, CHAT_ID, state.MAIN_MENU_SCREEN)
    state.set_state_data_value(USER_ID, CHAT_ID, "owned_draft", "clear-on-home")


@pytest.mark.parametrize(
    ("screen", "payload", "home_payload", "register"),
    [
        (state.BOOKING_HUB_SCREEN, BOOKING_BACK_PAYLOAD, NAV_HOME_PAYLOAD, register_booking_routes),
        (state.CONTACTS_SCREEN, NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD, register_menu_routes),
        (state.SUPPORT_SCREEN, NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD, register_menu_routes),
        (state.SETTINGS_MENU_SCREEN, SETTINGS_BACK_PAYLOAD, SETTINGS_HOME_PAYLOAD, register_settings_routes),
        (state.MY_BOOKINGS_SCREEN, NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD, register_menu_routes),
        (state.BROADCAST_MENU_SCREEN, BROADCAST_BACK_PAYLOAD, BROADCAST_HOME_PAYLOAD, register_broadcast_routes),
    ],
)
def test_core_root_back_and_home_real_handlers_match_telegram_matrix(
    db: Path,
    screen: str,
    payload: str,
    home_payload: str,
    register,
) -> None:
    router = _router(db)
    register(router)
    sender = Sender()
    _prepare_root(screen)

    asyncio.run(router.dispatch(_event(payload), sender))

    assert sender.callbacks == ["cb"]
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MAIN_MENU_SCREEN
    assert sender.messages[-1]["text"] == MAIN_MENU_TEXT
    assert sender.messages[-1]["keyboard"] is not None

    antiflood._callback_hits.clear()
    state.set_current_screen(USER_ID, CHAT_ID, screen)
    state.set_state_data_value(USER_ID, CHAT_ID, "owned_draft", "clear-on-home")
    asyncio.run(router.dispatch(_event(home_payload, callback_id="home"), sender))
    assert sender.callbacks[-1] == "home"
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MAIN_MENU_SCREEN
    assert state.get_state_data_value(USER_ID, CHAT_ID, "owned_draft") is None


def test_unknown_and_unsafe_stale_callbacks_use_real_fallback_and_return_role_menu(db: Path) -> None:
    router = _router(db)
    register_menu_routes(router)
    router.on_unknown_callback(handle_unknown_callback)
    selected: list[str] = []

    async def unsafe_prefix_handler(_context):
        selected.append("wrong")

    router.on_callback_prefix("broadcast:section:", unsafe_prefix_handler)
    _prepare_root(state.BROADCAST_MENU_SCREEN)
    sender = Sender()

    asyncio.run(router.dispatch(_event("broadcast:section:lost clients"), sender))

    assert selected == []
    assert sender.callbacks == ["cb"]
    assert [message["text"] for message in sender.messages] == [
        STALE_STATE_TEXT,
        MAIN_MENU_TEXT,
    ]
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MAIN_MENU_SCREEN
    assert state.get_state_data_value(USER_ID, CHAT_ID, "owned_draft") is None


def test_callback_handler_stale_lookup_failure_uses_real_router_safety_once(db: Path) -> None:
    router = _router(db)
    register_menu_routes(router)
    router.on_unknown_callback(handle_unknown_callback)
    mutations: list[str] = []

    async def stale_handler(_context):
        empty: list[str] = []
        empty[2]
        mutations.append("must-not-run")

    router.on_callback("test:stale", stale_handler)
    sender = Sender()
    for callback_id in ("first", "repeat"):
        antiflood._callback_hits.clear()
        asyncio.run(router.dispatch(_event("test:stale", callback_id=callback_id), sender))

    assert mutations == []
    assert sender.callbacks == ["first", "repeat"]
    assert [message["text"] for message in sender.messages].count(STALE_STATE_TEXT) == 2
    assert state.get_current_screen(USER_ID, CHAT_ID) == state.MAIN_MENU_SCREEN


def test_callback_payload_contract_rejects_unsafe_registration_and_never_selects_prefix_handler(
    db: Path,
) -> None:
    router = _router(db)

    async def handler(_context):
        return None

    with pytest.raises(CallbackPayloadError):
        router.on_callback("contains a space", handler)
    with pytest.raises(CallbackPayloadError):
        router.on_callback_prefix("x" * 65, handler)

    register_menu_routes(router)
    register_booking_routes(router)
    register_broadcast_routes(router)
    register_settings_routes(router)
