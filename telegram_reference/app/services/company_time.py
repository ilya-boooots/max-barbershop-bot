from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import get_company
from app.repositories.company_runtime_settings import get_company_runtime_settings, upsert_company_runtime_settings

DEFAULT_TIMEZONE = "Europe/Samara"
CITY_TIMEZONE_MAP = {
    "самара": "Europe/Samara",
    "саратов": "Europe/Saratov",
    "москва": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "екатеринбург": "Asia/Yekaterinburg",
    "новосибирск": "Asia/Novosibirsk",
    "казань": "Europe/Moscow",
}


@dataclass(frozen=True)
class CompanyTimezoneContext:
    timezone_name: str
    city: str | None
    source: str


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _extract_company_data(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _valid_timezone(name: str | None) -> str | None:
    raw = _s(name)
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except Exception:
        return None
    return raw


def _derive_timezone_from_city(city: str | None) -> str | None:
    raw = _s(city).lower()
    if not raw:
        return None
    for key, tz_name in CITY_TIMEZONE_MAP.items():
        if key in raw:
            return tz_name
    return None


async def resolve_company_timezone(company_id: str) -> CompanyTimezoneContext:
    saved = await get_company_runtime_settings(company_id)
    if saved and _valid_timezone(saved.timezone):
        return CompanyTimezoneContext(timezone_name=saved.timezone or DEFAULT_TIMEZONE, city=saved.city, source=saved.source or "cache")

    city: str | None = saved.city if saved else None
    tz_name: str | None = _valid_timezone(saved.timezone) if saved else None
    source = "manual"

    client, _ = await build_yclients_client()
    try:
        payload = await get_company(client, company_id=company_id)
        company = _extract_company_data(payload)
        city = _s(company.get("city") or company.get("city_name") or company.get("cityTitle") or city) or city
        tz_name = _valid_timezone(company.get("timezone") or company.get("time_zone") or company.get("tz") or company.get("timezone_name"))
        if tz_name:
            source = "yclients"
    except YClientsError:
        pass
    finally:
        await client.close()

    if not tz_name:
        tz_name = _derive_timezone_from_city(city)
        if tz_name:
            source = "derived_from_city"

    if not tz_name:
        tz_name = DEFAULT_TIMEZONE
        source = "manual"

    await upsert_company_runtime_settings(company_id=company_id, city=city, timezone_name=tz_name, source=source)
    return CompanyTimezoneContext(timezone_name=tz_name, city=city, source=source)


def parse_yclients_datetime(value: Any) -> datetime | None:
    raw = _s(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_dt_for_timezone(dt: datetime | None, timezone_name: str) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m.%Y %H:%M")


async def format_dt_for_company(company_id: str, dt: datetime | None) -> str:
    tz_context = await resolve_company_timezone(company_id)
    return format_dt_for_timezone(dt, tz_context.timezone_name)
