from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    skipped: bool = False
    error: str | None = None


async def safe_send(bot: Bot, method: str, *, chat_id: int, retries: int = 3, **kwargs: Any) -> SendResult:
    for attempt in range(1, retries + 1):
        try:
            sender: Callable[..., Awaitable[Any]] = getattr(bot, method)
            await sender(chat_id=chat_id, **kwargs)
            return SendResult(ok=True)
        except TelegramRetryAfter as exc:
            delay = max(1.0, float(exc.retry_after))
            logger.warning(
                "telegram_send_retry_after chat_id=%s method=%s attempt=%s/%s wait_s=%.2f",
                chat_id,
                method,
                attempt,
                retries,
                delay,
            )
            if attempt >= retries:
                return SendResult(ok=False, error=f"retry_after:{delay}")
            await asyncio.sleep(delay)
        except (TelegramNetworkError, TelegramServerError) as exc:
            logger.warning(
                "telegram_send_retryable_error chat_id=%s method=%s attempt=%s/%s error=%s",
                chat_id,
                method,
                attempt,
                retries,
                type(exc).__name__,
            )
            if attempt >= retries:
                return SendResult(ok=False, error=type(exc).__name__)
            await asyncio.sleep(0.6 * attempt)
        except (TelegramForbiddenError, TelegramNotFound) as exc:
            logger.info(
                "telegram_send_skipped chat_id=%s method=%s reason=%s",
                chat_id,
                method,
                type(exc).__name__,
            )
            return SendResult(ok=False, skipped=True, error=type(exc).__name__)
        except TelegramBadRequest as exc:
            text = str(exc).lower()
            if any(msg in text for msg in ("chat not found", "user is deactivated", "bot was blocked")):
                logger.info("telegram_send_skipped chat_id=%s method=%s reason=%s", chat_id, method, text[:80])
                return SendResult(ok=False, skipped=True, error=text[:120])
            logger.error("telegram_send_bad_request chat_id=%s method=%s error=%s", chat_id, method, text[:120])
            return SendResult(ok=False, error=text[:120])
        except Exception as exc:
            logger.exception("telegram_send_unexpected chat_id=%s method=%s", chat_id, method)
            if attempt >= retries:
                return SendResult(ok=False, error=type(exc).__name__)
            await asyncio.sleep(0.6 * attempt)
    return SendResult(ok=False, error="unknown")
