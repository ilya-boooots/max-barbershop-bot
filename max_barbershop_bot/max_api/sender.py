"""Reliable MAX message sending helpers with structured delivery results."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from max_barbershop_bot.max_api.client import (
    MaxApiAuthError,
    MaxApiClient,
    MaxApiError,
    MaxApiNetworkError,
    MaxApiRateLimitError,
)
from max_barbershop_bot.max_api.models import MaxInlineKeyboard, MaxMessage

logger = logging.getLogger(__name__)

RecipientType = Literal["user", "chat", "callback"]
DEFAULT_MAX_SEND_ATTEMPTS = 3
DEFAULT_MAX_SEND_BASE_DELAY_SECONDS = 0.5

_EXPLICIT_RETRYABLE_STATUSES = {429, 503}
_EXPLICIT_NON_RETRYABLE_STATUSES = {400, 401, 404, 405}
_BLOCKED_ERROR_CODES = {"blocked", "bot_blocked", "user_blocked_bot"}
_STOPPED_ERROR_CODES = {"stopped", "user_stopped_bot"}


@dataclass(frozen=True)
class MaxSendResult:
    """Structured result returned by the safe MAX sending layer."""

    ok: bool
    status_code: int | None
    message_id: str | None
    recipient_type: str
    recipient_id: str
    error_code: str | None = None
    error_message: str | None = None
    is_retryable: bool = False
    is_blocked: bool = False
    is_stopped: bool = False
    attempts: int = 1
    raw_response: dict[str, Any] | None = None
    created_at: str | None = field(default_factory=lambda: datetime.now(UTC).isoformat())


def is_retryable_status(status_code: int | None) -> bool:
    """Return whether a MAX HTTP status should be retried by safe sender."""

    if status_code is None:
        return True
    if status_code == 200:
        return False
    if status_code in _EXPLICIT_RETRYABLE_STATUSES:
        return True
    if status_code in _EXPLICIT_NON_RETRYABLE_STATUSES:
        return False
    if 500 <= status_code <= 599:
        return True
    if 400 <= status_code <= 499:
        return False
    return False


class MaxMessageSender:
    """Safe wrapper around MaxApiClient for generic MAX message delivery."""

    def __init__(
        self,
        client: MaxApiClient,
        *,
        max_attempts: int = DEFAULT_MAX_SEND_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_MAX_SEND_BASE_DELAY_SECONDS,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._max_attempts = max(1, max_attempts)
        self._base_delay_seconds = max(0.0, base_delay_seconds)
        self._sleep = sleep

    async def send_to_user(
        self,
        user_id: int | str,
        text: str,
        *,
        keyboard: MaxInlineKeyboard | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        format: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MaxSendResult:
        """Send a generic text message to a MAX user safely."""

        del metadata
        return await self._send_message(
            recipient_type="user",
            recipient_id=str(user_id),
            text=text,
            user_id=user_id,
            keyboard=keyboard,
            attachments=attachments,
            text_format=format,
        )

    async def send_to_chat(
        self,
        chat_id: int | str,
        text: str,
        *,
        keyboard: MaxInlineKeyboard | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        format: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MaxSendResult:
        """Send a generic text message to a MAX chat safely."""

        del metadata
        return await self._send_message(
            recipient_type="chat",
            recipient_id=str(chat_id),
            text=text,
            chat_id=chat_id,
            keyboard=keyboard,
            attachments=attachments,
            text_format=format,
        )

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        text: str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> MaxSendResult:
        """Answer a MAX callback event safely."""

        async def operation() -> dict[str, Any]:
            return await self._client.answer_callback(
                callback_id=callback_id,
                notification=notification,
                text=text,
                keyboard=keyboard,
            )

        return await self._run_with_retry(
            operation,
            recipient_type="callback",
            recipient_id=callback_id,
        )

    async def _send_message(
        self,
        *,
        recipient_type: RecipientType,
        recipient_id: str,
        text: str,
        user_id: int | str | None = None,
        chat_id: int | str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        text_format: str | None = None,
    ) -> MaxSendResult:
        async def operation() -> MaxMessage | dict[str, Any] | None:
            return await self._client.send_message(
                user_id=user_id,  # type: ignore[arg-type]
                chat_id=chat_id,  # type: ignore[arg-type]
                text=text,
                keyboard=keyboard,
                attachments=attachments,
                text_format=text_format,
            )

        return await self._run_with_retry(
            operation,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
        )

    async def _run_with_retry(
        self,
        operation: Callable[[], Any],
        *,
        recipient_type: str,
        recipient_id: str,
    ) -> MaxSendResult:
        last_result: MaxSendResult | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await operation()
                result = _success_result(
                    response,
                    recipient_type=recipient_type,
                    recipient_id=recipient_id,
                    attempts=attempt,
                )
            except Exception as exc:
                result = _exception_result(
                    exc,
                    recipient_type=recipient_type,
                    recipient_id=recipient_id,
                    attempts=attempt,
                )

            if result.ok or not result.is_retryable or attempt >= self._max_attempts:
                if not result.ok:
                    _log_safe_failure(result)
                return result

            last_result = result
            logger.warning(
                "max_send_retryable recipient_type=%s recipient_id=%s status_code=%s "
                "attempt=%s/%s error_code=%s",
                recipient_type,
                recipient_id,
                result.status_code,
                attempt,
                self._max_attempts,
                result.error_code,
            )
            await self._sleep(self._backoff_delay(attempt))

        return last_result or MaxSendResult(
            ok=False,
            status_code=None,
            message_id=None,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            error_code="unknown",
            error_message="MAX send failed without result",
            is_retryable=False,
            attempts=self._max_attempts,
        )

    def _backoff_delay(self, attempt: int) -> float:
        return self._base_delay_seconds * (2 ** max(attempt - 1, 0))


def _success_result(
    response: Any,
    *,
    recipient_type: str,
    recipient_id: str,
    attempts: int,
) -> MaxSendResult:
    message_id = _extract_message_id(response)
    return MaxSendResult(
        ok=True,
        status_code=200,
        message_id=message_id,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        attempts=attempts,
        raw_response=_safe_raw_response(response),
    )


def _exception_result(
    exc: Exception,
    *,
    recipient_type: str,
    recipient_id: str,
    attempts: int,
) -> MaxSendResult:
    status_code, error_code, error_message = _extract_exception_details(exc)
    normalized_code = _normalize_error_code(error_code)
    is_blocked = normalized_code in _BLOCKED_ERROR_CODES
    is_stopped = normalized_code in _STOPPED_ERROR_CODES
    if status_code == 404 and normalized_code is None:
        normalized_code = "recipient_unavailable"
    retryable = is_retryable_status(status_code) and not is_blocked and not is_stopped

    return MaxSendResult(
        ok=False,
        status_code=status_code,
        message_id=None,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        error_code=normalized_code or _default_error_code(exc, status_code),
        error_message=_safe_error_message(error_message or str(exc)),
        is_retryable=retryable,
        is_blocked=is_blocked,
        is_stopped=is_stopped,
        attempts=attempts,
        raw_response=None,
    )


def _extract_exception_details(exc: Exception) -> tuple[int | None, str | None, str | None]:
    if isinstance(exc, MaxApiError):
        return exc.status, exc.code, str(exc)
    if isinstance(exc, TimeoutError):
        return None, "timeout", "MAX API request timeout"
    return None, None, str(exc)


def _default_error_code(exc: Exception, status_code: int | None) -> str:
    if isinstance(exc, MaxApiAuthError):
        return "auth_error"
    if isinstance(exc, MaxApiRateLimitError):
        return "rate_limit"
    if isinstance(exc, MaxApiNetworkError):
        return "network_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if status_code is not None:
        return f"http_{status_code}"
    return type(exc).__name__


def _extract_message_id(response: Any) -> str | None:
    if isinstance(response, MaxMessage):
        return response.message_id
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            return _extract_message_id(message)
        body = response.get("body")
        if isinstance(body, dict):
            body_id = body.get("mid") or body.get("message_id") or body.get("id")
            if body_id is not None:
                return str(body_id)
        for key in ("message_id", "mid", "id"):
            value = response.get(key)
            if value is not None:
                return str(value)
    return None


def _safe_raw_response(response: Any) -> dict[str, Any] | None:
    if isinstance(response, MaxMessage):
        return {
            "message_id": response.message_id,
            "chat_id": response.chat_id,
            "user_id": response.user_id,
            "timestamp": response.timestamp,
        }
    if isinstance(response, dict):
        safe: dict[str, Any] = {}
        message_id = _extract_message_id(response)
        if message_id is not None:
            safe["message_id"] = message_id
        success = response.get("success")
        if isinstance(success, bool):
            safe["success"] = success
        message = response.get("message")
        if isinstance(message, str):
            safe["message"] = _safe_error_message(message)
        return safe or None
    return None


def _normalize_error_code(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    value = error_code.strip().lower()
    return value or None


def _safe_error_message(message: str | None) -> str | None:
    if message is None:
        return None
    return " ".join(message.split())[:240]


def _log_safe_failure(result: MaxSendResult) -> None:
    logger.warning(
        "max_send_failed recipient_type=%s recipient_id=%s status_code=%s attempts=%s "
        "error_code=%s retryable=%s blocked=%s stopped=%s",
        result.recipient_type,
        result.recipient_id,
        result.status_code,
        result.attempts,
        result.error_code,
        result.is_retryable,
        result.is_blocked,
        result.is_stopped,
    )
