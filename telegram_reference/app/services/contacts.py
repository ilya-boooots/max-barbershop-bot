from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.integrations.yclients import YClientsError, build_yclients_client, get_yclients_credentials
from app.integrations.yclients.endpoints import get_company
from app.repositories.contacts_override import ContactsOverride, get_contacts_override

logger = logging.getLogger(__name__)
PLACEHOLDER = "—"


@dataclass(frozen=True)
class ContactsData:
    address: str | None
    phone: str | None
    schedule: str | None


@dataclass(frozen=True)
class ResolvedContacts:
    address: str
    phone: str
    schedule: str


@dataclass(frozen=True)
class ContactsContext:
    company_id: str
    api: ContactsData
    override: ContactsOverride | None
    resolved: ResolvedContacts


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _extract_data(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _pick_phone(company: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    direct = _clean(company.get("phone"))
    if direct:
        candidates.append(direct)

    phone_list = company.get("phones")
    if isinstance(phone_list, list):
        for item in phone_list:
            if isinstance(item, dict):
                value = _clean(item.get("number") or item.get("phone") or item.get("value"))
            else:
                value = _clean(item)
            if value:
                candidates.append(value)

    unique = list(dict.fromkeys(candidates))
    if not unique:
        return None
    return " / ".join(unique)


def _extract_contacts(payload: dict[str, Any] | list[Any]) -> ContactsData:
    company = _extract_data(payload)
    return ContactsData(
        address=_clean(company.get("address") or company.get("short_address")),
        phone=_pick_phone(company),
        schedule=_clean(company.get("schedule") or company.get("schedule_text") or company.get("work_time")),
    )


async def get_company_contacts(company_id: str) -> ContactsData:
    client, _ = await build_yclients_client()
    try:
        payload = await get_company(client, company_id=company_id)
    except YClientsError as exc:
        logger.error(
            "yclients_contacts_fetch_failed trace_id=%s endpoint=%s status=%s snippet=%s",
            exc.trace_id or "n/a",
            exc.endpoint or f"/api/v1/company/{company_id}",
            exc.status_code,
            (exc.response_snippet or "")[:250],
        )
        return ContactsData(address=None, phone=None, schedule=None)
    finally:
        await client.close()
    return _extract_contacts(payload)


async def resolve_contacts_for_company(company_id: str) -> ContactsContext:
    api_contacts = await get_company_contacts(company_id)
    override = await get_contacts_override(company_id)

    address = _clean(override.address) if override else None
    phone = _clean(override.phone) if override else None
    schedule = _clean(override.schedule) if override else None

    resolved = ResolvedContacts(
        address=address or api_contacts.address or PLACEHOLDER,
        phone=phone or api_contacts.phone or PLACEHOLDER,
        schedule=schedule or api_contacts.schedule or PLACEHOLDER,
    )
    return ContactsContext(company_id=company_id, api=api_contacts, override=override, resolved=resolved)


async def resolve_contacts() -> ContactsContext:
    credentials, _ = await get_yclients_credentials()
    return await resolve_contacts_for_company(credentials.company_id)


def render_contacts_block(resolved: ResolvedContacts) -> str:
    return (
        "📍 Контакты Барбершоп\n\n"
        f"🏠 Адрес: {resolved.address}\n"
        f"📞 Телефон: {resolved.phone}\n"
        f"⏰ Режим работы: {resolved.schedule}"
    )
