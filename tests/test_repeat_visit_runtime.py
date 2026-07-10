from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from max_barbershop_bot.core.config import load_config
from max_barbershop_bot.services import repeat_visit
from max_barbershop_bot import main as runtime


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for key in ("MAX_BOT_TOKEN", "REPEAT_VISIT_ENABLED", "REPEAT_VISIT_POLL_INTERVAL_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAX_BOT_TOKEN", "max-token")
    monkeypatch.chdir(tmp_path)


def test_config_repeat_visit_defaults_disabled() -> None:
    config = load_config()
    assert config.repeat_visit_enabled is False
    assert config.repeat_visit_poll_interval_seconds == 3600


def test_config_repeat_visit_enabled_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPEAT_VISIT_ENABLED", "true")
    assert load_config().repeat_visit_enabled is True


@pytest.mark.parametrize("raw, expected", [("300", 300), ("299", 300), ("bad", 3600)])
def test_config_repeat_visit_interval_minimum_and_invalid(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("REPEAT_VISIT_POLL_INTERVAL_SECONDS", raw)
    assert load_config().repeat_visit_poll_interval_seconds == expected


def test_loop_calls_existing_process_function_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    stop_event = asyncio.Event()

    async def fake_process(sender, *, database_path: str):
        calls.append(database_path)
        stop_event.set()
        return 1

    monkeypatch.setattr(repeat_visit, "process_due_repeat_visit_events", fake_process)
    asyncio.run(repeat_visit.run_repeat_visit_loop(object(), database_path="db.sqlite3", stop_event=stop_event, interval_seconds=300))

    assert calls == ["db.sqlite3"]


def test_process_due_schedules_before_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def fake_schedule(*, database_path: str, settings=None):
        order.append("schedule")
        return 0

    class Repo:
        def __init__(self, database_path: str):
            order.append("repo")
        def find_due(self, now_iso: str, *, limit: int):
            order.append("find_due")
            return []

    monkeypatch.setattr(repeat_visit, "schedule_repeat_visit_events", fake_schedule)
    monkeypatch.setattr(repeat_visit, "RepeatVisitEventsRepository", Repo)

    asyncio.run(repeat_visit.process_due_repeat_visit_events(object(), database_path="db.sqlite3"))

    assert order == ["schedule", "repo", "find_due"]


def test_loop_reports_errors_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_event = asyncio.Event()
    seen: list[tuple[str, str]] = []
    attempts = 0

    async def fake_process(sender, *, database_path: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        stop_event.set()
        return 0

    async def fake_wait(awaitable, *, timeout: float):
        awaitable.close()
        assert timeout == 300
        if attempts >= 2:
            stop_event.set()
        return None

    async def errors(*, location: str, exception: Exception):
        seen.append((location, type(exception).__name__))

    monkeypatch.setattr(repeat_visit, "process_due_repeat_visit_events", fake_process)
    monkeypatch.setattr(repeat_visit.asyncio, "wait_for", fake_wait)

    asyncio.run(repeat_visit.run_repeat_visit_loop(object(), database_path="db.sqlite3", stop_event=stop_event, interval_seconds=1, error_callback=errors))

    assert attempts == 2
    assert seen == [("repeat_visit_loop", "RuntimeError")]


def test_loop_reraises_cancelled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process(sender, *, database_path: str):
        raise asyncio.CancelledError

    monkeypatch.setattr(repeat_visit, "process_due_repeat_visit_events", fake_process)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(repeat_visit.run_repeat_visit_loop(object(), database_path="db.sqlite3", stop_event=asyncio.Event(), interval_seconds=300))


def test_main_starts_repeat_visit_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def fake_poll(client, sender, router, stop_event, diagnostics):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stop_event.set()

    async def fake_repeat(sender, *, database_path: str, stop_event: asyncio.Event, interval_seconds: int, error_callback):
        started.append(f"{database_path}:{interval_seconds}")
        await stop_event.wait()

    monkeypatch.setattr(runtime, "_install_signal_handlers", lambda stop_event, signals: None)
    monkeypatch.setattr(runtime, "create_router", lambda config: object())
    monkeypatch.setattr(runtime, "_poll_dev_updates", fake_poll)
    class FakeSettingsRepository:
        def __init__(self, database_path: str):
            pass
        def notifications_enabled(self) -> bool:
            return False
        def notification_setting_source(self) -> str:
            return "test"

    monkeypatch.setattr(runtime, "AppSettingsRepository", FakeSettingsRepository)
    async def fake_start_reminder(*args, **kwargs):
        return None
    monkeypatch.setattr(runtime, "start_reminder_lifecycle", fake_start_reminder)
    monkeypatch.setattr(runtime, "shutdown_reminder_lifecycle", lambda: asyncio.sleep(0))
    monkeypatch.setattr(runtime, "run_repeat_visit_loop", fake_repeat)

    config = load_config()
    asyncio.run(runtime._run_dev_polling_runtime(object(), config))
    assert started == []

    monkeypatch.setenv("REPEAT_VISIT_ENABLED", "true")
    monkeypatch.setenv("REPEAT_VISIT_POLL_INTERVAL_SECONDS", "300")
    asyncio.run(runtime._run_dev_polling_runtime(object(), load_config()))
    assert started == [f"{load_config().database_path}:300"]
