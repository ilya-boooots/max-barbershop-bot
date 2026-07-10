from __future__ import annotations

import asyncio
import inspect

import pytest

import max_barbershop_bot.main as runtime
from max_barbershop_bot.core.config import load_config
from max_barbershop_bot.services import lost_clients


def test_lost_clients_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("max_barbershop_bot.core.config._load_dotenv_values", lambda: ({}, []))
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.delenv("LOST_CLIENTS_ENABLED", raising=False)
    monkeypatch.delenv("LOST_CLIENTS_POLL_INTERVAL_SECONDS", raising=False)

    config = load_config()

    assert config.lost_clients_enabled is False
    assert config.lost_clients_poll_interval_seconds == 3600


def test_lost_clients_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("max_barbershop_bot.core.config._load_dotenv_values", lambda: ({}, []))
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.setenv("LOST_CLIENTS_ENABLED", "true")
    monkeypatch.setenv("LOST_CLIENTS_POLL_INTERVAL_SECONDS", "300")

    config = load_config()

    assert config.lost_clients_enabled is True
    assert config.lost_clients_poll_interval_seconds == 300


def test_lost_clients_config_interval_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("max_barbershop_bot.core.config._load_dotenv_values", lambda: ({}, []))
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.setenv("LOST_CLIENTS_POLL_INTERVAL_SECONDS", "60")

    config = load_config()

    assert config.lost_clients_poll_interval_seconds == 300


def test_run_lost_clients_loop_calls_scan_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    calls: list[str] = []

    async def fake_scan(sender: object, *, database_path: str) -> object:
        calls.append(database_path)
        stop_event.set()
        return object()

    monkeypatch.setattr(lost_clients, "run_lost_clients_scan", fake_scan)

    asyncio.run(lost_clients.run_lost_clients_loop(object(), database_path="db.sqlite3", stop_event=stop_event, interval_seconds=300))

    assert calls == ["db.sqlite3"]


def test_run_lost_clients_loop_error_callback_keeps_loop_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    calls = 0
    errors: list[Exception] = []

    async def fake_scan(sender: object, *, database_path: str) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        stop_event.set()
        return object()

    async def fake_wait() -> bool:
        return stop_event.is_set()

    async def fake_wait_for(awaitable: object, timeout: float) -> bool:
        await awaitable
        if not stop_event.is_set():
            raise TimeoutError
        return True

    async def error_callback(exc: Exception) -> None:
        errors.append(exc)

    monkeypatch.setattr(lost_clients, "run_lost_clients_scan", fake_scan)
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(stop_event, "wait", fake_wait)

    asyncio.run(lost_clients.run_lost_clients_loop(
        object(),
        database_path="db.sqlite3",
        stop_event=stop_event,
        interval_seconds=1,
        error_callback=error_callback,
    ))

    assert calls == 2
    assert [type(error).__name__ for error in errors] == ["RuntimeError"]


def test_run_lost_clients_loop_cancelled_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_scan(sender: object, *, database_path: str) -> object:
        raise asyncio.CancelledError

    monkeypatch.setattr(lost_clients, "run_lost_clients_scan", fake_scan)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lost_clients.run_lost_clients_loop(
            object(),
            database_path="db.sqlite3",
            stop_event=asyncio.Event(),
            interval_seconds=300,
        ))


def test_main_starts_lost_clients_task_only_when_enabled() -> None:
    source = inspect.getsource(runtime._run_dev_polling_runtime)

    assert "if config.lost_clients_enabled:" in source
    assert "run_lost_clients_loop" in source
    assert "config.lost_clients_poll_interval_seconds" in source
    assert 'name="lost-clients"' in source
    assert "Lost clients disabled by LOST_CLIENTS_ENABLED" in source
