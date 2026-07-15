from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import staff as staff_flow
from max_barbershop_bot.flows.staff import handle_staff_card, handle_staff_list
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.ui.buttons import STAFF_CARD_PAYLOAD_PREFIX, STAFF_LIST_PAYLOAD

ROOT = Path(__file__).resolve().parents[1]


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
    path = tmp_path / "staff-final.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEV_MAX_USER_ID", "dev")
    state._user_states.clear()
    YClientsSettingsRepository(str(path)).create_settings(company_id="1", partner_token=None, user_token=None, branch_timezone="Europe/Moscow")
    return path


def _ctx(payload: str, user: str = "dev") -> tuple[RouterContext, FakeSender]:
    sender = FakeSender()
    event = NormalizedEvent("message_callback", user, user, "900", None, payload, f"cb-{payload}")
    return RouterContext(event, sender), sender


def _user(db: Path, uid: str, **kwargs):
    return UsersRepository(str(db)).create(UserCreate(platform=PLATFORM_MAX, platform_user_id=uid, max_user_id=f"max-{uid}", chat_id="900", **kwargs))


def _assign(db: Path, uid: str, role: str = "admin", by: str | None = "issuer") -> None:
    StaffRolesRepository(str(db)).assign_role(uid, role, assigned_by_platform_user_id=by)


def _set_role_date(db: Path, uid: str, role: str = "admin", value: str = "2026-01-03T08:00:00+00:00") -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE staff_roles SET created_at=?, updated_at=? WHERE platform=? AND platform_user_id=? AND role=?",
            (value, value, PLATFORM_MAX, uid, role),
        )


def _texts(sender: FakeSender) -> list[str]:
    return [text for text, _ in sender.messages]


def _buttons(sender: FakeSender) -> list[tuple[str, str]]:
    keyboard = sender.messages[-1][1]
    return [(button.text, button.payload or "") for row in keyboard.rows for button in row]


def _card_text(db: Path, target: str, actor: str = "dev") -> str:
    ctx, sender = _ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}{target}", user=actor)
    asyncio.run(handle_staff_card(ctx))
    return _texts(sender)[0]


def _list_text(db: Path, actor: str = "dev") -> str:
    ctx, sender = _ctx(STAFF_LIST_PAYLOAD, user=actor)
    asyncio.run(handle_staff_list(ctx))
    return _texts(sender)[0]


def test_telegram_reference_issuer_fallback_identifier_and_card_order() -> None:
    utils = (ROOT / "telegram_reference/app/utils/staff.py").read_text(encoding="utf-8")
    assert 'ISSUER_FALLBACK_NAME = "Разработчик"' in utils
    assert 'f"🆔 Telegram ID: {tg_id}\\n"' in utils
    expected_order = [
        '"<b>Карточка сотрудника</b>\\n"',
        'f"👤 Имя: {name}\\n"',
        'f"🆔 Telegram ID: {tg_id}\\n"',
        'f"🎖 Роль: {role}\\n"',
        'f"🗓 Роль с: {assigned_at}\\n"',
        'f"👤 Выдал: {assigned_by}{protected_line}"',
    ]
    positions = [utils.index(fragment) for fragment in expected_order]
    assert positions == sorted(positions)


def test_normal_issuer_name_kept_and_fallback_absent(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _user(db, "issuer", display_name="Главный администратор")
    _user(db, "employee", display_name="Employee")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "employee", "admin", by="issuer")

    list_text = _list_text(db)
    card_text = _card_text(db, "employee")

    assert "🛠 Выдал: Главный администратор" in list_text
    assert "👤 Выдал: Главный администратор" in card_text
    assert "Выдал: Разработчик" not in card_text


def test_missing_issuer_user_uses_developer_fallback_in_list_and_card(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _user(db, "employee", display_name="Employee")
    _assign(db, "dev", "developer", by="ghost-issuer")
    _assign(db, "employee", "admin", by="ghost-issuer")

    list_text = _list_text(db)
    card_text = _card_text(db, "employee")

    assert "🛠 Выдал: Разработчик" in list_text
    assert "👤 Выдал: Разработчик" in card_text
    issuer_lines = [line for line in (list_text + "\n" + card_text).splitlines() if "Выдал:" in line]
    assert all("Без имени" not in line for line in issuer_lines)
    assert all("ghost-issuer" not in line for line in issuer_lines)


def test_nameless_issuer_uses_developer_fallback_without_none_or_blank(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _user(db, "issuer")
    _user(db, "employee", display_name="Employee")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "employee", "admin", by="issuer")

    list_text = _list_text(db)
    card_text = _card_text(db, "employee")

    assert "🛠 Выдал: Разработчик" in list_text
    assert "👤 Выдал: Разработчик" in card_text
    assert "None" not in card_text
    assert "Выдал: \n" not in list_text + card_text


def test_legacy_assignment_without_issuer_uses_developer_fallback(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _user(db, "employee", display_name="Employee")
    _assign(db, "dev", "developer", by=None)
    _assign(db, "employee", "admin", by=None)

    assert "🛠 Выдал: Разработчик" in _list_text(db)
    assert "👤 Выдал: Разработчик" in _card_text(db, "employee")


def test_employee_without_name_still_uses_no_name_fallback(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _assign(db, "dev", "developer")
    _assign(db, "nameless", "admin", by=None)

    list_text = _list_text(db)
    card_text = _card_text(db, "nameless")

    assert "👤 Без имени" in list_text
    assert "👤 Имя: Без имени" in card_text
    assert "Выдал: Разработчик" in card_text


def test_identifier_uses_target_platform_user_id_not_db_or_issuer_id(db: Path) -> None:
    issuer = _user(db, "issuer-id", display_name="Issuer")
    employee = _user(db, "employee-max-id", display_name="Employee")
    _user(db, "dev", display_name="Developer")
    _assign(db, "dev", "developer", by="issuer-id")
    _assign(db, "employee-max-id", "admin", by="issuer-id")

    card_text = _card_text(db, "employee-max-id")

    assert card_text.count("🆔 MAX ID: employee-max-id") == 1
    assert f"🆔 MAX ID: {employee.id}" not in card_text
    assert f"🆔 MAX ID: {issuer.id}" not in card_text
    assert "issuer-id" not in [line for line in card_text.splitlines() if line.startswith("🆔")][0]
    assert "token" not in card_text.lower()


def test_card_identifier_and_field_order_for_protected_developer(db: Path) -> None:
    _user(db, "issuer", display_name="Issuer")
    _user(db, "dev", display_name="Protected")
    _assign(db, "dev", "developer", by="issuer")
    _set_role_date(db, "dev", "developer")

    card_text = _card_text(db, "dev")
    lines = card_text.splitlines()

    assert lines == [
        "<b>Карточка сотрудника</b>",
        "👤 Имя: Protected",
        "🆔 MAX ID: dev",
        "🎖 Роль: 💻 Разработчик",
        "🗓 Роль с: 03.01.2026 11:00",
        "👤 Выдал: Issuer",
        "🔒 Защищённый системный разработчик",
    ]


def test_normal_employee_identifier_without_protected_marker(db: Path) -> None:
    _user(db, "issuer", display_name="Issuer")
    _user(db, "dev", display_name="Developer")
    _user(db, "employee", display_name="Employee")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "employee", "manager", by="issuer")

    card_text = _card_text(db, "employee")

    assert "🆔 MAX ID: employee" in card_text
    assert "🔒 Защищённый системный разработчик" not in card_text


def test_stale_card_and_navigation_regressions(db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _assign(db, "dev", "developer")

    malformed_ctx, malformed_sender = _ctx(STAFF_CARD_PAYLOAD_PREFIX, user="dev")
    asyncio.run(handle_staff_card(malformed_ctx))
    assert _texts(malformed_sender) == ["Сотрудник не найден"]
    assert STAFF_CARD_PAYLOAD_PREFIX not in _texts(malformed_sender)[0]

    missing_ctx, missing_sender = _ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}deleted-employee", user="dev")
    asyncio.run(handle_staff_card(missing_ctx))
    assert _texts(missing_sender) == ["Сотрудник не найден"]
    assert "deleted-employee" not in _texts(missing_sender)[0]

    valid_ctx, valid_sender = _ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}dev", user="dev")
    asyncio.run(handle_staff_card(valid_ctx))
    assert ("⬅️ Назад", STAFF_LIST_PAYLOAD) in _buttons(valid_sender)
    assert ("🏠 Главное меню", "nav:home") in _buttons(valid_sender)
    assert all("Снять" not in label and "Назнач" not in label for label, _ in _buttons(valid_sender))


def test_list_regression_title_count_order_dedupe_labels_and_no_phone_username(db: Path) -> None:
    _user(db, "issuer", display_name="Issuer")
    _user(db, "dev", display_name="Developer")
    _user(db, "admin", display_name="Admin", username="admin_user", phone="+79991234567")
    _user(db, "manager", display_name="Manager")
    _assign(db, "dev", "developer", by="issuer")
    _assign(db, "admin", "manager", by="issuer")
    _assign(db, "admin", "admin", by="issuer")
    _assign(db, "manager", "manager", by="issuer")
    _set_role_date(db, "dev", "developer", "2026-01-01T08:00:00+00:00")
    _set_role_date(db, "admin", "admin", "2026-01-03T08:00:00+00:00")
    _set_role_date(db, "admin", "manager", "2026-01-04T08:00:00+00:00")
    _set_role_date(db, "manager", "manager", "2026-01-02T08:00:00+00:00")

    text = _list_text(db)

    assert text.startswith("👥 Персонал ресторана\nВсего: 3")
    assert text.index("1) 💻 Разработчик") < text.index("2) 🛡 Администратор") < text.index("3) 👑 Управляющий")
    assert text.count("Admin") == 1
    assert "@admin_user" not in text
    assert "Телефон:" not in text
    assert "+79991234567" not in text


def test_no_mutation_methods_are_called_by_list_or_card(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    _user(db, "dev", display_name="Developer")
    _assign(db, "dev", "developer")

    def fail(*args, **kwargs):
        raise AssertionError("mutation method must not be called")

    monkeypatch.setattr(staff_flow.StaffRolesRepository, "assign_role", fail)
    monkeypatch.setattr(staff_flow.StaffRolesRepository, "remove_role", fail)
    monkeypatch.setattr(staff_flow, "notify_role_assigned", fail)
    monkeypatch.setattr(staff_flow, "notify_role_removed", fail)

    asyncio.run(handle_staff_list(_ctx(STAFF_LIST_PAYLOAD, user="dev")[0]))
    asyncio.run(handle_staff_card(_ctx(f"{STAFF_CARD_PAYLOAD_PREFIX}dev", user="dev")[0]))


def test_scope_safety_assignment_removal_handlers_guard_and_no_aiogram() -> None:
    source = (ROOT / "max_barbershop_bot/flows/staff.py").read_text(encoding="utf-8")
    for name in [
        "handle_assign_start",
        "handle_assign_identifier",
        "handle_assign_role",
        "handle_remove_start",
        "handle_remove_identifier",
        "handle_remove_role",
    ]:
        assert f"async def {name}" in source
    repo_source = (ROOT / "max_barbershop_bot/repositories/staff_roles.py").read_text(encoding="utf-8")
    assert "def log_protected_developer_role_change_blocked" in repo_source
    max_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ["max_barbershop_bot/flows/staff.py", "max_barbershop_bot/ui/texts.py"]
    )
    assert "from aiogram" not in max_sources
    assert "import aiogram" not in max_sources
    register_source = inspect.getsource(staff_flow.register_staff_routes)
    assert "router.on_callback(STAFF_ASSIGN_START_PAYLOAD, handle_assign_start)" in register_source
    assert "router.on_callback(STAFF_REMOVE_START_PAYLOAD, handle_remove_start)" in register_source
