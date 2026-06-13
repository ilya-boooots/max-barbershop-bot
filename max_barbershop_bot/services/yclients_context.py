"""Shared safe loader for active YClients settings used by MAX flows."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings, YClientsSettingsRepository
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE

logger = logging.getLogger(__name__)


def load_active_yclients_settings(
    settings_repository: YClientsSettingsRepository,
    *,
    operation: str,
) -> YClientsSettings | None:
    """Load active DB YClients settings first, falling back to env-only settings."""

    database_path = getattr(settings_repository, "database_path", "unknown")
    settings: YClientsSettings | None = None
    error_class: str | None = None
    source = "none"
    try:
        settings = settings_repository.get_active()
    except Exception as exc:  # noqa: BLE001 - diagnostics must stay sanitized.
        error_class = type(exc).__name__
        _log_diagnostic(
            database_path=database_path,
            settings=None,
            source=source,
            operation=operation,
            error_class=error_class,
        )
        raise

    if settings is not None:
        source = "db"
    else:
        settings = _settings_from_env()
        source = "env" if settings is not None else "none"

    _log_diagnostic(
        database_path=database_path,
        settings=settings,
        source=source,
        operation=operation,
        error_class=error_class,
    )
    return settings


def has_required_yclients_credentials(settings: YClientsSettings | None) -> bool:
    """Return True when settings can build authenticated YClients requests."""

    return bool(
        settings
        and settings.is_active
        and settings.company_id
        and settings.partner_token
        and settings.user_token
    )


def build_yclients_client_from_active_settings(settings: YClientsSettings) -> YClientsClient:
    """Build a YClients client from already loaded active settings without logging secrets."""

    if not has_required_yclients_credentials(settings):
        raise ValueError("YClients settings are incomplete")
    return YClientsClient(
        partner_token=str(settings.partner_token),
        user_token=str(settings.user_token),
        company_id=str(settings.company_id),
    )


def _settings_from_env() -> YClientsSettings | None:
    company_id = _optional_env("YCLIENTS_COMPANY_ID")
    partner_token = _optional_env("YCLIENTS_PARTNER_TOKEN")
    user_token = _optional_env("YCLIENTS_USER_TOKEN")
    if not company_id or not partner_token or not user_token:
        return None
    return YClientsSettings(
        company_id=company_id,
        partner_token=partner_token,
        user_token=user_token,
        branch_timezone=_optional_env("YCLIENTS_BRANCH_TIMEZONE") or DEFAULT_BRANCH_TIMEZONE,
        branch_title=_optional_env("YCLIENTS_BRANCH_TITLE"),
        is_active=True,
    )


def _optional_env(name: str) -> str | None:
    value = getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _log_diagnostic(
    *,
    database_path: str,
    settings: YClientsSettings | None,
    source: str,
    operation: str,
    error_class: str | None,
) -> None:
    logger.info(
        "MAX YClients settings diagnostic: database_path=%s active_settings_found=%s source=%s "
        "company_id_present=%s partner_token_present=%s user_token_present=%s "
        "branch_timezone=%s is_active=%s operation=%s error_class=%s",
        database_path,
        settings is not None,
        source,
        bool(settings and settings.company_id),
        bool(settings and settings.partner_token),
        bool(settings and settings.user_token),
        settings.branch_timezone if settings else None,
        settings.is_active if settings else None,
        operation,
        error_class,
    )
