"""Safe diagnostics helpers for generic MAX bot errors."""

from __future__ import annotations

import re
import secrets
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from max_barbershop_bot.core.events import NormalizedEvent

GENERIC_ERROR_TEXT = "😔 Что-то пошло не так. Попробуйте ещё раз."
MASK = "***"
MAX_ALERT_LENGTH = 3500

_SECRET_KEY_PARTS = (
    "token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "cookie",
    "session",
)
_CONTACT_KEYS = {"vcf_info", "vcard", "contact", "phone", "phone_number", "phones"}
_LONG_SECRET_RE = re.compile(r"(?<![\w-])([A-Za-z0-9_\-]{32,})(?![\w-])")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_AUTH_RE = re.compile(r"(?i)(Authorization\s*[:=]\s*)([^\n\r;]+)")
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(MAX_BOT_TOKEN|partner_token|user_token|bot_token|api_key|secret|password)\s*[:=]\s*([^\s,;]+)"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8)\s*\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
_VCARD_RE = re.compile(r"(?is)BEGIN:VCARD.*?END:VCARD")


def generate_error_id() -> str:
    """Return a compact id shared by logs and developer diagnostics."""

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def mask_secret(value: str) -> str:
    """Mask a secret while keeping only tiny debugging hints."""

    text = str(value)
    if not text:
        return MASK
    if len(text) <= 4:
        return MASK
    return f"{text[:2]}…{text[-2:]}"


def sanitize_text(value: str) -> str:
    """Remove obvious secrets and personal contact data from text diagnostics."""

    text = str(value)
    text = _VCARD_RE.sub("<contact_vcard_hidden>", text)
    text = _AUTH_RE.sub(lambda match: f"{match.group(1)}{MASK}", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _NAMED_SECRET_RE.sub(lambda match: f"{match.group(1)}={MASK}", text)
    text = _PHONE_RE.sub("<phone_hidden>", text)
    text = _LONG_SECRET_RE.sub(MASK, text)
    return text


def sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a shallow/deep sanitized copy of a mapping."""

    return {str(key): _sanitize_value(str(key), child) for key, child in value.items()}


def build_safe_error_context(
    *,
    error_id: str,
    exception: BaseException,
    event: NormalizedEvent | None = None,
    handler_name: str | None = None,
    location: str | None = None,
    screen_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact context that is safe to send to the developer."""

    context: dict[str, Any] = {
        "error_id": error_id,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "exception_class": type(exception).__name__,
        "exception_message": _short(sanitize_text(str(exception) or "Unknown error"), 240),
        "location": _short(sanitize_text(location or "—"), 160),
        "handler": _short(sanitize_text(handler_name or "—"), 160),
        "screen_id": _short(sanitize_text(screen_id or "—"), 160),
    }
    if event is not None:
        context.update(
            {
                "update_type": _short(sanitize_text(event.update_type), 120),
                "callback_payload": _short(sanitize_text(event.callback_payload or "—"), 180),
                "platform_user_id": event.platform_user_id or "—",
                "max_user_id": event.max_user_id or "—",
                "chat_id": event.chat_id or "—",
                "message_text_present": event.text is not None,
                "attachments_count": len(event.attachments),
            }
        )
    if extra:
        context["extra"] = sanitize_mapping(extra)
    context["traceback_tail"] = traceback_tail(exception)
    return context


def traceback_tail(exception: BaseException, *, lines: int = 5, limit: int = 1200) -> str:
    """Return a sanitized tail of the traceback for compact alerts."""

    tail = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)[-lines:]).strip()
    if len(tail) > limit:
        tail = tail[-limit:]
    return sanitize_text(tail) or "—"


def render_developer_alert(context: Mapping[str, Any], *, user_message: str = GENERIC_ERROR_TEXT) -> str:
    """Render the developer alert in the style of the reference bot."""

    text = (
        "🚨 Bot error\n"
        f"error_id: {context.get('error_id', '—')}\n"
        f"User saw: {user_message}\n"
        "note: User saw generic error\n"
        f"timestamp_utc: {context.get('timestamp_utc', '—')}\n"
        f"update_type: {context.get('update_type', '—')}\n"
        f"platform_user_id: {context.get('platform_user_id', '—')}\n"
        f"max_user_id: {context.get('max_user_id', '—')}\n"
        f"chat_id: {context.get('chat_id', '—')}\n"
        f"handler/location: {context.get('handler', '—')}\n"
        f"where: {context.get('location', '—')}\n"
        f"callback_payload: {context.get('callback_payload', '—')}\n"
        f"screen_id/current state: {context.get('screen_id', '—')}\n"
        f"message_text_present: {context.get('message_text_present', '—')}\n"
        f"attachments_count: {context.get('attachments_count', '—')}\n"
        f"exception: {context.get('exception_class', '—')}: {context.get('exception_message', '—')}\n"
        "traceback_last_5_lines:\n"
        f"{context.get('traceback_tail', '—')}"
    )
    return sanitize_text(text)[:MAX_ALERT_LENGTH]


def alert_fingerprint(exception: BaseException, *, location: str | None = None) -> str:
    """Return a stable in-memory throttling key for repeated diagnostics."""

    top = "unknown"
    extracted = traceback.extract_tb(exception.__traceback__)
    if extracted:
        frame = extracted[-1]
        top = f"{frame.filename}:{frame.name}:{frame.lineno}"
    return f"{type(exception).__name__}|{location or 'unknown'}|{top}"


def _sanitize_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SECRET_KEY_PARTS):
        return MASK if value else value
    if lowered in _CONTACT_KEYS:
        return "<contact_hidden>" if value else value
    if isinstance(value, Mapping):
        return sanitize_mapping(value)
    if isinstance(value, list):
        return [_sanitize_value(key, item) for item in value[:20]]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(key, item) for item in value[:20])
    if isinstance(value, str):
        return _short(sanitize_text(value), 300)
    return value


def _short(value: Any, limit: int) -> str:
    text = str(value or "—")
    return text if len(text) <= limit else text[:limit] + "…"
