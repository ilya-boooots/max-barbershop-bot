from __future__ import annotations

import hashlib
import json
import logging
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from app.core.permissions import DEVELOPER_TG_ID
from app.core.safe_telegram import safe_send
from app.repositories.diagnostics import (
    clear_error_events,
    list_recent_error_events,
    upsert_error_event,
)

logger = logging.getLogger(__name__)

GLOBAL_ALERTS_PER_MINUTE = 3
GLOBAL_WINDOW_SECONDS = 60
FINGERPRINT_COOLDOWN_SECONDS = 600
BURST_WINDOW_SECONDS = 600
BURST_ALERT_THRESHOLD = 3

_NON_CRITICAL_SUBSTRINGS = (
    "message is not modified",
    "query is too old",
    "message to edit not found",
    "message can't be deleted",
)
_NON_CRITICAL_EXC_TYPES = {"TelegramBadRequest"}
_CRITICAL_WHERE_KEYWORDS = ("booking", "admin", "staff", "yclients", "payment")


@dataclass
class ErrorEntry:
    fingerprint: str
    error_type: str
    where: str
    count: int
    first_seen: str
    last_seen: str
    last_context_json: str | None


_events_memory: dict[str, ErrorEntry] = {}
_alert_timestamps: deque[float] = deque()
_last_sent_per_fingerprint: dict[str, float] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {}
    blocked = {"token", "bot_token", "authorization", "password", "secret", "api_key", "cookie"}
    safe: dict[str, Any] = {}
    for key, value in context.items():
        lowered = key.lower()
        if any(word in lowered for word in blocked):
            safe[key] = "***"
            continue
        as_text = str(value)
        if len(as_text) > 300:
            safe[key] = as_text[:300] + "…"
        else:
            safe[key] = value
    return safe


def _top_frame(exception: BaseException) -> str:
    tb = traceback.extract_tb(exception.__traceback__)
    if not tb:
        return "unknown"
    top = tb[-1]
    return f"{top.filename}:{top.name}:{top.lineno}"


def build_fingerprint(exception: BaseException, where: str, handler_name: str | None = None) -> str:
    payload = f"{type(exception).__name__}|{_top_frame(exception)}|{handler_name or where}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def short_where(exception: BaseException) -> str:
    tb = traceback.extract_tb(exception.__traceback__)
    if not tb:
        return "unknown"
    frame = tb[-1]
    module = frame.filename.split("/")[-1]
    return f"{module}:{frame.name}"


def should_alert(exception: BaseException, where: str, count_in_window: int, startup: bool = False) -> bool:
    if startup:
        return True
    message = str(exception).lower()
    if type(exception).__name__ in _NON_CRITICAL_EXC_TYPES and any(s in message for s in _NON_CRITICAL_SUBSTRINGS):
        return False
    where_lower = where.lower()
    if any(keyword in where_lower for keyword in _CRITICAL_WHERE_KEYWORDS):
        return True
    return count_in_window >= BURST_ALERT_THRESHOLD

async def record_error_event(
    *,
    exception: BaseException,
    where: str,
    handler_name: str | None,
    context: dict[str, Any] | None,
) -> ErrorEntry:
    fingerprint = build_fingerprint(exception, where, handler_name)
    now_iso = _utc_now().isoformat()
    safe_context = _safe_context(context)
    safe_context.setdefault("action", handler_name or where)
    context_json = json.dumps(safe_context, ensure_ascii=False)

    entry = _events_memory.get(fingerprint)
    if entry is None:
        entry = ErrorEntry(
            fingerprint=fingerprint,
            error_type=type(exception).__name__,
            where=where,
            count=1,
            first_seen=now_iso,
            last_seen=now_iso,
            last_context_json=context_json,
        )
    else:
        entry.count += 1
        entry.last_seen = now_iso
        entry.last_context_json = context_json
    _events_memory[fingerprint] = entry

    try:
        await upsert_error_event(
            fingerprint=fingerprint,
            error_type=entry.error_type,
            where=entry.where,
            count=entry.count,
            first_seen=entry.first_seen,
            last_seen=entry.last_seen,
            last_context_json=entry.last_context_json,
        )
    except Exception:
        logger.exception("Failed to upsert error event")
    return entry


def _global_rate_limited() -> bool:
    now = time.monotonic()
    while _alert_timestamps and now - _alert_timestamps[0] > GLOBAL_WINDOW_SECONDS:
        _alert_timestamps.popleft()
    if len(_alert_timestamps) >= GLOBAL_ALERTS_PER_MINUTE:
        return True
    _alert_timestamps.append(now)
    return False


def _fingerprint_cooled_down(fingerprint: str) -> bool:
    now = time.monotonic()
    last = _last_sent_per_fingerprint.get(fingerprint)
    if last is not None and now - last < FINGERPRINT_COOLDOWN_SECONDS:
        return False
    _last_sent_per_fingerprint[fingerprint] = now
    return True


async def send_dev_alert(bot: Bot, text: str) -> None:
    try:
        await safe_send(bot, "send_message", chat_id=DEVELOPER_TG_ID, text=text[:3500])
    except Exception:
        logger.exception("send_dev_alert failed")


async def maybe_alert_error(
    *,
    bot: Bot,
    exception: BaseException,
    where: str,
    action: str,
    user_id: int | None,
    fingerprint: str,
    repeat_count: int,
    startup: bool = False,
) -> None:
    if _global_rate_limited():
        return
    if not _fingerprint_cooled_down(fingerprint):
        return
    if not should_alert(exception, where, repeat_count, startup=startup):
        return

    now = _utc_now().strftime("%d.%m.%Y %H:%M:%S UTC")
    msg = str(exception)
    short_message = (msg[:160] + "…") if len(msg) > 160 else msg
    text = (
        "🚨 Критическая ошибка\n"
        f"🧠 Тип: {type(exception).__name__}\n"
        f"📍 Где: {where}\n"
        f"👤 user_id: {user_id or '—'}\n"
        f"🎬 Действие: {action}\n"
        f"🕒 Время: {now}\n"
        f"💬 Кратко: {short_message or '—'}\n"
        f"🧩 Код: {fingerprint}\n"
        f"📈 Повторы за 10 мин: {repeat_count}"
    )
    await send_dev_alert(bot, text)


async def get_error_events(limit: int = 10) -> list[dict[str, Any]]:
    rows = await list_recent_error_events(limit=limit)
    if rows:
        return rows
    data = sorted(_events_memory.values(), key=lambda item: item.last_seen, reverse=True)
    return [entry.__dict__ for entry in data[:limit]]


async def clear_error_events_storage() -> None:
    _events_memory.clear()
    _last_sent_per_fingerprint.clear()
    _alert_timestamps.clear()
    await clear_error_events()
