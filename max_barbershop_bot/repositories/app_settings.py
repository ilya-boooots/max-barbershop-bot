"""Small key-value app settings repository for MAX bot runtime switches."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime


NOTIFICATIONS_ENABLED_KEY = "notifications_enabled"
DEFAULT_NOTIFICATIONS_ENABLED = True


class AppSettingsRepository:
    """Persist global bot settings with explicit-value semantics."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def get_bool(self, key: str, *, default: bool) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ? LIMIT 1",
                (str(key),),
            ).fetchone()
        if row is None or row["value"] is None:
            return bool(default)
        return _value_to_bool(row["value"], default=default)

    def set_bool(self, key: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), "1" if enabled else "0", now, now),
            )
            connection.commit()

    def notifications_enabled(self) -> bool:
        """Return global notification switch; missing means enabled."""

        return self.get_bool(NOTIFICATIONS_ENABLED_KEY, default=DEFAULT_NOTIFICATIONS_ENABLED)

    def notification_setting_source(self) -> str:
        """Return whether notification setting is explicit or defaulted."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM app_settings WHERE key = ? LIMIT 1",
                (NOTIFICATIONS_ENABLED_KEY,),
            ).fetchone()
        return "app_settings" if row is not None else "default_enabled"

    def set_notifications_enabled(self, enabled: bool) -> None:
        self.set_bool(NOTIFICATIONS_ENABLED_KEY, enabled)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def _value_to_bool(value: object, *, default: bool) -> bool:
    clean = str(value).strip().lower()
    if clean in {"1", "true", "yes", "on", "enabled", "включено"}:
        return True
    if clean in {"0", "false", "no", "off", "disabled", "выключено"}:
        return False
    return bool(default)
