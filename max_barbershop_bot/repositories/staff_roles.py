"""SQLite repository for platform-neutral staff roles."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from max_barbershop_bot.core.permissions import (
    ROLE_DEVELOPER,
    ROLE_PRIORITY,
    ROLE_USER,
    normalize_role,
)
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserProfileUpdate, UsersRepository


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
        assigned = self._get_role(platform_user_id, normalized_role, platform)
        if assigned is None:
            raise RuntimeError("Роль не найдена после назначения")
        return assigned

    def remove_role(
        self,
        platform_user_id: str,
        role: str,
        platform: str = PLATFORM_MAX,
    ) -> bool:
        """Remove a role and refresh the cached user role."""

        normalized_role = normalize_role(role)
        platform = _required_text(platform, "platform")
        platform_user_id = _required_text(platform_user_id, "platform_user_id")
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
            self._sync_user_role(
                platform_user_id,
                self.get_highest_role(platform_user_id, platform=platform),
                platform,
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
        """Return the highest role for a user or the default user role."""

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
