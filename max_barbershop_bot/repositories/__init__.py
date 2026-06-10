"""Repository layer for MAX Barbershop Bot persistence."""

from max_barbershop_bot.repositories.staff_roles import StaffRole, StaffRolesRepository
from max_barbershop_bot.repositories.settings_audit import SettingsAuditRecord, SettingsAuditRepository
from max_barbershop_bot.repositories.platform_attribution import (
    AttributionRecord,
    PlatformAttributionRepository,
)
from max_barbershop_bot.repositories.yclients_settings import (
    YClientsSettings,
    YClientsSettingsRepository,
)

__all__ = [
    "StaffRole",
    "StaffRolesRepository",
    "SettingsAuditRecord",
    "SettingsAuditRepository",
    "AttributionRecord",
    "PlatformAttributionRepository",
    "YClientsSettings",
    "YClientsSettingsRepository",
]
