"""Central branch-local company time helpers for MAX flows."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_BRANCH_TIMEZONE = "Europe/Moscow"

logger = logging.getLogger(__name__)


class CompanyTimeService:
    """Resolve branch timezone from active YClients settings and format local dates."""

    def __init__(self, yclients_settings_repo: Any | None = None) -> None:
        self._yclients_settings_repo = yclients_settings_repo

    def get_branch_timezone_name(self) -> str:
        """Return active branch timezone name or the central safe fallback."""

        settings = None
        if self._yclients_settings_repo is not None:
            try:
                settings = self._yclients_settings_repo.get_active()
            except Exception as exc:  # noqa: BLE001 - timezone fallback must not crash user flows.
                logger.warning(
                    "MAX company time diagnostic: operation=get_branch_timezone branch_timezone=%s "
                    "fallback_used=True invalid_timezone=%s",
                    DEFAULT_BRANCH_TIMEZONE,
                    type(exc).__name__,
                )
                return DEFAULT_BRANCH_TIMEZONE
        return normalize_branch_timezone(getattr(settings, "branch_timezone", None), flow="company_time", operation="get_branch_timezone")

    def get_branch_zoneinfo(self) -> ZoneInfo:
        """Return branch ZoneInfo, falling back centrally when settings are missing/invalid."""

        return zoneinfo_or_default(self.get_branch_timezone_name(), flow="company_time", operation="get_branch_zoneinfo")

    def now(self) -> datetime:
        """Return current company-local datetime in the branch timezone."""

        return datetime.now(self.get_branch_zoneinfo())

    def today(self) -> date:
        """Return current company-local date in the branch timezone."""

        return self.now().date()

    def localize_datetime(self, value: Any) -> datetime | None:
        """Interpret naive datetimes as branch-local and convert aware datetimes to branch-local."""

        return localize_datetime(value, self.get_branch_timezone_name())

    def format_date(self, value: date | datetime | str | None) -> str:
        """Format a date as DD.MM.YYYY in branch timezone."""

        localized = self.localize_datetime(value) if not isinstance(value, date) or isinstance(value, datetime) else value
        if localized is None:
            return "—"
        return localized.strftime("%d.%m.%Y")

    def format_time(self, value: datetime | str | None) -> str:
        """Format a time as HH:MM in branch timezone."""

        localized = self.localize_datetime(value)
        if localized is None:
            return "—"
        return localized.strftime("%H:%M")

    def format_datetime(self, value: datetime | str | None) -> str:
        """Format a datetime as DD.MM.YYYY HH:MM in branch timezone."""

        localized = self.localize_datetime(value)
        if localized is None:
            return "—"
        return localized.strftime("%d.%m.%Y %H:%M")


def normalize_branch_timezone(timezone_name: str | None, *, flow: str = "company_time", operation: str = "normalize_timezone") -> str:
    """Validate a timezone name and return the central fallback when invalid."""

    raw = str(timezone_name).strip() if timezone_name is not None else ""
    candidate = raw or DEFAULT_BRANCH_TIMEZONE
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        logger.warning(
            "MAX company time diagnostic: flow=%s operation=%s branch_timezone=%s fallback_used=True invalid_timezone=%s",
            flow,
            operation,
            DEFAULT_BRANCH_TIMEZONE,
            candidate,
        )
        return DEFAULT_BRANCH_TIMEZONE
    return candidate


def zoneinfo_or_default(timezone_name: str | None, *, flow: str = "company_time", operation: str = "zoneinfo") -> ZoneInfo:
    """Return ZoneInfo for a branch timezone with central fallback."""

    return ZoneInfo(normalize_branch_timezone(timezone_name, flow=flow, operation=operation))


def localize_datetime(value: Any, timezone_name: str | None) -> datetime | None:
    """Parse/convert a YClients datetime using branch timezone for naive values."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw or len(raw) <= 5:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    tz = zoneinfo_or_default(timezone_name, operation="localize_datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def format_branch_action_timestamp(
    timezone_name: str | None,
    *,
    action_type: str,
    action_at: datetime | None = None,
) -> str:
    """Format the current MAX action time for YClients comments in branch timezone."""

    raw_timezone = str(timezone_name).strip() if timezone_name is not None else ""
    branch_timezone_present = bool(raw_timezone)
    branch_timezone_value = normalize_branch_timezone(
        raw_timezone or DEFAULT_BRANCH_TIMEZONE,
        flow="yclients_comment",
        operation="format_branch_action_timestamp",
    )
    fallback_used = (not branch_timezone_present) or branch_timezone_value != raw_timezone
    try:
        branch_zone = ZoneInfo(branch_timezone_value)
        action_time = action_at or datetime.now(branch_zone)
        if action_time.tzinfo is None:
            action_time = action_time.replace(tzinfo=branch_zone)
        else:
            action_time = action_time.astimezone(branch_zone)
        formatted = action_time.strftime("%d.%m.%Y в %H:%M")
    except Exception as exc:  # noqa: BLE001 - YClients comments must not block user actions.
        fallback_used = True
        branch_timezone_value = DEFAULT_BRANCH_TIMEZONE
        branch_zone = ZoneInfo(DEFAULT_BRANCH_TIMEZONE)
        action_time = action_at or datetime.now(branch_zone)
        if action_time.tzinfo is None:
            action_time = action_time.replace(tzinfo=branch_zone)
        else:
            action_time = action_time.astimezone(branch_zone)
        formatted = action_time.strftime("%d.%m.%Y в %H:%M")
        logger.warning(
            "MAX YClients comment timestamp diagnostic: action_type=%s branch_timezone_present=%s "
            "branch_timezone_value=%s formatted_timestamp_present=%s fallback_used=%s error_class=%s",
            action_type,
            branch_timezone_present,
            branch_timezone_value,
            bool(formatted),
            fallback_used,
            type(exc).__name__,
        )
        return formatted

    if fallback_used:
        logger.warning(
            "MAX YClients comment timestamp diagnostic: action_type=%s branch_timezone_present=%s "
            "branch_timezone_value=%s formatted_timestamp_present=%s fallback_used=%s",
            action_type,
            branch_timezone_present,
            branch_timezone_value,
            bool(formatted),
            fallback_used,
        )
    return formatted


def build_yclients_action_comment(
    marker: str,
    *,
    timezone_name: str | None,
    action_type: str,
    action_at: datetime | None = None,
) -> str:
    """Build a one-line YClients MAX action comment with branch-local action time."""

    clean_marker = str(marker or "").strip()
    timestamp = format_branch_action_timestamp(timezone_name, action_type=action_type, action_at=action_at)
    return f"{clean_marker} {timestamp}".strip()
