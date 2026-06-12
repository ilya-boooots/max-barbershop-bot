"""Registration business rules for MAX bot users."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from max_barbershop_bot.repositories.users import User, UsersRepository, UserProfileUpdate

_NON_DIGIT_PHONE_CHARS = re.compile(r"[\s()\-]")
_MIN_PHONE_DIGITS = 10
_MAX_PHONE_DIGITS = 15
_MIN_NAME_LENGTH = 2
_MAX_NAME_LENGTH = 60
_MIN_BIRTHDATE = date(1900, 1, 1)
_VCARD_PHONE_RE = re.compile(r"(?:^|\r?\n)TEL[^:]*:([^\r\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class BirthdateValidationResult:
    """Result of a DD.MM.YYYY birthdate validation."""

    is_valid: bool
    birthdate: str | None = None


def normalize_phone(raw_phone: str | None) -> str | None:
    """Normalize a user-entered phone number to a simple international format."""

    if raw_phone is None:
        return None

    value = _NON_DIGIT_PHONE_CHARS.sub("", raw_phone.strip())
    if not value:
        return None

    if value.startswith("+"):
        digits = value[1:]
        if not digits.isdigit() or not _valid_digit_count(digits):
            return None
        return f"+{digits}"

    if not value.isdigit() or not _valid_digit_count(value):
        return None

    if len(value) == 11 and value.startswith("8"):
        return f"+7{value[1:]}"
    if len(value) == 11 and value.startswith("7"):
        return f"+{value}"
    return f"+{value}"


def validate_name(raw_name: str | None) -> str | None:
    """Return a clean name when it is suitable for a barbershop profile."""

    if raw_name is None:
        return None

    name = " ".join(raw_name.strip().split())
    if not (_MIN_NAME_LENGTH <= len(name) <= _MAX_NAME_LENGTH):
        return None
    if name.isdigit():
        return None
    return name


def validate_birthdate(raw_birthdate: str | None) -> BirthdateValidationResult:
    """Validate Telegram-style birthdate input and return ISO date when accepted."""

    if raw_birthdate is None:
        return BirthdateValidationResult(is_valid=False)
    try:
        parsed = datetime.strptime(raw_birthdate.strip(), "%d.%m.%Y").date()
    except ValueError:
        return BirthdateValidationResult(is_valid=False)
    if parsed < _MIN_BIRTHDATE or parsed > date.today():
        return BirthdateValidationResult(is_valid=False)
    return BirthdateValidationResult(is_valid=True, birthdate=parsed.isoformat())


def is_registered(user: User | None) -> bool:
    """Check whether the profile has enough data to use the bot."""

    return bool(user and user.first_name and user.phone and user.birthdate)


def save_registration_profile(
    repository: UsersRepository,
    *,
    platform_user_id: str,
    phone: str,
    first_name: str,
    birthdate: str,
) -> User:
    """Persist the collected registration data into the existing user row."""

    user = repository.update_profile(
        platform_user_id,
        UserProfileUpdate(first_name=first_name, display_name=first_name, phone=phone, birthdate=birthdate),
    )
    if user is None:
        raise RuntimeError("Пользователь для завершения регистрации не найден")
    return user


def contains_contact_attachment(attachments: list[Any]) -> bool:
    """Return True when attachments include a supported MAX contact payload."""

    return any(_contact_payload(attachment) is not None for attachment in attachments)


def extract_contact_phone(attachments: list[Any]) -> str | None:
    """Best-effort extraction of a contact phone from already normalized attachments."""

    for attachment in attachments:
        payload = _contact_payload(attachment)
        if payload is None:
            continue
        phone = _contact_phone_from_payload(payload)
        normalized_phone = normalize_phone(phone)
        if normalized_phone is not None:
            return normalized_phone
    return None


def _contact_payload(attachment: Any) -> dict[str, Any] | None:
    if not isinstance(attachment, dict):
        return None

    payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else attachment
    attachment_type = attachment.get("type")
    if attachment_type == "contact" or _looks_like_contact(payload):
        return payload
    return None


def _contact_phone_from_payload(payload: dict[str, Any]) -> str | None:
    phone = payload.get("phone") or payload.get("phone_number")
    if isinstance(phone, str):
        return phone

    vcf_info = payload.get("vcf_info")
    if not isinstance(vcf_info, str):
        return None

    match = _VCARD_PHONE_RE.search(vcf_info.replace("\\r\\n", "\n").replace("\\n", "\n"))
    return match.group(1) if match is not None else None


def mask_phone(phone: str) -> str:
    """Mask a phone number for safe logs."""

    if len(phone) <= 8:
        return "***"
    return f"{phone[:5]}***{phone[-4:]}"


def _valid_digit_count(digits: str) -> bool:
    return _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS


def _looks_like_contact(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("phone"), str) or isinstance(payload.get("phone_number"), str)
