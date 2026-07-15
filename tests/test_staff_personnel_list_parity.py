from __future__ import annotations

import inspect
from pathlib import Path

from max_barbershop_bot.flows import staff as staff_flow
from max_barbershop_bot.ui import buttons, texts

ROOT = Path(__file__).resolve().parents[1]


def test_plan_and_reference_scope_for_pr_038() -> None:
    plan = (ROOT / "docs/max_telegram_parity_plan_v2.md").read_text(encoding="utf-8")
    assert "PR-038 — Staff/personnel list parity" in plan
    assert "Personnel list only." in plan
    assert "Do NOT touch:" in plan and "role assignment" in plan
    assert (ROOT / "telegram_reference/app/handlers/staff/personnel.py").exists()
    assert (ROOT / "telegram_reference/app/keyboards/staff.py").exists()
    assert (ROOT / "telegram_reference/app/repositories/staff_roles.py").exists()


def test_telegram_reference_active_personnel_list_semantics() -> None:
    personnel = (ROOT / "telegram_reference/app/handlers/staff/personnel.py").read_text(encoding="utf-8")
    keyboard = (ROOT / "telegram_reference/app/keyboards/staff.py").read_text(encoding="utf-8")
    repo = (ROOT / "telegram_reference/app/repositories/staff_roles.py").read_text(encoding="utf-8")
    permissions = (ROOT / "telegram_reference/app/core/staff_permissions.py").read_text(encoding="utf-8")
    assert 'F.data == "staff:menu:show_all"' in personnel
    assert 'can_view_personnel(role)' in personnel
    assert '"⛔️ Недостаточно прав."' in personnel
    assert '"👥 Персонал ресторана"' in personnel
    assert 'f"Всего: {len(staff)}"' in personnel
    assert 'role_label(member_role)' in personnel
    assert 'format_branch_datetime' in personnel
    assert '🛠 Выдал:' in personnel
    assert 'staff_list_kb(buttons)' in personnel
    assert 'callback_data=f"staff:card:open:{tg_id}"' in keyboard
    assert "ORDER BY CASE sr.role" in repo and "sr.assigned_at DESC" in repo
    assert "STAFF_VIEW_ROLES = {ROLE_DEVELOPER, ROLE_MANAGER, ROLE_ADMIN}" in permissions


def test_staff_list_payload_registered_to_real_handler() -> None:
    source = inspect.getsource(staff_flow.register_staff_routes)
    assert "router.on_callback(STAFF_LIST_PAYLOAD, handle_staff_list)" in source
    assert "router.on_callback_prefix(STAFF_CARD_PAYLOAD_PREFIX, handle_staff_card)" in source
    assert inspect.iscoroutinefunction(staff_flow.handle_staff_list)
    assert inspect.iscoroutinefunction(staff_flow.handle_staff_card)


def test_text_and_button_constants_match_telegram_list() -> None:
    assert texts.STAFF_LIST_TITLE_TEXT == "👥 Персонал ресторана"
    assert texts.STAFF_LIST_EMPTY_TEXT == "👥 Персонал ресторана\nВсего: 0"
    assert buttons.STAFF_LIST_PAYLOAD == "staff:list"
    assert buttons.STAFF_CARD_PAYLOAD_PREFIX == "staff:card:open:"


def test_scope_safety_assignment_removal_handlers_and_no_aiogram() -> None:
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
    max_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in [
            "max_barbershop_bot/flows/staff.py",
            "max_barbershop_bot/repositories/staff_roles.py",
            "max_barbershop_bot/ui/buttons.py",
            "max_barbershop_bot/ui/texts.py",
        ]
    )
    assert "from aiogram" not in max_sources
    assert "import aiogram" not in max_sources
