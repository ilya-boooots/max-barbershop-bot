from __future__ import annotations

import pytest

from app.core.permissions import DEVELOPER_TG_ID, ROLE_DEVELOPER, resolve_role
from app.core.staff_permissions import can_assign_role, can_manage_roles, can_remove_or_change_target, resolve_role as resolve_staff_role
from app.keyboards.factory import get_main_menu_kb
from app.repositories.users import set_user_role
from tests.factories import create_user
from tests.sync import run


pytestmark = pytest.mark.unit


def test_protected_developer_always_resolves_to_developer(initialized_db):
    role = run(resolve_role(DEVELOPER_TG_ID))
    assert role == ROLE_DEVELOPER
    assert resolve_staff_role(DEVELOPER_TG_ID, "admin") == "developer"


def test_protected_developer_cannot_be_demoted(initialized_db):
    run(create_user(tg_id=DEVELOPER_TG_ID, name="Dev"))
    run(set_user_role(DEVELOPER_TG_ID, "user", assigned_by_tg_id=999, assigned_at_iso="2026-01-01T00:00:00+00:00"))
    assert resolve_staff_role(DEVELOPER_TG_ID, "user") == "developer"
    assert can_assign_role(555, "manager", DEVELOPER_TG_ID, "admin") is False


def test_admin_restrictions_and_role_visibility(initialized_db):
    assert can_manage_roles("admin") is False
    assert can_remove_or_change_target(actor_tg_id=200, actor_role="admin", target_tg_id=201, target_role="manager") is False

    kb = run(get_main_menu_kb(user_id=200, db_role="admin"))
    labels = [btn.text for row in kb.keyboard for btn in row]
    assert "🧪 Админ-панель" not in labels
    assert "🔔 Уведомления" not in labels
    assert "💬 Сообщения" not in labels
    assert "🔌 Проверить YClients" not in labels


def test_developer_main_menu_hides_root_admin_button(initialized_db):
    kb = run(get_main_menu_kb(user_id=DEVELOPER_TG_ID, db_role="developer"))
    labels = [btn.text for row in kb.keyboard for btn in row]
    first_row = [btn.text for btn in kb.keyboard[0]]
    assert first_row == ["✂️ Записаться", "📅 Мои записи"]
    assert "👥 Персонал" in labels
    assert "🧪 Админ-панель" not in labels
    assert "🔔 Уведомления" not in labels
    assert "💬 Сообщения" not in labels
    assert "🔌 Проверить YClients" not in labels
