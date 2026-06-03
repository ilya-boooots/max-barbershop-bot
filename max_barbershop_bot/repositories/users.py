"""Users repository for MAX platform profiles."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

PLATFORM_MAX = "max"
DEFAULT_USER_ROLE = "user"
DEFAULT_NOTIFICATIONS_ENABLED = True


@dataclass(frozen=True)
class User:
    """Persisted platform-independent user profile."""

    id: int
    platform: str
    platform_user_id: str
    max_user_id: str | None
    chat_id: str | None
    display_name: str | None
    first_name: str | None
    last_name: str | None
    username: str | None
    phone: str | None
    role: str
    yclients_client_id: str | None
    notifications_enabled: bool
    notification_settings: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class UserCreate:
    """Data required to create a MAX user profile."""

    platform_user_id: str
    max_user_id: str | None = None
    chat_id: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    role: str = DEFAULT_USER_ROLE
    yclients_client_id: str | None = None
    notifications_enabled: bool = DEFAULT_NOTIFICATIONS_ENABLED
    notification_settings: Mapping[str, Any] = field(default_factory=dict)
    platform: str = PLATFORM_MAX


@dataclass(frozen=True)
class UserProfileUpdate:
    """Partial user profile update without Telegram-specific fields."""

    max_user_id: str | None = None
    chat_id: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    role: str | None = None
    yclients_client_id: str | None = None
    notifications_enabled: bool | None = None
    notification_settings: Mapping[str, Any] | None = None


class UsersRepository:
    """SQLite-backed users repository for the MAX bot."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create(self, user: UserCreate) -> User:
        """Create a user profile and return the persisted user."""

        platform = _required_text(user.platform, "platform")
        platform_user_id = _required_text(user.platform_user_id, "platform_user_id")
        settings_json = _dump_settings(user.notification_settings)

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    platform,
                    platform_user_id,
                    max_user_id,
                    chat_id,
                    display_name,
                    first_name,
                    last_name,
                    username,
                    phone,
                    role,
                    yclients_client_id,
                    notifications_enabled,
                    notification_settings_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    platform_user_id,
                    _optional_text(user.max_user_id),
                    _optional_text(user.chat_id),
                    _optional_text(user.display_name),
                    _optional_text(user.first_name),
                    _optional_text(user.last_name),
                    _optional_text(user.username),
                    _optional_text(user.phone),
                    _required_text(user.role, "role"),
                    _optional_text(user.yclients_client_id),
                    _bool_to_int(user.notifications_enabled),
                    settings_json,
                ),
            )
            connection.commit()
            return self._get_by_id(connection, cursor.lastrowid)

    def create_or_update_user(
        self,
        *,
        platform_user_id: str,
        max_user_id: str | None = None,
        chat_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
        platform: str = PLATFORM_MAX,
    ) -> User:
        """Create or refresh a base platform identity without erasing profile data."""

        existing = self.find_by_platform_user_id(platform_user_id, platform=platform)
        if existing is not None:
            update = UserProfileUpdate(
                max_user_id=max_user_id,
                chat_id=chat_id,
                first_name=first_name if existing.first_name is None else None,
                last_name=last_name if existing.last_name is None else None,
                username=username if existing.username is None else None,
                display_name=(
                    _join_display_name(first_name, last_name) if existing.display_name is None else None
                ),
            )
            updated = self.update_profile(platform_user_id, update, platform=platform)
            if updated is None:
                raise RuntimeError("Пользователь не найден после обновления")
            return updated

        return self.create(
            UserCreate(
                platform=platform,
                platform_user_id=platform_user_id,
                max_user_id=max_user_id,
                chat_id=chat_id,
                first_name=first_name,
                last_name=last_name,
                username=username,
                display_name=_join_display_name(first_name, last_name),
            )
        )

    def find_by_platform_user_id(
        self,
        platform_user_id: str,
        *,
        platform: str = PLATFORM_MAX,
    ) -> User | None:
        """Find a user by a platform-scoped user id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE platform = ? AND platform_user_id = ?
                LIMIT 1
                """,
                (
                    _required_text(platform, "platform"),
                    _required_text(platform_user_id, "platform_user_id"),
                ),
            ).fetchone()
            return _row_to_user(row)

    def find_by_max_user_id(self, max_user_id: str) -> User | None:
        """Find a user by MAX user id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE max_user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (_required_text(max_user_id, "max_user_id"),),
            ).fetchone()
            return _row_to_user(row)

    def find_by_chat_id(self, chat_id: str) -> User | None:
        """Find a user by MAX chat id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (_required_text(chat_id, "chat_id"),),
            ).fetchone()
            return _row_to_user(row)

    def update_profile(
        self,
        platform_user_id: str,
        update: UserProfileUpdate,
        *,
        platform: str = PLATFORM_MAX,
    ) -> User | None:
        """Update an existing user profile and return it, or None when it is absent."""

        assignments: list[str] = []
        values: list[Any] = []

        update_fields = {
            "max_user_id": update.max_user_id,
            "chat_id": update.chat_id,
            "display_name": update.display_name,
            "first_name": update.first_name,
            "last_name": update.last_name,
            "username": update.username,
            "phone": update.phone,
            "role": update.role,
            "yclients_client_id": update.yclients_client_id,
        }
        for column, value in update_fields.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(
                    _required_text(value, column) if column == "role" else _optional_text(value)
                )

        if update.notifications_enabled is not None:
            assignments.append("notifications_enabled = ?")
            values.append(_bool_to_int(update.notifications_enabled))

        if update.notification_settings is not None:
            assignments.append("notification_settings_json = ?")
            values.append(_dump_settings(update.notification_settings))

        if not assignments:
            return self.find_by_platform_user_id(platform_user_id, platform=platform)

        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.extend(
            [
                _required_text(platform, "platform"),
                _required_text(platform_user_id, "platform_user_id"),
            ]
        )

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                f"""
                UPDATE users
                SET {", ".join(assignments)}
                WHERE platform = ? AND platform_user_id = ?
                """,
                tuple(values),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return None
            connection.commit()
            return self.find_by_platform_user_id(platform_user_id, platform=platform)

    def update_notification_settings(
        self,
        platform_user_id: str,
        settings: Mapping[str, Any],
        *,
        notifications_enabled: bool | None = None,
        platform: str = PLATFORM_MAX,
    ) -> User | None:
        """Update notification preferences for an existing user."""

        return self.update_profile(
            platform_user_id,
            UserProfileUpdate(
                notifications_enabled=notifications_enabled,
                notification_settings=settings,
            ),
            platform=platform,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def _get_by_id(self, connection: sqlite3.Connection, user_id: int) -> User:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        user = _row_to_user(row)
        if user is None:
            raise RuntimeError("Созданный пользователь не найден в базе данных")
        return user


def _row_to_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None

    return User(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        max_user_id=_row_optional_text(row, "max_user_id"),
        chat_id=_row_optional_text(row, "chat_id"),
        display_name=_row_optional_text(row, "display_name"),
        first_name=_row_optional_text(row, "first_name"),
        last_name=_row_optional_text(row, "last_name"),
        username=_row_optional_text(row, "username"),
        phone=_row_optional_text(row, "phone"),
        role=str(row["role"]),
        yclients_client_id=_row_optional_text(row, "yclients_client_id"),
        notifications_enabled=bool(row["notifications_enabled"]),
        notification_settings=_load_settings(_row_optional_text(row, "notification_settings_json")),
        created_at=_row_optional_text(row, "created_at"),
        updated_at=_row_optional_text(row, "updated_at"),
    )


def _join_display_name(first_name: str | None, last_name: str | None) -> str | None:
    parts = [part.strip() for part in (first_name, last_name) if part and part.strip()]
    return " ".join(parts) or None


def _required_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} не может быть пустым")
    return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _dump_settings(settings: Mapping[str, Any]) -> str:
    return json.dumps(dict(settings), ensure_ascii=False, sort_keys=True)


def _load_settings(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _row_optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = row[column]
    return str(value) if value is not None else None
