"""Phone normalization helpers for safe cross-platform matching."""

from __future__ import annotations


def _digits(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    return digits or None


def normalize_phone_for_match(value: str | None) -> str | None:
    """Return primary RU phone key where +7/8/10-digit forms collapse to 79..."""

    digits = _digits(value)
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[-10:]
    if len(digits) == 11 and digits.startswith("7"):
        return digits
    if len(digits) == 10:
        return "7" + digits
    return digits


def build_phone_match_keys(value: str | None) -> set[str]:
    """Build comparable phone keys: primary digits, ru7 and last10 when safe."""

    digits = _digits(value)
    if not digits:
        return set()
    primary = normalize_phone_for_match(value)
    keys = {digits}
    if primary:
        keys.add(primary)
        if len(primary) == 11 and primary.startswith("7"):
            keys.add(primary[-10:])
    if len(digits) == 11 and digits.startswith("8"):
        keys.add("7" + digits[-10:])
        keys.add(digits[-10:])
    elif len(digits) == 11 and digits.startswith("7"):
        keys.add(digits[-10:])
    elif len(digits) == 10:
        keys.add("7" + digits)
        keys.add(digits)
    return {key for key in keys if key and (len(key) != 10 or len(key) == 10)}


def mask_phone(value: str | None) -> str:
    key = normalize_phone_for_match(value)
    if not key:
        return "n/a"
    return f"***{key[-4:]}"
