from __future__ import annotations

from pathlib import Path

from app.core.config import ConfigError, Settings, get_settings


def get_config() -> Settings:
    return get_settings()


def get_db_path() -> Path:
    return get_settings().db_path


def get_app_secret_key() -> bytes:
    return get_settings().app_secret_key.encode("utf-8")


def get_protected_dev_tg_id() -> int:
    return get_settings().protected_dev_tg_id
