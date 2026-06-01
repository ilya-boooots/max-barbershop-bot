from __future__ import annotations

from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo
from typing import Any

from app.repositories.yclients_settings import get_yclients_settings
from app.services.company_time import DEFAULT_TIMEZONE, resolve_company_timezone

logger = logging.getLogger(__name__)


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _ensure_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def format_datetime(value: Any) -> str:
    dt = _ensure_datetime(value)
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(_local_tz())
    return local_dt.strftime("%d.%m.%Y в %H:%M:%S")


def _safe_zoneinfo(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


async def resolve_branch_timezone() -> str:
    settings = await get_yclients_settings()
    company_id = (settings.company_id if settings else None) or ""
    if company_id:
        try:
            context = await resolve_company_timezone(company_id)
            return context.timezone_name or DEFAULT_TIMEZONE
        except Exception:
            return DEFAULT_TIMEZONE
    return DEFAULT_TIMEZONE


async def format_branch_datetime(value: Any) -> str:
    dt = _ensure_datetime(value)
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz_name = await resolve_branch_timezone()
    local_dt = dt.astimezone(_safe_zoneinfo(tz_name))
    return local_dt.strftime("%d.%m.%Y в %H:%M:%S")


def format_datetime_in_timezone(value: Any, timezone_name: str | None) -> str:
    dt = _ensure_datetime(value)
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if not timezone_name:
        logger.warning("branch_timezone_missing_for_datetime_display")
    local_dt = dt.astimezone(_safe_zoneinfo(timezone_name or DEFAULT_TIMEZONE))
    return local_dt.strftime("%d.%m.%Y в %H:%M:%S")
