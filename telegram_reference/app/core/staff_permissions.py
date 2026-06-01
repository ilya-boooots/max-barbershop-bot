from __future__ import annotations

from app.core.permissions import DEVELOPER_TG_ID

ROLE_DEVELOPER = "developer"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
ROLE_USER = "user"

STAFF_VIEW_ROLES = {ROLE_DEVELOPER, ROLE_MANAGER, ROLE_ADMIN}
ROLE_MANAGE_ROLES = {ROLE_DEVELOPER, ROLE_MANAGER}
STATS_ACCESS_ROLES = {ROLE_DEVELOPER, ROLE_MANAGER}
YCLIENTS_MANAGE_ROLES = {ROLE_DEVELOPER, ROLE_MANAGER}


def resolve_role(tg_id: int, db_role: str | None) -> str:
    if tg_id == DEVELOPER_TG_ID:
        return ROLE_DEVELOPER
    if db_role in STAFF_VIEW_ROLES:
        return db_role
    return ROLE_USER


def is_protected_developer(tg_id: int) -> bool:
    return tg_id == DEVELOPER_TG_ID


def can_view_personnel(role: str) -> bool:
    return role in STAFF_VIEW_ROLES


def can_manage_roles(role: str) -> bool:
    return role in ROLE_MANAGE_ROLES


def can_view_statistics(role: str) -> bool:
    return role in STATS_ACCESS_ROLES


def can_manage_yclients(role: str) -> bool:
    return role in YCLIENTS_MANAGE_ROLES


def can_remove_or_change_target(
    actor_tg_id: int,
    actor_role: str,
    target_tg_id: int,
    target_role: str | None,
) -> bool:
    if is_protected_developer(target_tg_id):
        return False
    if resolve_role(actor_tg_id, actor_role) == ROLE_DEVELOPER:
        return True
    if resolve_role(actor_tg_id, actor_role) == ROLE_MANAGER:
        return resolve_role(target_tg_id, target_role) in {ROLE_ADMIN, ROLE_MANAGER, ROLE_USER}
    return False


def can_assign_role(actor_tg_id: int, actor_role: str, target_tg_id: int, new_role: str) -> bool:
    if new_role == ROLE_DEVELOPER and target_tg_id != DEVELOPER_TG_ID:
        return False
    if is_protected_developer(target_tg_id):
        return False
    if resolve_role(actor_tg_id, actor_role) == ROLE_DEVELOPER:
        return True
    if resolve_role(actor_tg_id, actor_role) == ROLE_MANAGER:
        return new_role in {ROLE_ADMIN, ROLE_MANAGER}
    return False
