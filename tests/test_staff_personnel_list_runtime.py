from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows.staff import handle_staff_card, handle_staff_list
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.ui.buttons import STAFF_CARD_PAYLOAD_PREFIX, STAFF_LIST_PAYLOAD


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id: int, text: str, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id: int, text: str, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "staff.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEV_MAX_USER_ID", "dev")
    state._user_states.clear()
    return path


def _ctx(payload: str, user: str = "actor") -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent("message_callback", user, user, "900", None, payload, f"cb-{payload}")
    return RouterContext(event, sender), sender


def _user(db: Path, uid: str, **kwargs):
    return UsersRepository(str(db)).create(UserCreate(platform=PLATFORM_MAX, platform_user_id=uid, max_user_id=uid, chat_id="900", **kwargs))


def _assign(db: Path, uid: str, role: str, by: str | None = "issuer") -> None:
    StaffRolesRepository(str(db)).assign_role(uid, role, assigned_by_platform_user_id=by)


def _set_role_dates(db: Path, uid: str, role: str, created: str, updated: str | None = None) -> None:
    with sqlite3.connect(db) as con:
        con.execute("UPDATE staff_roles SET created_at=?, updated_at=? WHERE platform=? AND platform_user_id=? AND role=?", (created, updated or created, PLATFORM_MAX, uid, role))


def _texts(sender: FakeSender) -> list[str]:
    return [text for text, _ in sender.messages]


def _buttons(keyboard) -> list[tuple[str, str]]:
    return [(button.text, button.payload or "") for row in keyboard.rows for button in row]


def test_access_matrix_user_denied_manager_admin_developer_allowed(db: Path) -> None:
    _user(db, "issuer", display_name="Owner")
    _user(db, "manager", display_name="Manager")
    _user(db, "admin", display_name="Admin")
    _user(db, "dev", display_name="Dev")
    _user(db, "staff", display_name="Secret Staff")
    _assign(db, "manager", "manager")
    _assign(db, "admin", "admin")
    _assign(db, "dev", "developer")
    _assign(db, "staff", "admin")

    denied_ctx, denied_sender = _ctx(STAFF_LIST_PAYLOAD, user="ordinary")
    asyncio.run(handle_staff_list(denied_ctx))
    assert denied_sender.callbacks == ["cb-staff:list"]
    assert _texts(denied_sender) == ["⛔️ Недостаточно прав."]
    assert "Secret Staff" not in _texts(denied_sender)[0]

    for actor in ["manager", "admin", "dev"]:
        ctx, sender = _ctx(STAFF_LIST_PAYLOAD, user=actor)
        asyncio.run(handle_staff_list(ctx))
        assert sender.callbacks == ["cb-staff:list"]
        assert "👥 Персонал салона" in _texts(sender)[0]
        assert "Secret Staff" in _texts(sender)[0]


def test_title_count_roles_duplicates_order_dates_issuer_and_no_extra_phone_username(db: Path) -> None:
    YClientsSettingsRepository(str(db)).create_settings(company_id="1", partner_token=None, user_token=None, branch_timezone="Europe/Moscow")
    _user(db, "issuer", display_name="Главный")
    _user(db, "dev", display_name="Dev User")
    _user(db, "admin", first_name="Admin First", username="admin_name", phone="+79991234567")
    _user(db, "manager", username="manager_name")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "admin", "manager", by="issuer")
    _assign(db, "admin", "admin", by="issuer")
    _assign(db, "manager", "manager", by=None)
    _set_role_dates(db, "dev", "developer", "2026-01-01T08:00:00+00:00")
    _set_role_dates(db, "admin", "admin", "2026-01-03T08:00:00+00:00")
    _set_role_dates(db, "admin", "manager", "2026-01-04T08:00:00+00:00")
    _set_role_dates(db, "manager", "manager", "2026-01-02T08:00:00+00:00")

    ctx, sender = _ctx(STAFF_LIST_PAYLOAD, user="dev")
    asyncio.run(handle_staff_list(ctx))
    text = _texts(sender)[0]
    assert text.startswith("👥 Персонал салона\nВсего: 3")
    assert "1) 💻 Разработчик" in text
    assert "2) 🛡 Администратор" in text
    assert "3) 👑 Управляющий" in text
    assert "👤 Admin First" in text
    assert "👤 manager_name" in text
    assert "📅 С 03.01.2026 11:00" in text
    assert "🛠 Выдал: Главный" in text
    assert "🛠 Выдал: Разработчик" in text
    assert "@admin_name" not in text
    assert "Телефон:" not in text
    assert "+79991234567" not in text
    keyboard = sender.messages[0][1]
    buttons = _buttons(keyboard)
    assert buttons[0] == ("👤 Dev User (💻 Разработчик)", f"{STAFF_CARD_PAYLOAD_PREFIX}dev")
    assert buttons[1] == ("👤 Admin First (🛡 Администратор)", f"{STAFF_CARD_PAYLOAD_PREFIX}admin")
    assert buttons[-2:] == [("⬅️ Назад", "nav:back"), ("🏠 Главное меню", "nav:home")]


def test_empty_state_navigation_and_current_screen(db: Path) -> None:
    _user(db, "dev", display_name="Protected actor")
    ctx, sender = _ctx(STAFF_LIST_PAYLOAD, user="dev")
    asyncio.run(handle_staff_list(ctx))
    assert _texts(sender)[0] == "👥 Персонал салона\nВсего: 0"
    buttons = _buttons(sender.messages[0][1])
    assert buttons == [("⬅️ Назад", "nav:back"), ("🏠 Главное меню", "nav:home")]
    assert state.get_current_screen("dev", "900") == state.STAFF_LIST_SCREEN


def test_staff_card_valid_protected_stale_and_no_mutation_buttons(db: Path) -> None:
    _user(db, "issuer", display_name="Issuer")
    _user(db, "dev", display_name="Protected")
    _assign(db, "dev", "developer", by="issuer")
    _set_role_dates(db, "dev", "developer", "2026-01-01T08:00:00+00:00")

    ctx, sender = _ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}dev", user="dev")
    asyncio.run(handle_staff_card(ctx))
    text = _texts(sender)[0]
    assert "<b>Карточка сотрудника</b>" in text
    assert "👤 Имя: Protected" in text
    assert "🆔 MAX ID: dev" in text
    assert "🎖 Роль: 💻 Разработчик" in text
    assert "🔒 Защищённый системный разработчик" in text
    buttons = _buttons(sender.messages[0][1])
    assert ("⬅️ Назад", STAFF_LIST_PAYLOAD) in buttons
    assert all("Снять" not in label and "Назнач" not in label for label, _ in buttons)

    stale_ctx, stale_sender = _ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}missing", user="dev")
    asyncio.run(handle_staff_card(stale_ctx))
    assert _texts(stale_sender)[0] == "Сотрудник не найден"
    assert "missing" not in _texts(stale_sender)[0]


def test_display_name_missing_linked_user_and_malformed_callback_safe(db: Path) -> None:
    _user(db, "issuer")
    _user(db, "dev", display_name="Dev")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "ghost", "admin", by="issuer")
    ctx, sender = _ctx(STAFF_LIST_PAYLOAD, user="dev")
    asyncio.run(handle_staff_list(ctx))
    assert "👤 Без имени" in _texts(sender)[0]
    assert "🛠 Выдал: Разработчик" in _texts(sender)[0]

    malformed_ctx, malformed_sender = _ctx(STAFF_CARD_PAYLOAD_PREFIX, user="dev")
    asyncio.run(handle_staff_card(malformed_ctx))
    assert _texts(malformed_sender)[0] == "Сотрудник не найден"
