from __future__ import annotations

from app.core.permissions import DEVELOPER_TG_ID

PROTECTED_DEVELOPER_NAME = "🧑‍💻 Разработчик"

ROLE_ORDER = {
    "user": 0,
    "manager": 2,
    "admin": 1,
    "developer": 3,
}

ALLOWED_STAFF_ROLES = {"manager", "admin", "developer"}


def get_dev_user_id() -> int:
    return DEVELOPER_TG_ID


def normalize_role(user_id: int, db_role: str | None) -> str:
    if user_id == DEVELOPER_TG_ID:
        return "developer"
    if db_role in ALLOWED_STAFF_ROLES:
        return db_role
    return "user"


def is_protected_developer(user_id: int) -> bool:
    return user_id == DEVELOPER_TG_ID


def can_manage(
    actor_tg_id: int,
    actor_role: str | None,
    target_tg_id: int,
    target_role: str | None,
) -> bool:
    if is_protected_developer(target_tg_id):
        return False
    resolved_actor_role = normalize_role(actor_tg_id, actor_role)
    resolved_target_role = normalize_role(target_tg_id, target_role)
    return ROLE_ORDER.get(resolved_actor_role, 0) > ROLE_ORDER.get(resolved_target_role, 0)


def has_role(user_role: str, allowed_roles: list[str]) -> bool:
    if user_role == "developer":
        return True
    return user_role in allowed_roles
