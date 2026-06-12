"""MAX callback payload safety helpers."""

from __future__ import annotations

import re

# MAX docs currently document button types and link URL length, but do not
# publish an exact callback payload size/charset limit. Until a MAX-specific
# limit appears in https://dev.max.ru/docs-api, the project keeps callback
# payloads within a conservative ASCII envelope to avoid Telegram-era dynamic
# IDs, raw URLs, JSON, names, or user text leaking into callback routing.
MAX_CALLBACK_PAYLOAD_BYTES = 64
CALLBACK_PAYLOAD_PATTERN = re.compile(r"^[a-zA-Z0-9:_\-.]+$")


class CallbackPayloadError(ValueError):
    """Raised when a callback payload is unsafe for MAX inline buttons."""


def payload_size_bytes(payload: str) -> int:
    """Return UTF-8 byte size for a callback payload."""

    return len(payload.encode("utf-8"))


def validate_callback_payload(payload: str) -> str:
    """Validate a MAX callback payload and return it unchanged.

    The validator intentionally fails loudly instead of truncating payloads:
    truncation can route callbacks to a different handler or entity.
    """

    if not isinstance(payload, str):
        raise CallbackPayloadError("MAX callback payload must be a string")
    if not payload:
        raise CallbackPayloadError("MAX callback payload must not be empty")

    payload_size = payload_size_bytes(payload)
    if payload_size > MAX_CALLBACK_PAYLOAD_BYTES:
        raise CallbackPayloadError(
            "MAX callback payload is too long: "
            f"{payload_size} bytes > {MAX_CALLBACK_PAYLOAD_BYTES} bytes ({payload!r})"
        )
    if CALLBACK_PAYLOAD_PATTERN.fullmatch(payload) is None:
        raise CallbackPayloadError(
            "MAX callback payload contains unsafe characters; use only "
            f"letters, digits, ':', '_', '-', '.' ({payload!r})"
        )
    return payload


def make_callback_payload(*parts: str) -> str:
    """Build and validate a colon-separated MAX callback payload."""

    payload = ":".join(str(part) for part in parts)
    return validate_callback_payload(payload)


def indexed_payload(prefix: str, index: int) -> str:
    """Build a validated payload for a screen-local indexed entity."""

    if not prefix:
        raise CallbackPayloadError("MAX callback payload prefix must not be empty")
    separator = "" if prefix.endswith(":") else ":"
    return validate_callback_payload(f"{prefix}{separator}{index}")
