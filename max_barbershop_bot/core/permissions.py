"""Transport-neutral roles and permission helpers for the MAX bot."""

from __future__ import annotations

ROLE_DEVELOPER = "developer"
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_USER = "user"

ROLE_PRIORITY = {
    ROLE_USER: 0,
    ROLE_ADMIN: 1,
    ROLE_MANAGER: 2,
    ROLE_DEVELOPER: 3,
}
VALID_ROLES = frozenset(ROLE_PRIORITY)


def normalize_role(role: str | None) -> str:
    """Return a known role or the safe default user role."""

    if role is None:
        return ROLE_USER
    normalized = role.strip().lower()
    if normalized in VALID_ROLES:
        return normalized
    return ROLE_USER


def is_valid_role(role: str) -> bool:
    """Check that a role name is supported."""

    return role in VALID_ROLES


def is_developer(role: str) -> bool:
    """Check whether a role has developer access."""

    return normalize_role(role) == ROLE_DEVELOPER


def is_admin_or_higher(role: str) -> bool:
    """Check whether a role has admin-level access."""

    return normalize_role(role) in {ROLE_ADMIN, ROLE_DEVELOPER}


def is_manager_or_higher(role: str) -> bool:
    """Check whether a role has staff-level access."""

    return normalize_role(role) in {ROLE_ADMIN, ROLE_MANAGER, ROLE_DEVELOPER}


def can_view_staff(role: str) -> bool:
    """Allow staff section for admins, managers and developers."""

    return normalize_role(role) in {ROLE_ADMIN, ROLE_MANAGER, ROLE_DEVELOPER}


def can_manage_roles(role: str) -> bool:
    """Allow role management for managers and developers, like Telegram."""

    return normalize_role(role) in {ROLE_MANAGER, ROLE_DEVELOPER}


def can_view_settings(role: str) -> bool:
    """Allow settings hub when at least one settings section is visible."""

    return any(
        (
            can_view_yclients_settings(role),
            can_view_contacts_settings(role),
            can_view_notification_settings(role),
            can_manage_roles(role),
            can_view_diagnostics_settings(role),
        )
    )


def can_view_yclients_settings(role: str) -> bool:
    """Allow YClients settings for managers, admins and developers."""

    return can_view_yclients(role)


def can_view_contacts_settings(role: str) -> bool:
    """Allow operational contacts settings for managers, admins and developers."""

    return is_manager_or_higher(role)


def can_view_notification_settings(role: str) -> bool:
    """Allow notification settings status for managers, admins and developers."""

    return is_manager_or_higher(role)


def can_view_diagnostics_settings(role: str) -> bool:
    """Allow diagnostics settings section for admins and developers."""

    return is_admin_or_higher(role)


def can_view_broadcasts(role: str) -> bool:
    """Allow broadcast section for managers, admins and developers."""

    return is_manager_or_higher(role)


def can_view_statistics(role: str) -> bool:
    """Allow statistics section for managers and developers, like Telegram."""

    return normalize_role(role) in {ROLE_MANAGER, ROLE_DEVELOPER}


def can_view_yclients(role: str) -> bool:
    """Allow YClients section for managers and developers, like Telegram."""

    return normalize_role(role) in {ROLE_MANAGER, ROLE_DEVELOPER}


def can_view_notification_history(role: str) -> bool:
    """Allow notification diagnostics for managers, admins and developers."""

    return is_manager_or_higher(role)


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """Check whether an actor role may assign a target role."""

    actor = normalize_role(actor_role)
    target = normalize_role(target_role)
    if actor == ROLE_DEVELOPER:
        return True
    if actor == ROLE_MANAGER:
        return target in {ROLE_ADMIN, ROLE_MANAGER}
    return False


def can_remove_role(actor_role: str, target_role: str) -> bool:
    """Check whether an actor role may remove a target role."""

    actor = normalize_role(actor_role)
    target = normalize_role(target_role)
    if actor == ROLE_DEVELOPER:
        return target in {ROLE_ADMIN, ROLE_MANAGER, ROLE_USER}
    if actor == ROLE_MANAGER:
        return target in {ROLE_ADMIN, ROLE_MANAGER, ROLE_USER}
    return False


def is_protected_developer(
    platform_user_id: str | None,
    dev_max_user_id: str | None,
    max_user_id: str | None = None,
) -> bool:
    """Check whether the current MAX identity matches the configured protected owner."""

    if dev_max_user_id is None:
        return False
    protected_id = dev_max_user_id.strip()
    if not protected_id:
        return False
    return _same_identity(platform_user_id, protected_id) or _same_identity(max_user_id, protected_id)


def effective_role(
    db_role: str | None,
    *,
    platform_user_id: str | None = None,
    dev_max_user_id: str | None = None,
    max_user_id: str | None = None,
) -> str:
    """Resolve a stored role with protected developer override."""

    if is_protected_developer(platform_user_id, dev_max_user_id, max_user_id=max_user_id):
        return ROLE_DEVELOPER
    return normalize_role(db_role)


def _same_identity(value: str | None, expected: str) -> bool:
    return value is not None and str(value).strip() == expected


def _priority(role: str) -> int:
    return ROLE_PRIORITY[normalize_role(role)]
