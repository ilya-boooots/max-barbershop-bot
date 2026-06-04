"""Transport-neutral contacts service for the MAX bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.endpoints import get_company
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.integrations.yclients.utils import safe_str
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository

logger = logging.getLogger(__name__)

CONTACTS_FALLBACK_TEXT = "Контакты пока не настроены 🙏\n\nПожалуйста, напишите администратору."

_USEFUL_OVERRIDE_FIELDS = frozenset(
    {
        "title",
        "name",
        "branch_title",
        "address",
        "phone",
        "phones",
        "schedule",
        "working_hours",
        "work_schedule",
        "website",
        "site",
        "url",
        "map_url",
        "map",
        "maps_url",
        "telegram",
        "instagram",
    }
)

_SAFE_RAW_FIELDS = _USEFUL_OVERRIDE_FIELDS | {"branch_timezone"}


@dataclass(frozen=True)
class ContactInfo:
    """Normalized contacts data independent from bot transport."""

    title: str | None = None
    address: str | None = None
    phone: str | None = None
    schedule: str | None = None
    website: str | None = None
    map_url: str | None = None
    telegram: str | None = None
    instagram: str | None = None
    source: str = "fallback"
    raw: dict[str, Any] | None = None


class ContactsService:
    """Load contacts with manual override priority and safe YClients fallback."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    async def get_contacts(self) -> ContactInfo:
        """Return contacts from manual override, YClients, or a friendly fallback marker."""

        try:
            override = self._settings_repository.get_contacts_override()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Contacts fallback after settings override error: error_class=%s",
                type(exc).__name__,
            )
            return fallback_contact_info()

        if has_useful_override(override):
            return contact_info_from_override(override)

        try:
            settings = self._settings_repository.get_active()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Contacts fallback after active settings error: error_class=%s",
                type(exc).__name__,
            )
            return fallback_contact_info()
        if settings is None:
            logger.info("Contacts fallback: no active YClients settings")
            return fallback_contact_info()

        if not settings.company_id or not settings.partner_token:
            logger.info(
                "Contacts fallback: incomplete YClients settings company_id_present=%s "
                "partner_token_present=%s user_token_present=%s",
                bool(settings.company_id),
                bool(settings.partner_token),
                bool(settings.user_token),
            )
            return fallback_contact_info()

        try:
            async with YClientsClient(
                partner_token=settings.partner_token,
                user_token=settings.user_token,
                company_id=settings.company_id,
            ) as client:
                payload = await get_company(client, company_id=settings.company_id)
        except YClientsError as exc:
            logger.warning(
                "Contacts fallback after YClients error: source=yclients error_class=%s "
                "status_code=%s partner_token_present=%s user_token_present=%s",
                type(exc).__name__,
                exc.status_code,
                exc.partner_token_present,
                exc.user_token_present,
            )
            return fallback_contact_info()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Contacts fallback after unexpected YClients error: source=yclients error_class=%s",
                type(exc).__name__,
            )
            return fallback_contact_info()

        info = contact_info_from_yclients(
            payload,
            branch_title=settings.branch_title,
            branch_timezone=settings.branch_timezone,
        )
        if _has_display_fields(info):
            return info

        logger.info("Contacts fallback: YClients company response has no displayable contacts")
        return fallback_contact_info()


def has_useful_override(override: dict[str, Any] | None) -> bool:
    """Return True when a manual override has at least one displayable field."""

    if not isinstance(override, dict):
        return False
    for field in _USEFUL_OVERRIDE_FIELDS:
        if _clean_value(override.get(field)):
            return True
    return False


def contact_info_from_override(override: dict[str, Any]) -> ContactInfo:
    """Normalize contacts from settings override JSON."""

    return ContactInfo(
        title=_first_text(override, "title", "name", "branch_title"),
        address=_first_text(override, "address"),
        phone=_first_text(override, "phone", "phones"),
        schedule=_first_text(override, "schedule", "working_hours", "work_schedule"),
        website=_first_text(override, "website", "site", "url"),
        map_url=_first_text(override, "map_url", "map", "maps_url"),
        telegram=_first_text(override, "telegram"),
        instagram=_first_text(override, "instagram"),
        source="override",
        raw=_safe_raw_subset(override),
    )


def contact_info_from_yclients(
    payload: dict[str, Any] | list[Any],
    *,
    branch_title: str | None = None,
    branch_timezone: str | None = None,
) -> ContactInfo:
    """Normalize a YClients company card response into contact info."""

    company = _extract_company_payload(payload)
    title = _clean_value(branch_title) or _first_text(company, "title", "name")
    return ContactInfo(
        title=title,
        address=_first_text(company, "address", "short_address"),
        phone=_first_text(company, "phone", "phones", "phone_number"),
        schedule=_first_text(company, "schedule", "working_hours", "work_schedule", "timetable"),
        website=_first_text(company, "website", "site", "url"),
        map_url=_first_text(company, "map_url", "maps_url", "map", "route_url"),
        telegram=_first_text(company, "telegram"),
        instagram=_first_text(company, "instagram"),
        source="yclients",
        raw=_safe_raw_subset({**company, "branch_timezone": branch_timezone}),
    )


def fallback_contact_info() -> ContactInfo:
    """Return the explicit fallback marker for formatting."""

    return ContactInfo(source="fallback")


def format_contacts_text(contact_info: ContactInfo) -> str:
    """Format contacts in the friendly Russian MAX/Telegram-reference style."""

    if contact_info.source == "fallback" or not _has_display_fields(contact_info):
        return CONTACTS_FALLBACK_TEXT

    lines = ["📍 Контакты"]
    if contact_info.title:
        lines.extend(["", f"💈 {contact_info.title}"])
    else:
        lines.append("")

    field_lines = []
    if contact_info.address:
        field_lines.append(f"🏠 Адрес: {contact_info.address}")
    if contact_info.phone:
        field_lines.append(f"☎️ Телефон: {contact_info.phone}")
    if contact_info.schedule:
        field_lines.append(f"🕒 График: {contact_info.schedule}")
    if contact_info.website:
        field_lines.append(f"🌐 Сайт: {contact_info.website}")
    if contact_info.map_url:
        field_lines.append(f"🗺 Как добраться: {contact_info.map_url}")
    if contact_info.telegram:
        field_lines.append(f"💬 Telegram: {contact_info.telegram}")
    if contact_info.instagram:
        field_lines.append(f"📸 Instagram: {contact_info.instagram}")

    lines.extend(field_lines)
    return "\n".join(lines)


def _has_display_fields(contact_info: ContactInfo) -> bool:
    return any(
        (
            contact_info.title,
            contact_info.address,
            contact_info.phone,
            contact_info.schedule,
            contact_info.website,
            contact_info.map_url,
            contact_info.telegram,
            contact_info.instagram,
        )
    )


def _extract_company_payload(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
    return {}


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _clean_value(data.get(key))
        if value:
            return value
    return None


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [_clean_value(item) for item in value]
        return ", ".join(part for part in parts if part) or None
    if isinstance(value, dict):
        for key in ("title", "name", "value", "phone", "number", "url"):
            text = _clean_value(value.get(key))
            if text:
                return text
        return None
    text = safe_str(value)
    return text or None


def _safe_raw_subset(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: data[key]
        for key in sorted(_SAFE_RAW_FIELDS)
        if key in data and _clean_value(data[key])
    }
