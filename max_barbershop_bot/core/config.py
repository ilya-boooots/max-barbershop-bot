"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_APP_ENV = "local"
DEFAULT_DEV_TG_ID = "378881880"


class ConfigError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Runtime settings loaded from environment variables only."""

    max_bot_token: str
    log_level: str = DEFAULT_LOG_LEVEL
    app_env: str = DEFAULT_APP_ENV
    dev_tg_id: str = DEFAULT_DEV_TG_ID


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
        dev_tg_id=os.getenv("DEV_TG_ID", DEFAULT_DEV_TG_ID).strip() or DEFAULT_DEV_TG_ID,
    )
