"""SQLite repository for MAX support settings."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

DEFAULT_SUPPORT_DESCRIPTION = "Если у вас возникли вопросы, напишите нам — с удовольствием поможем! 🙂"
DEFAULT_SUPPORT_USERNAME = "flowbots1sup"
DEFAULT_SUPPORT_MAX_USERNAME = "flowbots1sup"


@dataclass(frozen=True)
class SupportSettings:
    """Stored support screen settings."""

    id: int | None = None
    support_username: str | None = None
    support_max_username: str | None = None
    support_description: str = DEFAULT_SUPPORT_DESCRIPTION
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class SupportSettingsRepository:
    """Minimal sqlite3 repository for support settings."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def get_active(self) -> SupportSettings | None:
        """Return newest active support settings row."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM support_settings
                WHERE LOWER(COALESCE(CAST(is_active AS TEXT), '1')) IN ('1', 'true', 'yes', 'on')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            return _row_to_settings(row)

    def upsert_active(self, support_username: str | None, support_description: str | None) -> SupportSettings:
        """Create or update the active support settings row."""

        username = normalize_support_username(support_username)
        if support_username is not None and support_username.strip() and username is None:
            raise ValueError("Invalid support username")
        description = _support_description_or_default(support_description)
        with closing(self._connect()) as connection:
            current = self._get_active_id(connection)
            if current is None:
                cursor = connection.execute(
                    """
                    INSERT INTO support_settings (
                        support_username, support_max_username, support_description, is_active
                    )
                    VALUES (?, ?, ?, 1)
                    """,
                    (username, username, description),
                )
                settings_id = int(cursor.lastrowid)
            else:
                settings_id = current
                current_settings = self._get_by_id(connection, settings_id)
                if (
                    current_settings is not None
                    and current_settings.support_username == username
                    and current_settings.support_max_username == username
                    and current_settings.support_description == description
                ):
                    return current_settings
                connection.execute(
                    """
                    UPDATE support_settings
                    SET support_username = ?, support_max_username = ?, support_description = ?,
                        is_active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (username, username, description, settings_id),
                )
            connection.commit()
            return self._get_by_id(connection, settings_id) or SupportSettings(
                id=settings_id,
                support_username=username,
                support_max_username=username,
                support_description=description,
            )

    def reset_active(self) -> tuple[SupportSettings, bool]:
        """Restore Telegram defaults and report whether storage changed."""

        current = self.get_active()
        if (
            current is not None
            and current.support_username == DEFAULT_SUPPORT_USERNAME
            and current.support_max_username == DEFAULT_SUPPORT_MAX_USERNAME
            and current.support_description == DEFAULT_SUPPORT_DESCRIPTION
        ):
            return current, False
        return self.upsert_active(DEFAULT_SUPPORT_USERNAME, DEFAULT_SUPPORT_DESCRIPTION), True

    def update_description(self, support_description: str) -> tuple[SupportSettings, bool]:
        """Update only the description, preserving both platform destinations."""

        description = _support_description_or_default(support_description)
        current = self.get_active()
        if current is None:
            return self.upsert_active(DEFAULT_SUPPORT_USERNAME, description), True
        if current.support_description == description:
            return current, False
        if current.id is None:
            raise RuntimeError("Active support settings row has no id")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE support_settings
                SET support_description = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (description, current.id),
            )
            connection.commit()
            updated = self._get_by_id(connection, current.id)
        if updated is None:
            raise RuntimeError("Support settings row disappeared during description update")
        return updated, True

    def _get_active_id(self, connection: sqlite3.Connection) -> int | None:
        row = connection.execute(
            """
            SELECT id FROM support_settings
            WHERE LOWER(COALESCE(CAST(is_active AS TEXT), '1')) IN ('1', 'true', 'yes', 'on')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _get_by_id(self, connection: sqlite3.Connection, settings_id: int) -> SupportSettings | None:
        row = connection.execute("SELECT * FROM support_settings WHERE id = ?", (settings_id,)).fetchone()
        return _row_to_settings(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def normalize_support_username(raw: str | None) -> str | None:
    """Normalize Telegram username/link to username without @, or return None."""

    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("https://"):
        value = value[len("https://"):]
    elif value.startswith("http://"):
        value = value[len("http://"):]
    if value.lower().startswith("t.me/"):
        value = value[5:]
    elif value.lower().startswith("max.ru/"):
        value = value[7:]
    value = value.lstrip("@").strip()
    if not value or any(ch.isspace() for ch in value):
        return None
    if "/" in value or "?" in value or "#" in value:
        return None
    if not (5 <= len(value) <= 32):
        return None
    if not all(ch.isalnum() or ch == "_" for ch in value):
        return None
    return value


def display_support_username(username: str | None) -> str | None:
    """Return username formatted for visible text."""

    normalized = normalize_support_username(username)
    return f"@{normalized}" if normalized else None


def build_support_url(username: str | None) -> str | None:
    """Build Telegram support URL matching the Telegram reference."""

    normalized = normalize_support_username(username)
    return f"https://t.me/{normalized}" if normalized else None


def build_max_support_url(username: str | None) -> str | None:
    """Build an official MAX bot/chat deeplink for support."""

    normalized = normalize_support_username(username)
    return f"https://max.ru/{normalized}" if normalized else None


def effective_support_settings(settings: SupportSettings | None) -> SupportSettings:
    """Apply Telegram defaults when DB settings are missing."""

    if settings is None:
        return SupportSettings(
            support_username=DEFAULT_SUPPORT_USERNAME,
            support_max_username=DEFAULT_SUPPORT_MAX_USERNAME,
            support_description=DEFAULT_SUPPORT_DESCRIPTION,
        )
    return SupportSettings(
        id=settings.id,
        support_username=normalize_support_username(settings.support_username) or DEFAULT_SUPPORT_USERNAME,
        support_max_username=normalize_support_username(settings.support_max_username)
        or normalize_support_username(settings.support_username)
        or DEFAULT_SUPPORT_MAX_USERNAME,
        support_description=_support_description_or_default(settings.support_description),
        is_active=settings.is_active,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def _support_description_or_default(raw: str | None) -> str:
    return (raw or "").strip() or DEFAULT_SUPPORT_DESCRIPTION


def _row_to_settings(row: sqlite3.Row | None) -> SupportSettings | None:
    if row is None:
        return None
    return SupportSettings(
        id=row["id"],
        support_username=normalize_support_username(row["support_username"]),
        support_max_username=normalize_support_username(
            _optional_row_value(row, "support_max_username")
        ),
        support_description=_support_description_or_default(row["support_description"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _optional_row_value(row: sqlite3.Row, key: str) -> str | None:
    return row[key] if key in row.keys() else None
