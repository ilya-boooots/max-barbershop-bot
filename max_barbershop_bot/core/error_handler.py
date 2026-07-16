"""Generic error diagnostics for MAX runtime and router paths."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from os import getenv
from typing import Any

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH, Config
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.diagnostics import DiagnosticsRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.services.diagnostics import (
    GENERIC_ERROR_TEXT,
    alert_fingerprint,
    build_safe_error_context,
    generate_error_id,
    remember_error,
    render_developer_alert,
    sanitize_text,
)

logger = logging.getLogger(__name__)

_ALERT_THROTTLE_SECONDS = 600.0
_GLOBAL_ALERT_WINDOW_SECONDS = 60.0
_GLOBAL_ALERTS_PER_MINUTE = 3
_last_alert_by_fingerprint: dict[str, float] = {}
_alert_timestamps: deque[float] = deque()


@dataclass(frozen=True)
class ErrorDiagnostics:
    """Configuration and helpers for safe diagnostics delivery."""

    dev_max_user_id: str | None
    enabled: bool = True
    app_env: str = "local"

    @classmethod
    def from_config(cls, config: Config | None) -> "ErrorDiagnostics":
        if config is None:
            return cls(dev_max_user_id=None)
        return cls(
            dev_max_user_id=config.dev_max_user_id,
            enabled=config.developer_diagnostics_enabled,
            app_env=config.app_env,
        )

    async def handle_handler_exception(
        self,
        *,
        exception: Exception,
        event: NormalizedEvent,
        sender: MaxMessageSender,
        handler_name: str | None,
    ) -> str:
        """Log, notify the user, and alert developer about a handler failure."""

        error_id = generate_error_id()
        _sanitize_exception_args(exception)
        screen_id = state.get_current_screen(event.platform_user_id, event.chat_id)
        logger.error(
            "Unhandled MAX handler error error_id=%s update_type=%s handler=%s screen_id=%s "
            "platform_user_id_present=%s chat_id_present=%s callback_payload_present=%s error_class=%s",
            error_id,
            sanitize_text(event.update_type),
            sanitize_text(handler_name or "—"),
            sanitize_text(screen_id or "—"),
            event.platform_user_id is not None,
            event.chat_id is not None,
            event.callback_payload is not None,
            type(exception).__name__,
        )
        await self._notify_user_safely(sender=sender, event=event, error_id=error_id)
        await self._send_developer_alert_safely(
            sender=sender,
            exception=exception,
            event=event,
            handler_name=handler_name,
            location="handler_dispatch",
            screen_id=screen_id,
            error_id=error_id,
            throttle=True,
        )
        return error_id

    async def handle_runtime_exception(
        self,
        *,
        exception: Exception,
        sender: MaxMessageSender | None,
        location: str,
        event: NormalizedEvent | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> str:
        """Log and optionally alert developer for non-handler runtime failures."""

        error_id = generate_error_id()
        _sanitize_exception_args(exception)
        logger.error(
            "Unhandled MAX runtime error error_id=%s location=%s update_type=%s "
            "platform_user_id_present=%s chat_id_present=%s error_class=%s",
            error_id,
            sanitize_text(location),
            sanitize_text(event.update_type) if event else "—",
            bool(event and event.platform_user_id is not None),
            bool(event and event.chat_id is not None),
            type(exception).__name__,
        )
        if sender is not None:
            await self._send_developer_alert_safely(
                sender=sender,
                exception=exception,
                event=event,
                handler_name=None,
                location=location,
                screen_id=_screen_id(event),
                error_id=error_id,
                extra=extra,
                throttle=True,
            )
        return error_id

    async def _notify_user_safely(
        self,
        *,
        sender: MaxMessageSender,
        event: NormalizedEvent,
        error_id: str,
    ) -> None:
        del error_id
        keyboard = MaxInlineKeyboard.from_rows(
            [[MaxButton(text="🏠 Главное меню", payload="nav:home")]]
        )
        try:
            chat_id = _int_from_string(event.chat_id)
            if chat_id is not None:
                await sender.send_to_chat(chat_id, GENERIC_ERROR_TEXT, keyboard=keyboard)
                return
            user_id = _int_from_string(event.max_user_id or event.platform_user_id)
            if user_id is not None:
                await sender.send_to_user(user_id, GENERIC_ERROR_TEXT, keyboard=keyboard)
                return
            logger.warning("Cannot send generic error message: recipient is missing")
        except Exception:
            logger.exception("Failed to notify user about generic MAX error")

    async def _send_developer_alert_safely(
        self,
        *,
        sender: MaxMessageSender,
        exception: BaseException,
        event: NormalizedEvent | None,
        handler_name: str | None,
        location: str,
        screen_id: str | None,
        error_id: str,
        extra: Mapping[str, Any] | None = None,
        throttle: bool = False,
    ) -> None:
        context = build_safe_error_context(
            error_id=error_id,
            exception=exception,
            event=event,
            handler_name=handler_name,
            location=location,
            screen_id=screen_id,
            extra={"app_env": self.app_env, **dict(extra or {})},
        )
        context["role"] = _resolve_role(event)
        context["exception_message"] = "<message_hidden>"
        context["traceback_tail"] = "<traceback_hidden>"
        if context.get("callback_payload") not in (None, "", "—"):
            context["callback_payload"] = "<payload_hidden>"
        remember_error(context)
        _persist_error_safely(context)
        if not self.enabled:
            logger.debug("Developer diagnostics disabled; alert skipped error_id=%s", error_id)
            return
        developer_id = _int_from_string(self.dev_max_user_id)
        if developer_id is None:
            logger.debug("DEV_MAX_USER_ID is missing or invalid; developer alert skipped error_id=%s", error_id)
            return
        fingerprint = alert_fingerprint(exception, location=location)
        if throttle and not _alert_allowed(fingerprint):
            logger.debug("Developer alert throttled error_id=%s location=%s", error_id, location)
            return
        try:
            result = await sender.send_to_user(developer_id, render_developer_alert(context))
            if not result.ok:
                logger.warning(
                    "Developer diagnostic alert was not delivered error_id=%s status_code=%s error_code=%s",
                    error_id,
                    result.status_code,
                    result.error_code,
                )
        except Exception:
            logger.exception("Failed to send developer diagnostic alert error_id=%s", error_id)


def _screen_id(event: NormalizedEvent | None) -> str | None:
    if event is None:
        return None
    return state.get_current_screen(event.platform_user_id, event.chat_id)


def _int_from_string(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _alert_allowed(fingerprint: str) -> bool:
    now = time.monotonic()
    while _alert_timestamps and now - _alert_timestamps[0] >= _GLOBAL_ALERT_WINDOW_SECONDS:
        _alert_timestamps.popleft()
    last = _last_alert_by_fingerprint.get(fingerprint)
    if last is not None and now - last < _ALERT_THROTTLE_SECONDS:
        return False
    if len(_alert_timestamps) >= _GLOBAL_ALERTS_PER_MINUTE:
        return False
    _last_alert_by_fingerprint[fingerprint] = now
    _alert_timestamps.append(now)
    return True


def _persist_error_safely(context: Mapping[str, Any]) -> None:
    try:
        DiagnosticsRepository(_database_path()).log_error_event(context)
    except Exception:
        logger.exception("Failed to persist safe MAX error context")


def _sanitize_exception_args(exception: BaseException) -> None:
    if not exception.args:
        return
    try:
        exception.args = tuple(sanitize_text(str(arg)) for arg in exception.args)
    except Exception:
        return


def _resolve_role(event: NormalizedEvent | None) -> str:
    if event is None or not event.platform_user_id:
        return "unknown"
    try:
        role = StaffRolesRepository(getenv("DATABASE_PATH", "data/max_barbershop_bot.sqlite3")).get_highest_role(
            event.platform_user_id
        )
        return role or "user"
    except Exception:
        return "unknown"


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
