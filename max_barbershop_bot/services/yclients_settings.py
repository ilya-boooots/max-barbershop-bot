"""Validation and safe helpers for the MAX YClients settings flow."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.dto import YClientsHealthCheckResult
from max_barbershop_bot.integrations.yclients.exceptions import (
    YCLIENTS_ERROR_CREDENTIALS,
    YCLIENTS_ERROR_TRANSPORT,
    YClientsError,
)
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE

logger = logging.getLogger(__name__)


def mask_secret(value: str | None) -> str:
    """Mask secrets for diagnostics without exposing full token values."""

    if not value:
        return "—"
    clean = str(value).strip()
    if len(clean) <= 6:
        return "*" * len(clean)
    return f"{clean[:3]}***{clean[-2:]}"


def normalize_required_text(value: str | None) -> str | None:
    """Trim a required text field and return None when it is empty."""

    clean = (value or "").strip()
    return clean or None


def normalize_optional_text(value: str | None) -> str | None:
    """Trim an optional text field and return None when it is empty."""

    return normalize_required_text(value)


def normalize_support_timezone(value: str | None) -> str:
    """Validate an IANA timezone name and return a safe default for empty input."""

    clean = (value or "").strip() or DEFAULT_BRANCH_TIMEZONE
    try:
        ZoneInfo(clean)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("invalid timezone") from exc
    return clean


def is_configured(settings: YClientsSettings | None) -> bool:
    """Return True when active settings contain all credentials needed for booking flows."""

    return has_required_yclients_credentials(settings)


async def check_yclients_connection(settings: YClientsSettings) -> YClientsHealthCheckResult:
    """Run a read-only YClients health check using saved settings."""

    if not has_required_yclients_credentials(settings):
        return YClientsHealthCheckResult(
            ok=False,
            status_code=None,
            short_message="Не заполнены ключи YClients",
            error_category=YCLIENTS_ERROR_CREDENTIALS,
        )

    try:
        async with build_yclients_client_from_active_settings(settings) as client:
            service = YClientsServiceLayer(client, company_id=settings.company_id)
            return await service.health_check(company_id=settings.company_id)
    except YClientsError as exc:
        logger.warning(
            "YClients settings check failed: operation=check_yclients_connection company_id=%s error_class=%s status_code=%s",
            settings.company_id,
            type(exc).__name__,
            exc.status_code,
        )
        return YClientsHealthCheckResult(
            ok=False,
            status_code=exc.status_code,
            short_message="YClients connection failed",
            error_category=exc.error_category,
        )
    except Exception as exc:  # noqa: BLE001 - keep raw technical details away from users.
        logger.warning(
            "YClients settings check failed: operation=check_yclients_connection company_id=%s error_class=%s",
            settings.company_id,
            type(exc).__name__,
        )
        return YClientsHealthCheckResult(
            ok=False,
            status_code=None,
            short_message="YClients connection failed",
            error_category=YCLIENTS_ERROR_TRANSPORT,
        )
