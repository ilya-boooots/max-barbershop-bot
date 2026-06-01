from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import phonenumbers
    from phonenumbers.phonenumberutil import NumberParseException
except Exception:  # pragma: no cover - graceful fallback when dependency unavailable
    phonenumbers = None
    NumberParseException = Exception


@dataclass(frozen=True)
class NormalizedPhoneBundle:
    raw_input: str
    digits_only: str
    canonical_e164: str | None
    national_significant: str | None
    ru_11_with_7: str | None
    ru_11_with_8: str | None
    possible_match_keys: tuple[str, ...]
    is_valid: bool
    parse_strategy: str


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _build_keys(*, canonical_e164: str | None, ru_11_with_7: str | None, ru_11_with_8: str | None, national: str | None) -> tuple[str, ...]:
    keys: list[str] = []
    for candidate in (canonical_e164, ru_11_with_7, ru_11_with_8, national):
        if not candidate:
            continue
        if candidate not in keys:
            keys.append(candidate)
    return tuple(keys)


def _fallback_ru_normalize(raw_phone: str) -> NormalizedPhoneBundle:
    digits = _only_digits(raw_phone)
    ru_7 = None
    ru_8 = None
    national = None
    e164 = None
    valid = False

    if len(digits) == 11 and digits.startswith("8"):
        ru_8 = digits
        ru_7 = f"7{digits[1:]}"
    elif len(digits) == 11 and digits.startswith("7"):
        ru_7 = digits
        ru_8 = f"8{digits[1:]}"
    elif len(digits) == 10:
        national = digits
        ru_7 = f"7{digits}"
        ru_8 = f"8{digits}"

    if ru_7:
        national = ru_7[1:]
        e164 = f"+{ru_7}"
        valid = True

    keys = _build_keys(canonical_e164=e164, ru_11_with_7=ru_7, ru_11_with_8=ru_8, national=national)
    return NormalizedPhoneBundle(
        raw_input=raw_phone,
        digits_only=digits,
        canonical_e164=e164,
        national_significant=national,
        ru_11_with_7=ru_7,
        ru_11_with_8=ru_8,
        possible_match_keys=keys,
        is_valid=valid,
        parse_strategy="fallback_ru",
    )


def normalize_phone(input_phone: str, default_region: str = "RU") -> NormalizedPhoneBundle:
    raw_phone = str(input_phone or "")
    digits = _only_digits(raw_phone)

    if phonenumbers is None:
        return _fallback_ru_normalize(raw_phone)

    try:
        parsed = phonenumbers.parse(raw_phone, default_region)
    except NumberParseException:
        return _fallback_ru_normalize(raw_phone)

    is_valid = phonenumbers.is_valid_number(parsed)
    possible = phonenumbers.is_possible_number(parsed)
    region = phonenumbers.region_code_for_number(parsed)

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    national = str(parsed.national_number) if parsed.national_number else None

    ru_7 = None
    ru_8 = None
    if region == "RU" and national and len(national) == 10:
        ru_7 = f"7{national}"
        ru_8 = f"8{national}"

    keys = _build_keys(canonical_e164=e164, ru_11_with_7=ru_7, ru_11_with_8=ru_8, national=national)

    return NormalizedPhoneBundle(
        raw_input=raw_phone,
        digits_only=digits,
        canonical_e164=e164,
        national_significant=national,
        ru_11_with_7=ru_7,
        ru_11_with_8=ru_8,
        possible_match_keys=keys,
        is_valid=is_valid and possible,
        parse_strategy="phonenumbers",
    )


def build_phone_match_keys(bundle: NormalizedPhoneBundle) -> set[str]:
    return {key for key in bundle.possible_match_keys if key}
