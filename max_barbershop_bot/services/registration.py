"""Registration business rules for MAX bot users."""

from __future__ import annotations

import re
from typing import Any

from max_barbershop_bot.repositories.users import User, UsersRepository, UserProfileUpdate

_NON_DIGIT_PHONE_CHARS = re.compile(r"[\s()\-]")
_MIN_PHONE_DIGITS = 10
_MAX_PHONE_DIGITS = 15
_MIN_NAME_LENGTH = 2
_MAX_NAME_LENGTH = 60


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


def is_registered(user: User | None) -> bool:
    """Check whether the profile has enough data to use the bot."""

    return bool(user and user.first_name and user.phone)


def save_registration_profile(
    repository: UsersRepository,
    *,
    platform_user_id: str,
    phone: str,
    first_name: str,
) -> User:
    """Persist the collected registration data into the existing user row."""

    user = repository.update_profile(
        platform_user_id,
        UserProfileUpdate(first_name=first_name, display_name=first_name, phone=phone),
    )
    if user is None:
        raise RuntimeError("Пользователь для завершения регистрации не найден")
    return user


def extract_contact_phone(attachments: list[Any]) -> str | None:
    """Best-effort extraction of a contact phone from already normalized attachments."""

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else attachment
        attachment_type = attachment.get("type")
        if attachment_type not in {"contact", "request_contact"} and not _looks_like_contact(payload):
            continue
        phone = payload.get("phone") or payload.get("phone_number")
        normalized_phone = normalize_phone(phone if isinstance(phone, str) else None)
        if normalized_phone is not None:
            return normalized_phone
    return None


def mask_phone(phone: str) -> str:
    """Mask a phone number for safe logs."""

    if len(phone) <= 8:
        return "***"
    return f"{phone[:5]}***{phone[-4:]}"


def _valid_digit_count(digits: str) -> bool:
    return _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS


def _looks_like_contact(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("phone"), str) or isinstance(payload.get("phone_number"), str)
