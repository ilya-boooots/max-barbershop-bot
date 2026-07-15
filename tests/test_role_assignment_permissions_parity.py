from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.permissions import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGER,
    can_assign_role,
)
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows.staff import handle_assign_role
from max_barbershop_bot.repositories.audit_log import AuditLogEntry, AuditLogRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.ui.buttons import (
    STAFF_ASSIGN_ADMIN_PAYLOAD,
    STAFF_ASSIGN_DEVELOPER_PAYLOAD,
    STAFF_ASSIGN_MANAGER_PAYLOAD,
    staff_role_assign_keyboard,
)
from max_barbershop_bot.ui.texts import (
    STAFF_NO_ACCESS_TEXT,
    STAFF_PROTECTED_DEVELOPER_ROLE_TEXT,
    STAFF_ROLE_ASSIGNED_TEXT,
)


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id: str, text: str, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id: str, text: str, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "role-assignment.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEV_MAX_USER_ID", "dev")
    state._user_states.clear()
    return path


def _ctx(payload: str, *, actor: str) -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent("message_callback", actor, actor, "900", None, payload, f"cb-{payload}")
    return RouterContext(event, sender), sender


def _user(db: Path, uid: str, *, role: str = "user") -> None:
    UsersRepository(str(db)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=uid,
            max_user_id=uid,
            chat_id="900",
            display_name=uid.title(),
            role=role,
        )
    )


def _assign_direct(db: Path, uid: str, role: str, *, actor: str = "setup") -> None:
    StaffRolesRepository(str(db)).assign_role(
        uid,
        role,
        assigned_by_platform_user_id=actor,
        platform=PLATFORM_MAX,
    )


def _select_target(actor: str, target: str) -> None:
    state.set_state_data_value(actor, "900", "target_platform_user_id", target)
    state.set_state_data_value(actor, "900", "target_display_name", target.title())


def _role_events(db: Path, event_type: str, target: str) -> list[AuditLogEntry]:
    return [
        entry
        for entry in AuditLogRepository(str(db)).list_recent(limit=100)
        if entry.event_type == event_type and entry.target_platform_user_id == target
    ]


def _button_payloads(role: str) -> list[str]:
    keyboard = staff_role_assign_keyboard(role)
    return [button.payload or "" for row in keyboard.rows for button in row]


def test_visible_telegram_assignment_matrix() -> None:
    assert can_assign_role(ROLE_DEVELOPER, ROLE_ADMIN)
    assert can_assign_role(ROLE_DEVELOPER, ROLE_MANAGER)
    assert not can_assign_role(ROLE_DEVELOPER, ROLE_DEVELOPER)
    assert can_assign_role(ROLE_MANAGER, ROLE_ADMIN)
    assert not can_assign_role(ROLE_MANAGER, ROLE_MANAGER)
    assert not can_assign_role(ROLE_MANAGER, ROLE_DEVELOPER)
    assert not can_assign_role(ROLE_ADMIN, ROLE_ADMIN)
    assert not can_assign_role(ROLE_ADMIN, ROLE_MANAGER)
    assert _button_payloads(ROLE_DEVELOPER)[:2] == [
        STAFF_ASSIGN_ADMIN_PAYLOAD,
        STAFF_ASSIGN_MANAGER_PAYLOAD,
    ]
    assert STAFF_ASSIGN_DEVELOPER_PAYLOAD not in _button_payloads(ROLE_DEVELOPER)
    assert _button_payloads(ROLE_MANAGER)[0] == STAFF_ASSIGN_ADMIN_PAYLOAD
    assert STAFF_ASSIGN_MANAGER_PAYLOAD not in _button_payloads(ROLE_MANAGER)


def test_real_handler_manager_assigns_admin_and_writes_audit(db: Path) -> None:
    _user(db, "manager")
    _user(db, "target")
    _assign_direct(db, "manager", ROLE_MANAGER)
    _select_target("manager", "target")
    before = len(_role_events(db, "role_assigned", "target"))

    context, sender = _ctx(STAFF_ASSIGN_ADMIN_PAYLOAD, actor="manager")
    asyncio.run(handle_assign_role(context))

    repository = StaffRolesRepository(str(db))
    assert repository.get_roles("target") == [ROLE_ADMIN]
    assert UsersRepository(str(db)).find_by_platform_user_id("target").role == ROLE_ADMIN
    events = _role_events(db, "role_assigned", "target")
    assert len(events) == before + 1
    assert events[0].actor_platform_user_id == "manager"
    assert events[0].old_role == "user"
    assert events[0].new_role == ROLE_ADMIN
    assert sender.callbacks == [f"cb-{STAFF_ASSIGN_ADMIN_PAYLOAD}"]
    assert any(text == STAFF_ROLE_ASSIGNED_TEXT for text, _ in sender.messages)


def test_real_handler_replaces_existing_role_and_writes_old_new_audit(db: Path) -> None:
    _user(db, "dev")
    _user(db, "target")
    _assign_direct(db, "dev", ROLE_DEVELOPER)
    _assign_direct(db, "target", ROLE_MANAGER)
    _select_target("dev", "target")

    context, _ = _ctx(STAFF_ASSIGN_ADMIN_PAYLOAD, actor="dev")
    asyncio.run(handle_assign_role(context))

    assert StaffRolesRepository(str(db)).get_roles("target") == [ROLE_ADMIN]
    event = _role_events(db, "role_assigned", "target")[0]
    assert event.actor_platform_user_id == "dev"
    assert event.old_role == ROLE_MANAGER
    assert event.new_role == ROLE_ADMIN


@pytest.mark.parametrize(
    ("actor", "actor_role", "payload", "new_role"),
    [
        ("manager", ROLE_MANAGER, STAFF_ASSIGN_MANAGER_PAYLOAD, ROLE_MANAGER),
        ("dev", ROLE_DEVELOPER, STAFF_ASSIGN_DEVELOPER_PAYLOAD, ROLE_DEVELOPER),
    ],
)
def test_real_handler_denies_roles_hidden_by_visible_matrix_and_audits(
    db: Path,
    actor: str,
    actor_role: str,
    payload: str,
    new_role: str,
) -> None:
    _user(db, actor)
    _user(db, "target")
    _assign_direct(db, actor, actor_role)
    _select_target(actor, "target")

    context, sender = _ctx(payload, actor=actor)
    asyncio.run(handle_assign_role(context))

    assert StaffRolesRepository(str(db)).get_roles("target") == []
    event = _role_events(db, "role_change_blocked", "target")[0]
    assert event.actor_platform_user_id == actor
    assert event.new_role == new_role
    assert sender.callbacks == [f"cb-{payload}"]
    assert sender.messages == [(STAFF_NO_ACCESS_TEXT, None)]


def test_real_handler_denies_admin_assignment_without_mutation(db: Path) -> None:
    _user(db, "admin")
    _user(db, "target")
    _assign_direct(db, "admin", ROLE_ADMIN)
    _select_target("admin", "target")

    context, sender = _ctx(STAFF_ASSIGN_MANAGER_PAYLOAD, actor="admin")
    asyncio.run(handle_assign_role(context))

    assert StaffRolesRepository(str(db)).get_roles("target") == []
    event = _role_events(db, "role_change_blocked", "target")[0]
    assert event.actor_platform_user_id == "admin"
    assert sender.messages == [(STAFF_NO_ACCESS_TEXT, None)]


def test_real_handler_blocks_protected_developer_and_audits_attempt(db: Path) -> None:
    _user(db, "dev")
    _assign_direct(db, "dev", ROLE_DEVELOPER)
    _select_target("dev", "dev")

    context, sender = _ctx(STAFF_ASSIGN_ADMIN_PAYLOAD, actor="dev")
    asyncio.run(handle_assign_role(context))

    assert StaffRolesRepository(str(db)).get_roles("dev") == [ROLE_DEVELOPER]
    event = _role_events(db, "protected_developer_role_change_blocked", "dev")[0]
    assert event.actor_platform_user_id == "dev"
    assert event.old_role == ROLE_DEVELOPER
    assert event.new_role == ROLE_ADMIN
    assert sender.messages == [(STAFF_PROTECTED_DEVELOPER_ROLE_TEXT, None)]


def test_repeated_real_handler_callback_mutates_once(db: Path) -> None:
    _user(db, "manager")
    _user(db, "target")
    _assign_direct(db, "manager", ROLE_MANAGER)
    _select_target("manager", "target")
    context, _ = _ctx(STAFF_ASSIGN_ADMIN_PAYLOAD, actor="manager")
    before = len(_role_events(db, "role_assigned", "target"))

    asyncio.run(handle_assign_role(context))
    after_first = len(_role_events(db, "role_assigned", "target"))
    asyncio.run(handle_assign_role(context))
    after_second = len(_role_events(db, "role_assigned", "target"))

    assert after_first == before + 1
    assert after_second == after_first
    assert StaffRolesRepository(str(db)).get_roles("target") == [ROLE_ADMIN]
