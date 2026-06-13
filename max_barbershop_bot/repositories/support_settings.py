"""SQLite repository for MAX support settings."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

DEFAULT_SUPPORT_DESCRIPTION = "Если у вас возникли вопросы, напишите нам — с удовольствием поможем! 🙂"
DEFAULT_SUPPORT_USERNAME = "flowbots1sup"


@dataclass(frozen=True)
class SupportSettings:
    """Stored support screen settings."""

    id: int | None = None
    support_username: str | None = None
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
                    INSERT INTO support_settings (support_username, support_description, is_active)
                    VALUES (?, ?, 1)
                    """,
                    (username, description),
                )
                settings_id = int(cursor.lastrowid)
            else:
                settings_id = current
                connection.execute(
                    """
                    UPDATE support_settings
                    SET support_username = ?, support_description = ?, is_active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (username, description, settings_id),
                )
            connection.commit()
            return self._get_by_id(connection, settings_id) or SupportSettings(
                id=settings_id,
                support_username=username,
                support_description=description,
            )

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


def effective_support_settings(settings: SupportSettings | None) -> SupportSettings:
    """Apply Telegram defaults when DB settings are missing."""

    if settings is None:
        return SupportSettings(support_username=DEFAULT_SUPPORT_USERNAME, support_description=DEFAULT_SUPPORT_DESCRIPTION)
    return SupportSettings(
        id=settings.id,
        support_username=normalize_support_username(settings.support_username) or DEFAULT_SUPPORT_USERNAME,
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
        support_description=_support_description_or_default(row["support_description"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
