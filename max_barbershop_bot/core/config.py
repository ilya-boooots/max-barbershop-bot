"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_APP_ENV = "local"
DEFAULT_DEV_LEGACY_ID = "378881880"
DEFAULT_DATABASE_PATH = "data/max_barbershop_bot.sqlite3"
DEFAULT_SUPPORT_USERNAME = "@XXX"
DEFAULT_REMINDERS_ENABLED = False
DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS = 300
DEFAULT_DEVELOPER_DIAGNOSTICS_ENABLED = True


class ConfigError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Runtime settings loaded from environment variables only."""

    max_bot_token: str
    log_level: str = DEFAULT_LOG_LEVEL
    app_env: str = DEFAULT_APP_ENV
    dev_legacy_user_id: str = DEFAULT_DEV_LEGACY_ID
    dev_max_user_id: str | None = None
    database_path: str = DEFAULT_DATABASE_PATH
    support_username: str = DEFAULT_SUPPORT_USERNAME
    reminders_enabled: bool = DEFAULT_REMINDERS_ENABLED
    reminders_poll_interval_seconds: int = DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS
    developer_diagnostics_enabled: bool = DEFAULT_DEVELOPER_DIAGNOSTICS_ENABLED


def load_config() -> Config:
    """Load and validate configuration from environment variables."""

    max_bot_token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not max_bot_token:
        raise ConfigError(
            "MAX_BOT_TOKEN не задан. Укажите токен MAX-бота в переменной окружения "
            "MAX_BOT_TOKEN и запустите приложение повторно."
        )

    return Config(
        max_bot_token=max_bot_token,
        log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip() or DEFAULT_LOG_LEVEL,
        app_env=os.getenv("APP_ENV", DEFAULT_APP_ENV).strip() or DEFAULT_APP_ENV,
        dev_legacy_user_id=os.getenv("DEV_TG_ID", DEFAULT_DEV_LEGACY_ID).strip()
        or DEFAULT_DEV_LEGACY_ID,
        dev_max_user_id=_optional_env("DEV_MAX_USER_ID"),
        database_path=os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip()
        or DEFAULT_DATABASE_PATH,
        support_username=normalize_support_username(
            os.getenv("SUPPORT_USERNAME", DEFAULT_SUPPORT_USERNAME)
        ),
        reminders_enabled=_bool_env("REMINDERS_ENABLED", DEFAULT_REMINDERS_ENABLED),
        reminders_poll_interval_seconds=_int_env(
            "REMINDERS_POLL_INTERVAL_SECONDS",
            DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS,
            minimum=30,
        ),
        developer_diagnostics_enabled=_bool_env(
            "DEVELOPER_DIAGNOSTICS_ENABLED",
            DEFAULT_DEVELOPER_DIAGNOSTICS_ENABLED,
        ),
    )


def _optional_env(name: str) -> str | None:
    """Return a stripped optional environment variable value."""

    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_support_username(raw: str | None) -> str:
    """Normalize support username for display as @username."""

    value = (raw or "").strip() or DEFAULT_SUPPORT_USERNAME
    value = value.lstrip("@").strip()
    if not value:
        value = DEFAULT_SUPPORT_USERNAME.lstrip("@")
    return f"@{value}"


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return max(minimum, parsed)
