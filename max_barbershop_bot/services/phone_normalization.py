"""Phone normalization helpers for safe cross-platform matching."""

from __future__ import annotations


def normalize_phone_for_match(value: str | None) -> str | None:
    """Return a stable phone match key; Russian 8/7/+7 variants collapse to 79..."""

    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits
    return digits


def build_phone_match_keys(value: str | None) -> set[str]:
    key = normalize_phone_for_match(value)
    if not key:
        return set()
    keys = {key}
    if len(key) == 11 and key.startswith("7"):
        keys.add("8" + key[1:])
        keys.add(key[1:])
    return keys


def mask_phone(value: str | None) -> str:
    key = normalize_phone_for_match(value)
    if not key:
        return "n/a"
    return f"***{key[-4:]}"
