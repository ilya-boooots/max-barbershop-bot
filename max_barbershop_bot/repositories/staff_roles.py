"""SQLite repository for platform-neutral staff roles."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from os import getenv
from typing import Any, Mapping

from max_barbershop_bot.core.permissions import (
    ROLE_DEVELOPER,
    ROLE_PRIORITY,
    ROLE_USER,
    is_protected_developer,
    normalize_role,
)
from max_barbershop_bot.repositories.audit_log import AuditLogRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserProfileUpdate, UsersRepository


class ProtectedDeveloperRoleChangeError(RuntimeError):
    """Raised when code tries to remove or demote the protected developer."""


@dataclass(frozen=True)
class StaffRole:
    """Persisted platform-neutral staff role assignment."""

    id: int | None
    platform: str
    platform_user_id: str
    role: str
    assigned_by_platform_user_id: str | None
    created_at: str | None
    updated_at: str | None


class StaffRolesRepository:
    """SQLite-backed staff role repository for the MAX bot."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def assign_role(
        self,
        platform_user_id: str,
        role: str,
        assigned_by_platform_user_id: str | None = None,
        platform: str = PLATFORM_MAX,
    ) -> StaffRole:
        """Assign a role and update the cached user role when the user exists."""

        normalized_role = normalize_role(role)
        platform = _required_text(platform, "platform")
        platform_user_id = _required_text(platform_user_id, "platform_user_id")
        if self._is_protected_developer(platform_user_id, platform) and normalized_role != ROLE_DEVELOPER:
            self.log_protected_developer_role_change_blocked(
                actor_platform_user_id=assigned_by_platform_user_id,
                target_platform_user_id=platform_user_id,
                attempted_role=normalized_role,
                action="role_assign",
                platform=platform,
            )
            raise ProtectedDeveloperRoleChangeError("Нельзя понизить роль защищённого разработчика")
        old_role = self.get_highest_role(platform_user_id, platform=platform)
        target_max_user_id = self._target_max_user_id(platform_user_id, platform)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO staff_roles (
                    platform,
                    platform_user_id,
                    role,
                    assigned_by_platform_user_id
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(platform, platform_user_id, role) DO UPDATE SET
                    assigned_by_platform_user_id = excluded.assigned_by_platform_user_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    platform,
                    platform_user_id,
                    normalized_role,
                    _optional_text(assigned_by_platform_user_id),
                ),
            )
            connection.commit()

        highest_role = self.get_highest_role(platform_user_id, platform=platform)
        self._sync_user_role(platform_user_id, highest_role, platform)
        self._audit_role_event(
            event_type="role_assigned",
            actor_platform_user_id=assigned_by_platform_user_id,
            target_platform_user_id=platform_user_id,
            target_max_user_id=target_max_user_id,
            old_role=old_role,
            new_role=highest_role if normalized_role != highest_role else normalized_role,
            metadata={"assigned_role": normalized_role},
            platform=platform,
        )
        assigned = self._get_role(platform_user_id, normalized_role, platform)
        if assigned is None:
            raise RuntimeError("Роль не найдена после назначения")
        return assigned

    def remove_role(
        self,
        platform_user_id: str,
        role: str,
        platform: str = PLATFORM_MAX,
        actor_platform_user_id: str | None = None,
    ) -> bool:
        """Remove a role and refresh the cached user role."""

        normalized_role = normalize_role(role)
        platform = _required_text(platform, "platform")
        platform_user_id = _required_text(platform_user_id, "platform_user_id")
        if self._is_protected_developer(platform_user_id, platform):
            self.log_protected_developer_role_change_blocked(
                actor_platform_user_id=actor_platform_user_id,
                target_platform_user_id=platform_user_id,
                attempted_role=ROLE_USER,
                action="role_remove",
                platform=platform,
            )
            raise ProtectedDeveloperRoleChangeError("Нельзя снять роль защищённого разработчика")
        old_role = self.get_highest_role(platform_user_id, platform=platform)
        target_max_user_id = self._target_max_user_id(platform_user_id, platform)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                DELETE FROM staff_roles
                WHERE platform = ? AND platform_user_id = ? AND role = ?
                """,
                (platform, platform_user_id, normalized_role),
            )
            removed = cursor.rowcount > 0
            connection.commit()

        if removed:
            new_role = self.get_highest_role(platform_user_id, platform=platform)
            self._sync_user_role(
                platform_user_id,
                new_role,
                platform,
            )
            self._audit_role_event(
                event_type="role_removed",
                actor_platform_user_id=actor_platform_user_id,
                target_platform_user_id=platform_user_id,
                target_max_user_id=target_max_user_id,
                old_role=old_role,
                new_role=new_role,
                metadata={"removed_role": normalized_role},
                platform=platform,
            )
        return removed

    def get_roles(self, platform_user_id: str, platform: str = PLATFORM_MAX) -> list[str]:
        """Return assigned roles ordered from highest to lowest priority."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT role FROM staff_roles
                WHERE platform = ? AND platform_user_id = ?
                """,
                (
                    _required_text(platform, "platform"),
                    _required_text(platform_user_id, "platform_user_id"),
                ),
            ).fetchall()
        roles = {normalize_role(str(row["role"])) for row in rows}
        return sorted(roles, key=lambda value: ROLE_PRIORITY[value], reverse=True)

    def get_highest_role(self, platform_user_id: str, platform: str = PLATFORM_MAX) -> str:
        """Return the highest effective role for a user or the default user role."""

        platform = _required_text(platform, "platform")
        platform_user_id = _required_text(platform_user_id, "platform_user_id")
        if self._is_protected_developer(platform_user_id, platform):
            return ROLE_DEVELOPER
        roles = self.get_roles(platform_user_id, platform=platform)
        if not roles:
            return ROLE_USER
        return roles[0]

    def has_role(
        self,
        platform_user_id: str,
        role: str,
        platform: str = PLATFORM_MAX,
    ) -> bool:
        """Check whether a user has a role assignment."""

        return normalize_role(role) in self.get_roles(platform_user_id, platform=platform)

    def list_staff(self, platform: str = PLATFORM_MAX) -> list[StaffRole]:
        """List all staff role assignments for one platform."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM staff_roles
                WHERE platform = ?
                ORDER BY CASE role
                    WHEN 'developer' THEN 1
                    WHEN 'admin' THEN 2
                    WHEN 'manager' THEN 3
                    ELSE 4
                END, platform_user_id ASC
                """,
                (_required_text(platform, "platform"),),
            ).fetchall()
        return [_row_to_staff_role(row) for row in rows]

    def ensure_developer(
        self,
        platform_user_id: str,
        assigned_by_platform_user_id: str | None = None,
        platform: str = PLATFORM_MAX,
    ) -> StaffRole:
        """Ensure that the configured protected owner has developer role."""

        return self.assign_role(
            platform_user_id,
            ROLE_DEVELOPER,
            assigned_by_platform_user_id=assigned_by_platform_user_id,
            platform=platform,
        )

    def _get_role(
        self,
        platform_user_id: str,
        role: str,
        platform: str,
    ) -> StaffRole | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM staff_roles
                WHERE platform = ? AND platform_user_id = ? AND role = ?
                LIMIT 1
                """,
                (platform, platform_user_id, role),
            ).fetchone()
        if row is None:
            return None
        return _row_to_staff_role(row)

    def _sync_user_role(self, platform_user_id: str, role: str, platform: str) -> None:
        users = UsersRepository(self._database_path)
        if users.find_by_platform_user_id(platform_user_id, platform=platform) is None:
            return
        users.update_profile(
            platform_user_id,
            UserProfileUpdate(role=normalize_role(role)),
            platform=platform,
        )

    def _audit_role_event(
        self,
        *,
        event_type: str,
        actor_platform_user_id: str | None,
        target_platform_user_id: str,
        target_max_user_id: str | None,
        old_role: str | None,
        new_role: str | None,
        metadata: Mapping[str, Any] | None,
        platform: str,
    ) -> None:
        AuditLogRepository(self._database_path).create(
            event_type=event_type,
            actor_platform_user_id=actor_platform_user_id,
            target_platform_user_id=target_platform_user_id,
            target_max_user_id=target_max_user_id,
            old_role=old_role,
            new_role=new_role,
            metadata=metadata,
            platform=platform,
        )

    def log_role_change_blocked(
        self,
        *,
        actor_platform_user_id: str | None,
        target_platform_user_id: str,
        old_role: str | None,
        new_role: str | None,
        action: str,
        platform: str = PLATFORM_MAX,
    ) -> None:
        """Audit a blocked non-protected role change attempt."""

        platform = _required_text(platform, "platform")
        target_platform_user_id = _required_text(target_platform_user_id, "target_platform_user_id")
        self._audit_role_event(
            event_type="role_change_blocked",
            actor_platform_user_id=actor_platform_user_id,
            target_platform_user_id=target_platform_user_id,
            target_max_user_id=self._target_max_user_id(target_platform_user_id, platform),
            old_role=normalize_role(old_role),
            new_role=normalize_role(new_role),
            metadata={"action": action},
            platform=platform,
        )

    def log_protected_developer_role_change_blocked(
        self,
        *,
        actor_platform_user_id: str | None,
        target_platform_user_id: str,
        attempted_role: str | None,
        action: str,
        platform: str = PLATFORM_MAX,
    ) -> None:
        """Audit a blocked protected developer role change attempt."""

        platform = _required_text(platform, "platform")
        target_platform_user_id = _required_text(target_platform_user_id, "target_platform_user_id")
        self._audit_role_event(
            event_type="protected_developer_role_change_blocked",
            actor_platform_user_id=actor_platform_user_id,
            target_platform_user_id=target_platform_user_id,
            target_max_user_id=self._target_max_user_id(target_platform_user_id, platform),
            old_role=ROLE_DEVELOPER,
            new_role=normalize_role(attempted_role) if attempted_role else None,
            metadata={"action": action},
            platform=platform,
        )

    def _is_protected_developer(self, platform_user_id: str, platform: str) -> bool:
        if platform != PLATFORM_MAX:
            return False
        dev_max_user_id = getenv("DEV_MAX_USER_ID", "").strip() or None
        if is_protected_developer(platform_user_id, dev_max_user_id):
            return True
        user = UsersRepository(self._database_path).find_by_platform_user_id(platform_user_id, platform=platform)
        return is_protected_developer(platform_user_id, dev_max_user_id, max_user_id=user.max_user_id if user else None)

    def _target_max_user_id(self, platform_user_id: str, platform: str) -> str | None:
        user = UsersRepository(self._database_path).find_by_platform_user_id(platform_user_id, platform=platform)
        return user.max_user_id if user else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_staff_role(row: sqlite3.Row) -> StaffRole:
    return StaffRole(
        id=int(row["id"]) if row["id"] is not None else None,
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        role=normalize_role(str(row["role"])),
        assigned_by_platform_user_id=_optional_text(row["assigned_by_platform_user_id"]),
        created_at=_optional_text(row["created_at"]),
        updated_at=_optional_text(row["updated_at"]),
    )


def _required_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым")
    return normalized


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
