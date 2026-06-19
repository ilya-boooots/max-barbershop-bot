"""Helpers for stable user-visible names."""

from __future__ import annotations

FALLBACK_DISPLAY_NAME = "Пользователь"


def get_user_display_name(user: object | None, event_profile_name: str | None = None) -> str:
    """Return the visible profile name for menu greetings."""

    return resolve_user_display_name(user, event_profile_name)


def resolve_user_display_name(user: object | None, profile_name: str | None = None) -> str:
    """Resolve one source of truth for user-facing display names.

    Priority:
    1. persisted non-placeholder registration name;
    2. non-empty MAX profile name;
    3. generic fallback.
    """

    return get_saved_user_display_name(user) or clean_display_name(profile_name) or FALLBACK_DISPLAY_NAME


def get_saved_user_display_name(user: object | None) -> str | None:
    """Return a persisted non-placeholder user name if one exists."""

    for value in (
        getattr(user, "display_name", None),
        getattr(user, "full_name", None),
        getattr(user, "name", None),
        getattr(user, "first_name", None),
    ):
        cleaned = clean_display_name(value)
        if cleaned:
            return cleaned
    return None


def clean_display_name(value: object) -> str | None:
    """Normalize a candidate display name and reject empty placeholders."""

    cleaned = " ".join(str(value or "").split()).strip()
    if not cleaned or cleaned == FALLBACK_DISPLAY_NAME:
        return None
    return cleaned


def join_profile_name(first_name: str | None, last_name: str | None) -> str | None:
    """Build a MAX profile display name from event name parts."""

    return clean_display_name(
        " ".join(part.strip() for part in (first_name or "", last_name or "") if part and part.strip())
    )
