"""Utility helpers for YClients payload parsing and normalization."""

from __future__ import annotations

from typing import Any

MAX_BOOKING_COMMENT_MARKER = "Клиент записался из MAX бота"


def safe_str(value: Any) -> str:
    """Return a stripped string or an empty string for missing values."""

    if value is None:
        return ""
    return str(value).strip()


def normalize_id(value: str | int) -> int | str:
    """Convert numeric string identifiers to integers for YClients payloads."""

    value_str = str(value).strip()
    return int(value_str) if value_str.isdigit() else value_str


def normalize_phone(phone: str) -> str:
    """Normalize a phone for simple YClients search/create payloads.

    This intentionally avoids a new dependency. It preserves a leading plus and
    strips spaces, brackets, dashes and other presentation characters.
    """

    phone = phone.strip()
    prefix = "+" if phone.startswith("+") else ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"{prefix}{digits}" if digits else phone


def extract_data_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Extract a list of dictionaries from common YClients response envelopes."""

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    if isinstance(payload, dict):
        return [payload]
    return []


def extract_first_record(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    """Extract a single record-like dictionary from a YClients response."""

    rows = extract_data_rows(payload)
    return rows[0] if rows else None


def append_booking_marker(comment: str | None, marker: str = MAX_BOOKING_COMMENT_MARKER) -> str:
    """Append a booking origin marker once, preserving an existing comment."""

    existing = (comment or "").strip()
    if marker in existing:
        return existing
    if existing:
        return f"{existing}\n{marker}"
    return marker


def truthy_bool(value: Any) -> bool | None:
    """Best-effort bool conversion for optional API flags."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    value_str = str(value).strip().lower()
    if value_str in {"1", "true", "yes", "y", "да"}:
        return True
    if value_str in {"0", "false", "no", "n", "нет"}:
        return False
    return None
