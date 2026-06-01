from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup

from app.core.permissions import resolve_role
from app.core.staff_permissions import can_manage_yclients, can_view_personnel, can_view_statistics
from app.keyboards.menu import admin_main_menu_kb, developer_main_menu_kb, user_main_menu_kb


PRIVILEGED_ROLES = {"admin", "manager"}


async def get_main_menu_kb(user_id: int, db_role: str | None) -> ReplyKeyboardMarkup:
    role = db_role or await resolve_role(user_id) or "user"
    show_statistics = can_view_statistics(role)
    show_personnel = can_view_personnel(role)
    show_yclients = can_manage_yclients(role)

    if role == "developer":
        return developer_main_menu_kb(
            show_statistics=show_statistics,
            show_personnel=show_personnel,
            show_yclients_integration=show_yclients,
            show_ycheck=True,
            show_settings=True,
            show_messages=True,
            show_broadcast=True,
            show_dev_admin_panel=False,
            show_dev_diagnostics=True,
        )
    if role in PRIVILEGED_ROLES:
        return admin_main_menu_kb(
            show_statistics=show_statistics,
            show_personnel=show_personnel,
            show_yclients_integration=show_yclients,
            show_ycheck=True,
            show_settings=True,
            show_messages=True,
            show_broadcast=True,
        )
    return user_main_menu_kb()
