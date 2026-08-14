"""Transport-neutral contacts service for the MAX bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote
from typing import Any

from max_barbershop_bot.integrations.yclients.endpoints import get_company
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.integrations.yclients.utils import safe_str
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

PLACEHOLDER = "—"

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
        "google_maps_enabled",
        "google_maps_url",
        "twogis_enabled",
        "twogis_url",
        "yandex_maps_enabled",
        "yandex_maps_url",
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

        yclients_info = await self._get_yclients_contacts()
        if has_useful_override(override):
            return merge_contact_info(
                override=contact_info_from_override(override),
                fallback=yclients_info,
            )
        return yclients_info

    async def _get_yclients_contacts(self) -> ContactInfo:
        """Return contacts from YClients or a friendly fallback marker."""

        try:
            settings = load_active_yclients_settings(self._settings_repository, operation="get_contacts")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Contacts fallback after active settings error: error_class=%s",
                type(exc).__name__,
            )
            return fallback_contact_info()
        if settings is None:
            logger.info("Contacts fallback: no active YClients settings")
            return fallback_contact_info()

        if not has_required_yclients_credentials(settings):
            logger.info(
                "Contacts fallback: incomplete YClients settings company_id_present=%s "
                "partner_token_present=%s user_token_present=%s",
                bool(settings.company_id),
                bool(settings.partner_token),
                bool(settings.user_token),
            )
            return fallback_contact_info()

        try:
            async with build_yclients_client_from_active_settings(settings) as client:
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


def merge_contact_info(*, override: ContactInfo, fallback: ContactInfo) -> ContactInfo:
    """Merge manual override fields with YClients/fallback values field by field."""

    raw: dict[str, Any] = {}
    if fallback.raw:
        raw.update(fallback.raw)
    if override.raw:
        raw.update(override.raw)
    return ContactInfo(
        title=override.title or fallback.title,
        address=override.address or fallback.address,
        phone=override.phone or fallback.phone,
        schedule=override.schedule or fallback.schedule,
        website=override.website or fallback.website,
        map_url=override.map_url or fallback.map_url,
        telegram=override.telegram or fallback.telegram,
        instagram=override.instagram or fallback.instagram,
        source="override",
        raw=raw or None,
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
        schedule=_first_text(company, "schedule", "schedule_text", "work_time", "working_hours", "work_schedule"),
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
    """Format contacts exactly like the Telegram contacts screen where possible."""

    return (
        "📍 Контакты салона\n\n"
        f"🏠 Адрес: {_display_value(contact_info.address)}\n"
        f"📞 Телефон: {_display_value(contact_info.phone)}\n"
        f"⏰ Режим работы: {_display_value(contact_info.schedule)}"
    )


def build_route_links(contact_info: ContactInfo) -> dict[str, str]:
    """Build route links from manual map settings or generated address links."""

    generated_links = _generated_route_links(contact_info.address)
    raw = contact_info.raw or {}
    has_map_override = any(
        key in raw
        for key in (
            "yandex_maps_url",
            "yandex_maps_enabled",
            "twogis_url",
            "twogis_enabled",
            "google_maps_url",
            "google_maps_enabled",
        )
    )
    if not has_map_override:
        return generated_links

    links: dict[str, str] = {}
    for label, url_key, enabled_key in (
        ("Яндекс Карты", "yandex_maps_url", "yandex_maps_enabled"),
        ("2GIS", "twogis_url", "twogis_enabled"),
        ("Google Maps", "google_maps_url", "google_maps_enabled"),
    ):
        if not _bool_value(raw.get(enabled_key), default=True):
            continue
        url = _clean_value(raw.get(url_key)) or generated_links.get(label)
        if url:
            links[label] = url
    return links


def _generated_route_links(address_value: str | None) -> dict[str, str]:
    address = _clean_value(address_value)
    if not address or address == PLACEHOLDER:
        return {}

    encoded_address = quote(address)
    return {
        "Яндекс Карты": f"https://yandex.ru/maps/?rtext=~{encoded_address}&rtt=auto",
        "2GIS": f"https://2gis.ru/search/{encoded_address}",
        "Google Maps": (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination={encoded_address}&travelmode=driving"
        ),
    }


def _has_display_fields(contact_info: ContactInfo) -> bool:
    return any((contact_info.address, contact_info.phone, contact_info.schedule))


def _display_value(value: str | None) -> str:
    return _clean_value(value) or PLACEHOLDER


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


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
