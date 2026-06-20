"""Simple SQLite initialization for the MAX bot."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE


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
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            max_user_id TEXT,
            chat_id TEXT,
            display_name TEXT,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            phone TEXT,
            birthdate TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            yclients_client_id TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 1,
            notification_settings_json TEXT NOT NULL DEFAULT '{{}}',
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
            branch_timezone TEXT NOT NULL DEFAULT '{DEFAULT_BRANCH_TIMEZONE}',
            branch_title TEXT,
            contacts_override_json TEXT,
            yandex_maps_url TEXT,
            yandex_maps_enabled INTEGER NOT NULL DEFAULT 1,
            twogis_url TEXT,
            twogis_enabled INTEGER NOT NULL DEFAULT 1,
            google_maps_url TEXT,
            google_maps_enabled INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS support_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            support_username TEXT,
            support_max_username TEXT,
            support_description TEXT,
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


        CREATE TABLE IF NOT EXISTS birthday_funnel_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            birth_year INTEGER NOT NULL,
            notification_type TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, birth_year, notification_type)
        );

        CREATE TABLE IF NOT EXISTS cancellation_recovery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT NOT NULL,
            yclients_client_id TEXT,
            max_user_id TEXT,
            chat_id TEXT,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT,
            skipped_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, yclients_record_id)
        );


        CREATE TABLE IF NOT EXISTS repeat_visit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT NOT NULL,
            yclients_client_id TEXT,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT,
            skipped_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, yclients_record_id)
        );

        CREATE TABLE IF NOT EXISTS notification_delivery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT,
            max_user_id TEXT,
            chat_id TEXT,
            message_type TEXT,
            recipient_type TEXT,
            recipient_id TEXT,
            status TEXT NOT NULL,
            status_code INTEGER,
            error_code TEXT,
            error_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 1,
            message_id TEXT,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            is_stopped INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            actor_platform_user_id TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            section TEXT,
            target_platform_user_id TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor_platform_user_id TEXT,
            target_platform_user_id TEXT,
            target_max_user_id TEXT,
            old_role TEXT,
            new_role TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT
        );

        CREATE TABLE IF NOT EXISTS user_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            platform_user_id TEXT,
            max_user_id TEXT,
            chat_id TEXT,
            username TEXT,
            phone TEXT,
            event_type TEXT NOT NULL,
            event_name TEXT NOT NULL,
            screen TEXT,
            payload_json TEXT
        );

        CREATE TABLE IF NOT EXISTS master_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            yclients_staff_id TEXT NOT NULL,
            master_name TEXT,
            photo_file_id TEXT,
            photo_url TEXT,
            photo_attachment_json TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by_platform_user_id TEXT,
            updated_by_platform_user_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, yclients_staff_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT NOT NULL,
            yclients_client_id TEXT,
            status TEXT NOT NULL,
            requested_at TEXT,
            completed_at TEXT,
            skipped_at TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, yclients_record_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            is_negative INTEGER NOT NULL DEFAULT 0,
            admin_notified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, yclients_record_id)
        );

        CREATE TABLE IF NOT EXISTS notification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            max_user_id TEXT,
            chat_id TEXT,
            yclients_record_id TEXT NOT NULL,
            yclients_client_id TEXT,
            notification_type TEXT NOT NULL,
            scheduled_for TEXT,
            sent_at TEXT,
            status TEXT NOT NULL,
            delivery_status_code INTEGER,
            delivery_error_code TEXT,
            delivery_error_message TEXT,
            message_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            is_stopped INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, platform_user_id, yclients_record_id, notification_type)
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
        CREATE INDEX IF NOT EXISTS idx_support_settings_active
            ON support_settings(is_active, id);
        CREATE INDEX IF NOT EXISTS idx_platform_attribution_yclients_record_id
            ON platform_attribution(yclients_record_id);
        CREATE INDEX IF NOT EXISTS idx_state_storage_state_key
            ON state_storage(state_key);
        CREATE INDEX IF NOT EXISTS idx_birthday_funnel_events_user_year
            ON birthday_funnel_events(platform, platform_user_id, birth_year);
        CREATE INDEX IF NOT EXISTS idx_birthday_funnel_events_status
            ON birthday_funnel_events(status);
        CREATE INDEX IF NOT EXISTS idx_cancellation_recovery_events_due
            ON cancellation_recovery_events(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_cancellation_recovery_events_user
            ON cancellation_recovery_events(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_repeat_visit_events_due
            ON repeat_visit_events(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_repeat_visit_events_user
            ON repeat_visit_events(platform, platform_user_id);

        CREATE INDEX IF NOT EXISTS idx_notification_delivery_platform_user_id
            ON notification_delivery(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_notification_delivery_message_type
            ON notification_delivery(message_type);
        CREATE INDEX IF NOT EXISTS idx_notification_delivery_status
            ON notification_delivery(status);
        CREATE INDEX IF NOT EXISTS idx_notification_delivery_created_at
            ON notification_delivery(created_at);
        CREATE INDEX IF NOT EXISTS idx_settings_audit_log_actor
            ON settings_audit_log(platform, actor_platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_settings_audit_log_action
            ON settings_audit_log(action);
        CREATE INDEX IF NOT EXISTS idx_settings_audit_log_section
            ON settings_audit_log(section);
        CREATE INDEX IF NOT EXISTS idx_settings_audit_log_created_at
            ON settings_audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
            ON audit_log(event_type);
        CREATE INDEX IF NOT EXISTS idx_audit_log_target_platform_user_id
            ON audit_log(target_platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
            ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_bot_logs_ts_utc
            ON bot_logs(ts_utc);
        CREATE INDEX IF NOT EXISTS idx_user_events_platform_user_id
            ON user_events(platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_user_events_username
            ON user_events(username);
        CREATE INDEX IF NOT EXISTS idx_user_events_event_name
            ON user_events(event_name);
        CREATE INDEX IF NOT EXISTS idx_user_events_ts_utc
            ON user_events(ts_utc);
        CREATE INDEX IF NOT EXISTS idx_master_photos_staff
            ON master_photos(platform, yclients_staff_id);
        CREATE INDEX IF NOT EXISTS idx_master_photos_active
            ON master_photos(platform, is_active);
        CREATE INDEX IF NOT EXISTS idx_feedback_requests_pending
            ON feedback_requests(platform, status, requested_at);
        CREATE INDEX IF NOT EXISTS idx_feedback_responses_user
            ON feedback_responses(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_notification_history_platform_user_id
            ON notification_history(platform, platform_user_id);
        CREATE INDEX IF NOT EXISTS idx_notification_history_yclients_record_id
            ON notification_history(yclients_record_id);
        CREATE INDEX IF NOT EXISTS idx_notification_history_notification_type
            ON notification_history(notification_type);
        CREATE INDEX IF NOT EXISTS idx_notification_history_status
            ON notification_history(status);
        CREATE INDEX IF NOT EXISTS idx_notification_history_scheduled_for
            ON notification_history(scheduled_for);
        """
    )
    _ensure_column(connection, "users", "display_name", "TEXT")
    _ensure_column(connection, "users", "first_name", "TEXT")
    _ensure_column(connection, "users", "last_name", "TEXT")
    _ensure_column(connection, "users", "username", "TEXT")
    _ensure_column(connection, "users", "birthdate", "TEXT")
    _ensure_column(
        connection,
        "users",
        "notification_settings_json",
        "TEXT NOT NULL DEFAULT '{}'",
    )
    _ensure_column(connection, "birthday_funnel_events", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "birthday_funnel_events", "platform_user_id", "TEXT")
    _ensure_column(connection, "birthday_funnel_events", "birth_year", "INTEGER")
    _ensure_column(connection, "birthday_funnel_events", "notification_type", "TEXT")
    _ensure_column(connection, "birthday_funnel_events", "status", "TEXT")
    _ensure_column(connection, "birthday_funnel_events", "sent_at", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "cancellation_recovery_events", "platform_user_id", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "yclients_record_id", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "yclients_client_id", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "max_user_id", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "chat_id", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "scheduled_at", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "status", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "sent_at", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "skipped_reason", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "created_at", "TEXT")
    _ensure_column(connection, "cancellation_recovery_events", "updated_at", "TEXT")

    _ensure_column(connection, "repeat_visit_events", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "repeat_visit_events", "platform_user_id", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "yclients_record_id", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "yclients_client_id", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "scheduled_at", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "status", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "sent_at", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "skipped_reason", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "created_at", "TEXT")
    _ensure_column(connection, "repeat_visit_events", "updated_at", "TEXT")

    _ensure_column(connection, "yclients_settings", "company_id", "TEXT")
    _ensure_column(connection, "yclients_settings", "partner_token", "TEXT")
    _ensure_column(connection, "yclients_settings", "user_token", "TEXT")
    _ensure_column(
        connection,
        "yclients_settings",
        "branch_timezone",
        f"TEXT NOT NULL DEFAULT '{DEFAULT_BRANCH_TIMEZONE}'",
    )
    _ensure_column(connection, "yclients_settings", "branch_title", "TEXT")
    _ensure_column(connection, "yclients_settings", "contacts_override_json", "TEXT")
    _ensure_column(connection, "yclients_settings", "yandex_maps_url", "TEXT")
    _ensure_column(connection, "yclients_settings", "yandex_maps_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "yclients_settings", "twogis_url", "TEXT")
    _ensure_column(connection, "yclients_settings", "twogis_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "yclients_settings", "google_maps_url", "TEXT")
    _ensure_column(connection, "yclients_settings", "google_maps_enabled", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "yclients_settings", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "yclients_settings", "created_at", "TEXT")
    _ensure_column(connection, "yclients_settings", "updated_at", "TEXT")
    _ensure_column(connection, "support_settings", "support_username", "TEXT")
    _ensure_column(connection, "support_settings", "support_max_username", "TEXT")
    _ensure_column(connection, "support_settings", "support_description", "TEXT")
    _ensure_column(connection, "support_settings", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "support_settings", "created_at", "TEXT")
    _ensure_column(connection, "support_settings", "updated_at", "TEXT")
    _ensure_column(connection, "settings_audit_log", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "settings_audit_log", "actor_platform_user_id", "TEXT")
    _ensure_column(connection, "settings_audit_log", "actor_role", "TEXT")
    _ensure_column(connection, "settings_audit_log", "section", "TEXT")
    _ensure_column(connection, "settings_audit_log", "target_platform_user_id", "TEXT")
    _ensure_column(connection, "settings_audit_log", "metadata_json", "TEXT")
    _ensure_column(connection, "settings_audit_log", "created_at", "TEXT")
    _ensure_column(connection, "audit_log", "event_type", "TEXT")
    _ensure_column(connection, "audit_log", "actor_platform_user_id", "TEXT")
    _ensure_column(connection, "audit_log", "target_platform_user_id", "TEXT")
    _ensure_column(connection, "audit_log", "target_max_user_id", "TEXT")
    _ensure_column(connection, "audit_log", "old_role", "TEXT")
    _ensure_column(connection, "audit_log", "new_role", "TEXT")
    _ensure_column(connection, "audit_log", "metadata_json", "TEXT")
    _ensure_column(connection, "audit_log", "created_at", "TEXT")
    _ensure_column(connection, "bot_logs", "ts_utc", "TEXT")
    _ensure_column(connection, "bot_logs", "level", "TEXT")
    _ensure_column(connection, "bot_logs", "source", "TEXT")
    _ensure_column(connection, "bot_logs", "message", "TEXT")
    _ensure_column(connection, "bot_logs", "details_json", "TEXT")
    _ensure_column(connection, "user_events", "ts_utc", "TEXT")
    _ensure_column(connection, "user_events", "platform_user_id", "TEXT")
    _ensure_column(connection, "user_events", "max_user_id", "TEXT")
    _ensure_column(connection, "user_events", "chat_id", "TEXT")
    _ensure_column(connection, "user_events", "username", "TEXT")
    _ensure_column(connection, "user_events", "phone", "TEXT")
    _ensure_column(connection, "user_events", "event_type", "TEXT")
    _ensure_column(connection, "user_events", "event_name", "TEXT")
    _ensure_column(connection, "user_events", "screen", "TEXT")
    _ensure_column(connection, "user_events", "payload_json", "TEXT")
    _ensure_column(connection, "master_photos", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "master_photos", "yclients_staff_id", "TEXT")
    _ensure_column(connection, "master_photos", "master_name", "TEXT")
    _ensure_column(connection, "master_photos", "photo_file_id", "TEXT")
    _ensure_column(connection, "master_photos", "photo_url", "TEXT")
    _ensure_column(connection, "master_photos", "photo_attachment_json", "TEXT")
    _ensure_column(connection, "master_photos", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "master_photos", "created_by_platform_user_id", "TEXT")
    _ensure_column(connection, "master_photos", "updated_by_platform_user_id", "TEXT")
    _ensure_column(connection, "master_photos", "created_at", "TEXT")
    _ensure_column(connection, "master_photos", "updated_at", "TEXT")
    _ensure_column(connection, "feedback_requests", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "feedback_requests", "platform_user_id", "TEXT")
    _ensure_column(connection, "feedback_requests", "yclients_record_id", "TEXT")
    _ensure_column(connection, "feedback_requests", "yclients_client_id", "TEXT")
    _ensure_column(connection, "feedback_requests", "status", "TEXT")
    _ensure_column(connection, "feedback_requests", "requested_at", "TEXT")
    _ensure_column(connection, "feedback_requests", "completed_at", "TEXT")
    _ensure_column(connection, "feedback_requests", "skipped_at", "TEXT")
    _ensure_column(connection, "feedback_requests", "error", "TEXT")
    _ensure_column(connection, "feedback_requests", "updated_at", "TEXT")
    _ensure_column(connection, "feedback_responses", "platform", "TEXT NOT NULL DEFAULT 'max'")
    _ensure_column(connection, "feedback_responses", "platform_user_id", "TEXT")
    _ensure_column(connection, "feedback_responses", "yclients_record_id", "TEXT")
    _ensure_column(connection, "feedback_responses", "rating", "INTEGER")
    _ensure_column(connection, "feedback_responses", "comment", "TEXT")
    _ensure_column(connection, "feedback_responses", "is_negative", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "feedback_responses", "admin_notified_at", "TEXT")
    _ensure_column(connection, "notification_history", "max_user_id", "TEXT")
    _ensure_column(connection, "notification_history", "chat_id", "TEXT")
    _ensure_column(connection, "notification_history", "yclients_client_id", "TEXT")
    _ensure_column(connection, "notification_history", "scheduled_for", "TEXT")
    _ensure_column(connection, "notification_history", "sent_at", "TEXT")
    _ensure_column(connection, "notification_history", "delivery_status_code", "INTEGER")
    _ensure_column(connection, "notification_history", "delivery_error_code", "TEXT")
    _ensure_column(connection, "notification_history", "delivery_error_message", "TEXT")
    _ensure_column(connection, "notification_history", "message_id", "TEXT")
    _ensure_column(connection, "notification_history", "attempts", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "notification_history", "is_blocked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "notification_history", "is_stopped", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "notification_history", "metadata_json", "TEXT")
    _ensure_column(connection, "notification_history", "updated_at", "TEXT")
    connection.commit()


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    """Add a missing column for databases created before the current schema."""

    existing_columns = {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
