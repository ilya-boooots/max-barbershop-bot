"""Transport-neutral booking service for YClients service selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.dto import YClientsService, YClientsServiceCategory
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository

logger = logging.getLogger(__name__)

BOOKING_NOT_CONFIGURED_TEXT = "Запись пока не настроена 🙏\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
BOOKING_YCLIENTS_ERROR_TEXT = "Не получилось загрузить услуги для записи 🙏\n\nПожалуйста, попробуйте позже."


class BookingServiceError(RuntimeError):
    """Clean booking domain error safe for UI flow handling."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class BookingSettingsMissingError(BookingServiceError):
    """Raised when active YClients settings are absent or incomplete."""


class BookingYClientsError(BookingServiceError):
    """Raised when YClients cannot provide booking services safely."""


@dataclass(frozen=True)
class BookingCategory:
    """A YClients service category normalized for booking step 1."""

    yclients_category_id: str
    title: str


@dataclass(frozen=True)
class BookingServiceItem:
    """A YClients service normalized for booking step 1."""

    yclients_service_id: str
    title: str
    yclients_category_id: str | None = None
    category_title: str | None = None
    price_min: int | float | None = None
    price_max: int | float | None = None


@dataclass(frozen=True)
class BookingCatalog:
    """Service categories and services loaded from YClients."""

    categories: list[BookingCategory]
    services: list[BookingServiceItem]


class BookingService:
    """Load and normalize YClients services without bot transport dependencies."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    async def get_service_categories_and_services(self) -> BookingCatalog:
        """Return available service categories and services from active YClients settings."""

        try:
            settings = self._settings_repository.get_active()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking settings lookup failed: operation=get_booking_catalog error_class=%s",
                type(exc).__name__,
            )
            raise BookingSettingsMissingError(BOOKING_NOT_CONFIGURED_TEXT) from exc

        if settings is None or not settings.company_id or not settings.partner_token:
            logger.info(
                "Booking catalog unavailable: operation=get_booking_catalog settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s",
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
            )
            raise BookingSettingsMissingError(BOOKING_NOT_CONFIGURED_TEXT)

        try:
            async with YClientsClient(
                partner_token=settings.partner_token,
                user_token=settings.user_token,
                company_id=settings.company_id,
            ) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                categories_payload = await yclients.get_service_categories(company_id=settings.company_id)
                services_payload = await yclients.get_available_services(company_id=settings.company_id)
        except YClientsError as exc:
            logger.warning(
                "Booking YClients error: operation=get_booking_catalog error_class=%s status_code=%s "
                "partner_token_present=%s user_token_present=%s",
                type(exc).__name__,
                exc.status_code,
                exc.partner_token_present,
                exc.user_token_present,
            )
            raise BookingYClientsError(BOOKING_YCLIENTS_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "Booking unexpected YClients error: operation=get_booking_catalog error_class=%s",
                type(exc).__name__,
            )
            raise BookingYClientsError(BOOKING_YCLIENTS_ERROR_TEXT) from exc

        services = [_normalize_service(item) for item in services_payload]
        services = [item for item in services if item.yclients_service_id and item.title]
        service_category_ids = {item.yclients_category_id for item in services if item.yclients_category_id}

        categories = [_normalize_category(item) for item in categories_payload]
        categories = [
            item
            for item in categories
            if item.yclients_category_id and item.title and item.yclients_category_id in service_category_ids
        ]
        if not categories:
            categories = _categories_from_services(services)

        logger.info(
            "Booking catalog loaded: operation=get_booking_catalog category_count=%s service_count=%s",
            len(categories),
            len(services),
        )
        return BookingCatalog(categories=categories, services=services)


def group_services_by_category(
    categories: list[BookingCategory] | list[dict[str, Any]],
    services: list[BookingServiceItem] | list[dict[str, Any]],
) -> dict[str, list[BookingServiceItem]]:
    """Group normalized services by category id, accepting dicts for simple smoke checks."""

    normalized_categories = [_normalize_category(item) for item in categories]
    normalized_services = [_normalize_service(item) for item in services]
    category_ids = {category.yclients_category_id for category in normalized_categories}
    grouped: dict[str, list[BookingServiceItem]] = {category_id: [] for category_id in category_ids if category_id}
    for service in normalized_services:
        category_id = service.yclients_category_id
        if category_id and category_id in grouped:
            grouped[category_id].append(service)
    return grouped


def has_available_services(catalog: BookingCatalog) -> bool:
    """Return True when YClients returned at least one displayable service."""

    return bool(catalog.services)


def format_service_title(service: BookingServiceItem | dict[str, Any]) -> str:
    """Format a booking service button title in the reference bot style."""

    normalized = _normalize_service(service)
    details: list[str] = []
    price = _format_price(normalized)
    if price:
        details.append(price)
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{normalized.title}{suffix}"


def _normalize_category(item: BookingCategory | YClientsServiceCategory | dict[str, Any]) -> BookingCategory:
    if isinstance(item, BookingCategory):
        return item
    if isinstance(item, YClientsServiceCategory):
        return BookingCategory(yclients_category_id=item.id, title=item.title or "")
    category_id = _clean_text(item.get("yclients_category_id") or item.get("id") or item.get("category_id"))
    title = _clean_text(item.get("title") or item.get("name"))
    return BookingCategory(yclients_category_id=category_id, title=title)


def _normalize_service(item: BookingServiceItem | YClientsService | dict[str, Any]) -> BookingServiceItem:
    if isinstance(item, BookingServiceItem):
        return item
    if isinstance(item, YClientsService):
        category_title = _clean_text(item.raw.get("category_title") or item.raw.get("category_name")) or None
        return BookingServiceItem(
            yclients_service_id=item.id,
            title=item.title or "",
            yclients_category_id=item.category_id,
            category_title=category_title,
            price_min=item.price_min,
            price_max=item.price_max,
        )
    return BookingServiceItem(
        yclients_service_id=_clean_text(item.get("yclients_service_id") or item.get("id") or item.get("service_id")),
        title=_clean_text(item.get("title") or item.get("name")),
        yclients_category_id=_clean_text(item.get("yclients_category_id") or item.get("category_id") or item.get("category")) or None,
        category_title=_clean_text(item.get("category_title") or item.get("category_name")) or None,
        price_min=item.get("price_min") or item.get("price") or item.get("cost"),
        price_max=item.get("price_max") or item.get("price") or item.get("cost"),
    )


def _categories_from_services(services: list[BookingServiceItem]) -> list[BookingCategory]:
    grouped: dict[str, str] = {}
    for service in services:
        category_id = service.yclients_category_id
        if not category_id or _is_empty_category(category_id, service.category_title or ""):
            continue
        grouped[category_id] = service.category_title or "Другое"
    return [BookingCategory(yclients_category_id=category_id, title=title) for category_id, title in sorted(grouped.items(), key=lambda item: item[1])]


def _is_empty_category(category_id: str, title: str) -> bool:
    if not title.strip():
        return True
    if title.strip().lower() == "без группы":
        return True
    return category_id.strip().lower() in {"", "0", "none", "null"}


def _format_price(service: BookingServiceItem) -> str | None:
    price = service.price_min if service.price_min not in (None, "") else service.price_max
    if price in (None, ""):
        return None
    return f"{price} ₽"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
