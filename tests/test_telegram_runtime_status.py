from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from max_barbershop_bot.core.config import Config, load_config
from max_barbershop_bot.core.telegram_runtime import build_telegram_runtime_status
from max_barbershop_bot.flows.broadcasts import _format_telegram_admin_diagnostics, _telegram_delivery_dependencies
from max_barbershop_bot.services.omnichannel_broadcasts import TelegramBotApiBroadcastAdapter


def _db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY, chat_id TEXT, phone TEXT)")
        connection.execute("INSERT INTO users (user_id, chat_id, phone) VALUES ('1', '1', '+79990000000')")


def _config(db_path: Path, *, token: str | None = "token") -> Config:
    return Config(max_bot_token="max", telegram_bot_token=token, telegram_db_path=str(db_path), env_file_checked=("/tmp/.env",))


def test_env_vars_present_db_exists_users_table_status_adapter_real(tmp_path: Path) -> None:
    db_path = tmp_path / "telegram.sqlite3"
    _db(db_path)

    status = build_telegram_runtime_status(_config(db_path))

    assert status.token_configured is True
    assert status.db_path_configured is True
    assert status.db_exists is True
    assert status.db_readable is True
    assert status.users_table_found is True
    assert status.adapter_kind == "real"
    assert status.unavailable_reason is None


def test_env_fallback_works_when_cwd_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "telegram.sqlite3"
    _db(db_path)
    project_env = Path(__file__).resolve().parents[1] / ".env"
    old_text = project_env.read_text(encoding="utf-8") if project_env.exists() else None
    project_env.write_text(
        f"MAX_BOT_TOKEN=max\nTELEGRAM_BOT_TOKEN=token\nTELEGRAM_DB_PATH={db_path}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for key in ("MAX_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    try:
        config = load_config()
    finally:
        if old_text is None:
            project_env.unlink(missing_ok=True)
        else:
            project_env.write_text(old_text, encoding="utf-8")

    status = build_telegram_runtime_status(config)
    assert status.adapter_kind == "real"
    assert str(project_env) in config.env_file_checked


def test_preview_report_uses_same_status_as_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "telegram.sqlite3"
    _db(db_path)
    monkeypatch.setenv("MAX_BOT_TOKEN", "max")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_DB_PATH", str(db_path))

    adapter, _repo, reason, diagnostics = _telegram_delivery_dependencies()
    text = _format_telegram_admin_diagnostics(diagnostics)

    assert isinstance(adapter, TelegramBotApiBroadcastAdapter)
    assert reason is None
    assert diagnostics["adapter_kind"] == "real"
    assert "adapter: real" in text
    assert "Причина:" not in text


def test_missing_token_reason(tmp_path: Path) -> None:
    db_path = tmp_path / "telegram.sqlite3"
    _db(db_path)
    status = build_telegram_runtime_status(_config(db_path, token=None))
    assert status.adapter_kind == "unavailable"
    assert status.unavailable_reason == "token_missing"


def test_missing_db_path_reason() -> None:
    status = build_telegram_runtime_status(Config(max_bot_token="max", telegram_bot_token="token", telegram_db_path=None))
    assert status.adapter_kind == "unavailable"
    assert status.unavailable_reason == "db_path_missing"
