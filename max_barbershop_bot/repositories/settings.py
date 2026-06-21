"""Compatibility imports for app settings repository."""

from __future__ import annotations

from max_barbershop_bot.repositories.app_settings import (  # noqa: F401
    DEFAULT_NOTIFICATIONS_ENABLED,
    NOTIFICATIONS_ENABLED_KEY,
    AppSettingsRepository,
)

SettingsRepository = AppSettingsRepository
