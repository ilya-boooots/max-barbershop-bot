"""Simple SQLite initialization for the MAX bot."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def init_database(database_path: str) -> None:
    """Create the SQLite database and apply idempotent baseline migrations."""

    path = Path(database_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = _connect(database_path)
    try:
        _apply_migrations(connection)
    finally:
        connection.close()


def _connect(database_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with required pragmas enabled."""

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply simple idempotent migrations for the initial database schema."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            max_user_id TEXT,
            chat_id TEXT,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            yclients_client_id TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id)
        );

        CREATE TABLE IF NOT EXISTS staff_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            assigned_by_platform_user_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, role)
        );

        CREATE TABLE IF NOT EXISTS yclients_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT,
            partner_token TEXT,
            user_token TEXT,
            branch_timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
            contacts_override_json TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS platform_attribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT,
            yclients_client_id TEXT,
            marker TEXT NOT NULL DEFAULT 'Клиент записался из MAX бота',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS state_storage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_key TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT,
            chat_id TEXT,
            current_screen TEXT,
            screen_stack_json TEXT,
            state_data_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_users_platform_user_id
            ON users(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_users_max_user_id
            ON users(max_user_id);
        CREATE INDEX IF NOT EXISTS idx_users_chat_id
            ON users(chat_id);
        CREATE INDEX IF NOT EXISTS idx_staff_roles_platform_user_id
            ON staff_roles(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_platform_attribution_platform_user_id
            ON platform_attribution(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_platform_attribution_yclients_record_id
            ON platform_attribution(yclients_record_id);
        CREATE INDEX IF NOT EXISTS idx_state_storage_state_key
            ON state_storage(state_key);
        """
    )
    connection.commit()
