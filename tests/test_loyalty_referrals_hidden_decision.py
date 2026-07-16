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
from max_barbershop_bot.flows import create_router
from max_barbershop_bot.flows.menu import handle_nav_home
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository


@dataclass
class Sender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "loyalty-hidden.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    state._user_states.clear()
    return path


def _registered_user(db: Path, user_id: str) -> None:
    UsersRepository(str(db)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=user_id,
            max_user_id=user_id,
            chat_id=f"9{user_id}",
            first_name=f"Пользователь {user_id}",
            phone=f"+7999000{int(user_id):04d}",
            birthdate="1990-01-02",
        )
    )


def _context(user_id: str) -> tuple[RouterContext, Sender]:
    sender = Sender()
    event = NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user_id,
        max_user_id=user_id,
        chat_id=f"9{user_id}",
        text=None,
        callback_payload="nav:home",
        callback_id=f"home-{user_id}",
        first_name=f"Пользователь {user_id}",
    )
    return RouterContext(event=event, sender=sender), sender


def _keyboard_values(sender: Sender) -> tuple[list[str], list[str]]:
    keyboard = sender.messages[-1]["keyboard"]
    labels = [button.text for row in keyboard.rows for button in row]
    payloads = [str(button.payload or "") for row in keyboard.rows for button in row]
    return labels, payloads


def test_loyalty_hidden_decision_real_home_handler_has_no_loyalty_or_referral_for_any_role(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = (("601", "user"), ("602", "manager"), ("603", "admin"), ("604", "developer"))
    for user_id, role in roles:
        _registered_user(db, user_id)
        if role in {"manager", "admin"}:
            StaffRolesRepository(str(db)).assign_role(
                user_id,
                role,
                assigned_by_platform_user_id="decision-test",
                platform=PLATFORM_MAX,
            )
        monkeypatch.setenv("DEV_MAX_USER_ID", user_id if role == "developer" else "999999")
        context, sender = _context(user_id)

        asyncio.run(handle_nav_home(context))

        labels, payloads = _keyboard_values(sender)
        rendered = " ".join([*labels, *payloads]).lower()
        assert "лояль" not in rendered
        assert "рефера" not in rendered
        assert "loy:" not in rendered
        assert "referral" not in rendered
        assert sender.callbacks == [f"home-{user_id}"]
        assert state.get_current_screen(user_id, f"9{user_id}") == state.MAIN_MENU_SCREEN


def test_loyalty_hidden_decision_registers_no_placeholder_callbacks() -> None:
    router = create_router()
    registered = [*router._callback_handlers, *(prefix for prefix, _ in router._callback_prefix_handlers)]
    assert all("loy" not in payload.lower() and "referral" not in payload.lower() for payload in registered)
