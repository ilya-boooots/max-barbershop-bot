"""Lifecycle manager for MAX booking reminder loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.services.reminders import ReminderLoopStatus, get_reminder_loop_status, run_reminder_loop

logger = logging.getLogger(__name__)

LoopState = Literal["running", "stopped", "disabled", "error"]


@dataclass(frozen=True)
class ReminderLifecycleStatus:
    """Public notification lifecycle status for settings and diagnostics."""

    state: LoopState
    is_running: bool
    task_present: bool
    last_error_class: str | None = None
    last_error_at: datetime | None = None
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    interval_seconds: int | None = None
    stop_reason: str | None = None


_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_interval_seconds: int | None = None
_stop_reason: str | None = None
_last_error_class: str | None = None
_last_error_at: datetime | None = None


def get_lifecycle_status() -> ReminderLifecycleStatus:
    """Return current reminder loop lifecycle status without side effects."""

    global _task
    reminder_status = get_reminder_loop_status()
    task_present = _task is not None and not _task.done()
    if task_present and reminder_status.is_running:
        state: LoopState = "running"
    elif _last_error_class or (reminder_status.last_error_class and not task_present and _stop_reason != "notifications_disabled"):
        state = "error"
    elif _stop_reason == "notifications_disabled":
        state = "disabled"
    else:
        state = "stopped"
    return ReminderLifecycleStatus(
        state=state,
        is_running=state == "running",
        task_present=task_present,
        last_error_class=_last_error_class or reminder_status.last_error_class,
        last_error_at=_last_error_at or reminder_status.last_error_at,
        last_started_at=reminder_status.last_started_at,
        last_success_at=reminder_status.last_success_at,
        interval_seconds=_interval_seconds,
        stop_reason=_stop_reason,
    )


def is_running() -> bool:
    """Return whether a reminder loop task is currently running."""

    return get_lifecycle_status().is_running


async def start_reminder_lifecycle(
    sender: MaxMessageSender,
    *,
    database_path: str,
    interval_seconds: int,
    error_callback: Callable[[Exception], Awaitable[object]] | None = None,
) -> ReminderLifecycleStatus:
    """Start reminder loop once; return status without creating duplicate tasks."""

    global _task, _stop_event, _interval_seconds, _stop_reason, _last_error_class, _last_error_at
    interval = max(30, int(interval_seconds))
    if _task is not None and not _task.done():
        _interval_seconds = interval
        _log_lifecycle(start_result="already_running", interval_seconds=interval)
        return get_lifecycle_status()

    if _task is not None and _task.done() and not _task.cancelled():
        error = _task.exception()
        if error is not None:
            _last_error_class = type(error).__name__
            _last_error_at = datetime.now(UTC)

    _stop_event = asyncio.Event()
    _interval_seconds = interval
    _stop_reason = None
    _last_error_class = None
    _last_error_at = None
    _task = asyncio.create_task(
        run_reminder_loop(
            sender,
            database_path=database_path,
            stop_event=_stop_event,
            interval_seconds=interval,
            error_callback=error_callback,
        ),
        name="booking-reminders",
    )
    _task.add_done_callback(_on_task_done)
    _log_lifecycle(startup_attempted=True, start_result="started", interval_seconds=interval)
    await asyncio.sleep(0)
    return get_lifecycle_status()


async def stop_reminder_lifecycle(*, reason: str = "notifications_disabled") -> ReminderLifecycleStatus:
    """Stop reminder loop task safely if it exists."""

    global _task, _stop_event, _stop_reason
    _stop_reason = reason
    if _task is None or _task.done():
        _log_lifecycle(stop_result="already_stopped", reminder_loop_running=False)
        return get_lifecycle_status()
    if _stop_event is not None:
        _stop_event.set()
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    _stop_event = None
    _log_lifecycle(stop_result="stopped", reminder_loop_running=False)
    return get_lifecycle_status()


async def shutdown_reminder_lifecycle() -> None:
    """Stop reminder loop during application shutdown."""

    await stop_reminder_lifecycle(reason="shutdown")


def _on_task_done(task: asyncio.Task) -> None:
    global _last_error_class, _last_error_at
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _last_error_class = type(error).__name__
        _last_error_at = datetime.now(UTC)
        logger.warning(
            "MAX notifications lifecycle diagnostic: %s",
            {
                "reminder_loop_running": False,
                "reminder_loop_task_present": False,
                "reminder_loop_last_error_present": True,
                "start_result": "task_crashed",
            },
        )


def _log_lifecycle(**fields: object) -> None:
    safe = {
        key: value
        for key, value in fields.items()
        if key in {
            "notifications_enabled",
            "setting_source",
            "reminder_loop_running",
            "reminder_loop_task_present",
            "reminder_loop_last_error_present",
            "interval_seconds",
            "startup_attempted",
            "start_result",
            "stop_result",
            "toggle_action",
            "dry_run_button_test_result",
        }
    }
    status = get_lifecycle_status()
    safe.setdefault("reminder_loop_running", status.is_running)
    safe.setdefault("reminder_loop_task_present", status.task_present)
    safe.setdefault("reminder_loop_last_error_present", bool(status.last_error_class))
    logger.info("MAX notifications lifecycle diagnostic: %s", safe)
