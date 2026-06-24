"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_APP_ENV = "local"
DEFAULT_DEV_LEGACY_ID = "378881880"
DEFAULT_DATABASE_PATH = "data/max_barbershop_bot.sqlite3"
DEFAULT_SUPPORT_USERNAME = "@XXX"
DEFAULT_REMINDERS_ENABLED = True
DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS = 300
DEFAULT_BIRTHDAY_FUNNEL_ENABLED = False
DEFAULT_BIRTHDAY_FUNNEL_POLL_INTERVAL_SECONDS = 3600
DEFAULT_CANCELLATION_RECOVERY_ENABLED = False
DEFAULT_CANCELLATION_RECOVERY_POLL_INTERVAL_SECONDS = 300
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
    birthday_funnel_enabled: bool = DEFAULT_BIRTHDAY_FUNNEL_ENABLED
    birthday_funnel_poll_interval_seconds: int = DEFAULT_BIRTHDAY_FUNNEL_POLL_INTERVAL_SECONDS
    cancellation_recovery_enabled: bool = DEFAULT_CANCELLATION_RECOVERY_ENABLED
    cancellation_recovery_poll_interval_seconds: int = DEFAULT_CANCELLATION_RECOVERY_POLL_INTERVAL_SECONDS
    developer_diagnostics_enabled: bool = DEFAULT_DEVELOPER_DIAGNOSTICS_ENABLED
    telegram_bot_token: str | None = None
    telegram_db_path: str | None = None
    telegram_test_chat_id: str | None = None
    config_source: str = "env"
    env_file_checked: tuple[str, ...] = ()


def load_config() -> Config:
    """Load and validate configuration from environment variables."""

    dotenv_values, env_file_checked = _load_dotenv_values()
    config_source = "config" if dotenv_values else "env"

    max_bot_token = _env_value("MAX_BOT_TOKEN", dotenv_values, default="").strip()
    if not max_bot_token:
        raise ConfigError(
            "MAX_BOT_TOKEN не задан. Укажите токен MAX-бота в переменной окружения "
            "MAX_BOT_TOKEN и запустите приложение повторно."
        )

    return Config(
        max_bot_token=max_bot_token,
        log_level=_env_value("LOG_LEVEL", dotenv_values, default=DEFAULT_LOG_LEVEL).strip() or DEFAULT_LOG_LEVEL,
        app_env=_env_value("APP_ENV", dotenv_values, default=DEFAULT_APP_ENV).strip() or DEFAULT_APP_ENV,
        dev_legacy_user_id=_env_value("DEV_TG_ID", dotenv_values, default=DEFAULT_DEV_LEGACY_ID).strip()
        or DEFAULT_DEV_LEGACY_ID,
        dev_max_user_id=_optional_env("DEV_MAX_USER_ID", dotenv_values),
        database_path=_env_value("DATABASE_PATH", dotenv_values, default=DEFAULT_DATABASE_PATH).strip()
        or DEFAULT_DATABASE_PATH,
        support_username=normalize_support_username(
            _env_value("SUPPORT_USERNAME", dotenv_values, default=DEFAULT_SUPPORT_USERNAME)
        ),
        reminders_enabled=_bool_env("REMINDERS_ENABLED", DEFAULT_REMINDERS_ENABLED, dotenv_values),
        reminders_poll_interval_seconds=_int_env(
            "REMINDERS_POLL_INTERVAL_SECONDS",
            DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS,
            dotenv_values,
            minimum=30,
        ),
        birthday_funnel_enabled=_bool_env("BIRTHDAY_FUNNEL_ENABLED", DEFAULT_BIRTHDAY_FUNNEL_ENABLED, dotenv_values),
        birthday_funnel_poll_interval_seconds=_int_env(
            "BIRTHDAY_FUNNEL_POLL_INTERVAL_SECONDS",
            DEFAULT_BIRTHDAY_FUNNEL_POLL_INTERVAL_SECONDS,
            dotenv_values,
            minimum=300,
        ),
        cancellation_recovery_enabled=_bool_env("CANCELLATION_RECOVERY_ENABLED", DEFAULT_CANCELLATION_RECOVERY_ENABLED, dotenv_values),
        cancellation_recovery_poll_interval_seconds=_int_env(
            "CANCELLATION_RECOVERY_POLL_INTERVAL_SECONDS",
            DEFAULT_CANCELLATION_RECOVERY_POLL_INTERVAL_SECONDS,
            dotenv_values,
            minimum=30,
        ),
        developer_diagnostics_enabled=_bool_env(
            "DEVELOPER_DIAGNOSTICS_ENABLED",
            DEFAULT_DEVELOPER_DIAGNOSTICS_ENABLED,
            dotenv_values,
        ),
        telegram_bot_token=_optional_env("TELEGRAM_BOT_TOKEN", dotenv_values),
        telegram_db_path=_optional_env("TELEGRAM_DB_PATH", dotenv_values),
        telegram_test_chat_id=_optional_env("TELEGRAM_TEST_CHAT_ID", dotenv_values),
        config_source=config_source,
        env_file_checked=tuple(env_file_checked),
    )


def _optional_env(name: str, dotenv_values: dict[str, str] | None = None) -> str | None:
    """Return a stripped optional environment variable value."""

    value = os.environ.get(name)
    if value is None and dotenv_values is not None:
        value = dotenv_values.get(name)
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


def _bool_env(name: str, default: bool, dotenv_values: dict[str, str] | None = None) -> bool:
    value = os.environ.get(name)
    if value is None and dotenv_values is not None:
        value = dotenv_values.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int_env(name: str, default: int, dotenv_values: dict[str, str] | None = None, *, minimum: int = 1) -> int:
    value = os.environ.get(name)
    if value is None and dotenv_values is not None:
        value = dotenv_values.get(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return max(minimum, parsed)


def _env_value(name: str, dotenv_values: dict[str, str], *, default: str) -> str:
    value = os.environ.get(name)
    if value is None:
        value = dotenv_values.get(name)
    return value if value is not None else default


def _load_dotenv_values() -> tuple[dict[str, str], list[str]]:
    paths = _dotenv_candidate_paths()
    checked = [str(path) for path in paths]
    path = next((candidate for candidate in paths if candidate.is_file()), None)
    if path is None:
        return {}, checked
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values, checked
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        value = value.strip().strip("\"'")
        values[key] = value
    return values, checked


def _dotenv_candidate_paths() -> list[Path]:
    """Return .env fallback paths in production-safe priority order."""

    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path("/opt/bots/max-barbershop-bot/.env"),
        project_root / ".env",
        Path.cwd() / ".env",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique
