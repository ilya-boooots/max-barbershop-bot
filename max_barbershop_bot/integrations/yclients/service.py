"""Transport-neutral service layer composing YClients endpoint calls."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any, Protocol

from .client import DEFAULT_YCLIENTS_BASE_URL, DEFAULT_YCLIENTS_TIMEOUT_SECONDS, YClientsClient
from .dto import (
    YClientsCancelBookingResult,
    YClientsClientCard,
    YClientsHealthCheckResult,
    YClientsService,
    YClientsServiceCategory,
    YClientsSlot,
    YClientsStaff,
    YClientsVisit,
)
from .endpoints import (
    create_booking as endpoint_create_booking,
    create_client as endpoint_create_client,
    cancel_booking as endpoint_cancel_booking,
    get_available_slots as endpoint_get_available_slots,
    get_client_details as endpoint_get_client_details,
    get_company,
    get_future_bookings as endpoint_get_future_bookings,
    get_booking_details as endpoint_get_booking_details,
    get_service_categories as endpoint_get_service_categories,
    get_services as endpoint_get_services,
    get_staff as endpoint_get_staff,
    get_staff_by_service as endpoint_get_staff_by_service,
    list_client_visits as endpoint_list_client_visits,
    list_user_bookings as endpoint_list_user_bookings,
    reschedule_booking as endpoint_reschedule_booking,
    search_clients as endpoint_search_clients,
    update_client as endpoint_update_client,
)
from .exceptions import (
    YClientsAuthError,
    YClientsConfigError,
    YClientsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
)
from .utils import extract_data_rows, safe_str, truthy_bool

logger = logging.getLogger(__name__)


class YClientsNotifier(Protocol):
    """Optional transport-neutral notification hook for integration errors."""

    async def notify_error(self, message: str) -> None:
        """Notify an external observer about a YClients error."""


class YClientsServiceLayer:
    """High-level YClients operations without transport dependencies."""

    def __init__(self, client: YClientsClient, *, company_id: str | None = None, notifier: YClientsNotifier | None = None) -> None:
        self._client = client
        self._company_id = company_id or client.company_id
        self._notifier = notifier

    def require_company_id(self, company_id: str | int | None = None) -> str:
        """Resolve a company id or raise a clear configuration error."""

        resolved = safe_str(company_id) or safe_str(self._company_id)
        if not resolved:
            raise YClientsConfigError("YCLIENTS_COMPANY_ID is required for this YClients operation")
        return resolved

    async def get_available_services(
        self,
        *,
        company_id: str | int | None = None,
        category_id: str | None = None,
        active_only: bool = True,
        bookable_only: bool = True,
    ) -> list[YClientsService]:
        """Return normalized services, optionally filtering active/bookable ones."""

        payload = await endpoint_get_services(
            self._client,
            company_id=self.require_company_id(company_id),
            category_id=category_id,
        )
        services = [_service_from_payload(item) for item in extract_data_rows(payload)]
        if active_only:
            services = [service for service in services if service.active is not False]
        if bookable_only:
            services = [service for service in services if service.bookable is not False]
        return services

    async def get_service_categories(self, *, company_id: str | int | None = None) -> list[YClientsServiceCategory]:
        """Return normalized service categories."""

        payload = await endpoint_get_service_categories(self._client, company_id=self.require_company_id(company_id))
        return [_category_from_payload(item) for item in extract_data_rows(payload)]

    async def get_available_masters(
        self,
        *,
        company_id: str | int | None = None,
        service_id: str | None = None,
        bookable_only: bool = True,
    ) -> list[YClientsStaff]:
        """Return normalized masters/staff, optionally filtered by service."""

        resolved_company_id = self.require_company_id(company_id)
        if service_id:
            payload = await endpoint_get_staff_by_service(self._client, company_id=resolved_company_id, service_id=service_id)
        else:
            payload = await endpoint_get_staff(self._client, company_id=resolved_company_id)
        staff = [_staff_from_payload(item) for item in extract_data_rows(payload)]
        if bookable_only:
            staff = [master for master in staff if master.bookable is not False]
        return staff

    async def get_available_slots(
        self,
        *,
        service_id: str,
        date: str,
        company_id: str | int | None = None,
        staff_id: str | None = None,
    ) -> list[YClientsSlot]:
        """Return normalized slots for a service/date/staff combination."""

        payload = await endpoint_get_available_slots(
            self._client,
            company_id=self.require_company_id(company_id),
            service_id=service_id,
            staff_id=staff_id,
            date=date,
        )
        return [_slot_from_payload(item, fallback_staff_id=staff_id) for item in _extract_slot_rows(payload)]

    async def create_booking(
        self,
        *,
        service_id: str,
        datetime_iso: str,
        phone: str,
        fullname: str,
        company_id: str | int | None = None,
        staff_id: str | None = None,
        email: str = "",
        comment: str = "",
    ):
        """Create a YClients booking with a MAX origin marker."""

        try:
            return await endpoint_create_booking(
                self._client,
                company_id=self.require_company_id(company_id),
                service_id=service_id,
                datetime_iso=datetime_iso,
                phone=phone,
                fullname=fullname,
                staff_id=staff_id,
                email=email,
                comment=comment,
            )
        except YClientsError as exc:
            await self._notify_or_log("create_booking", exc)
            raise

    async def find_client(
        self,
        *,
        query: str,
        company_id: str | int | None = None,
        by_phone: bool = False,
        by_name: bool = False,
        page: int = 1,
        count: int = 10,
    ) -> list[YClientsClientCard]:
        """Search YClients clients and return normalized client cards."""

        payload = await endpoint_search_clients(
            self._client,
            company_id=self.require_company_id(company_id),
            query=query,
            page=page,
            count=count,
            by_phone=by_phone,
            by_name=by_name,
        )
        return [_client_card_from_payload(item) for item in extract_data_rows(payload)]

    async def get_client_card(
        self,
        *,
        yclients_client_id: str,
        company_id: str | int | None = None,
    ) -> YClientsClientCard | None:
        """Return one normalized YClients client card/profile."""

        payload = await endpoint_get_client_details(
            self._client,
            company_id=self.require_company_id(company_id),
            client_id=yclients_client_id,
        )
        rows = extract_data_rows(payload)
        return _client_card_from_payload(rows[0]) if rows else None

    async def create_client(
        self,
        *,
        name: str,
        phone: str,
        company_id: str | int | None = None,
        email: str | None = None,
        comment: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Create a YClients client card/profile."""

        return await endpoint_create_client(
            self._client,
            company_id=self.require_company_id(company_id),
            name=name,
            phone=phone,
            email=email,
            comment=comment,
            extra_fields=extra_fields,
        )

    async def update_client(
        self,
        *,
        yclients_client_id: str,
        payload: dict[str, Any],
        company_id: str | int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Update a YClients client card/profile."""

        return await endpoint_update_client(
            self._client,
            company_id=self.require_company_id(company_id),
            client_id=yclients_client_id,
            payload=payload,
        )

    async def get_client_visits(
        self,
        *,
        yclients_client_id: str,
        company_id: str | int | None = None,
        page: int = 1,
        count: int = 5,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[YClientsVisit]:
        """Return normalized YClients visit/record history."""

        payload = await endpoint_list_client_visits(
            self._client,
            company_id=self.require_company_id(company_id),
            client_id=yclients_client_id,
            page=page,
            count=count,
            start_date=start_date,
            end_date=end_date,
        )
        return [_visit_from_payload(item) for item in extract_data_rows(payload)]

    async def get_client_records(
        self,
        *,
        company_id: str | int | None = None,
        yclients_client_id: str | None = None,
        phone: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int | None = None,
        count: int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Return raw YClients records without using local DB as history source."""

        return await endpoint_list_user_bookings(
            self._client,
            company_id=self.require_company_id(company_id),
            client_id=yclients_client_id,
            phone=phone,
            start_date=start_date,
            end_date=end_date,
            page=page,
            count=count,
        )

    async def get_future_records(
        self,
        *,
        start_date: str,
        company_id: str | int | None = None,
        end_date: str | None = None,
        yclients_client_id: str | None = None,
        phone: str | None = None,
        page: int | None = None,
        count: int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Return future records from YClients using explicit date filters."""

        return await endpoint_get_future_bookings(
            self._client,
            company_id=self.require_company_id(company_id),
            start_date=start_date,
            end_date=end_date,
            client_id=yclients_client_id,
            phone=phone,
            page=page,
            count=count,
        )

    async def get_booking_details(
        self,
        *,
        yclients_record_id: str,
        company_id: str | int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Return a raw YClients record by id."""

        return await endpoint_get_booking_details(
            self._client,
            company_id=self.require_company_id(company_id),
            record_id=yclients_record_id,
        )

    async def reschedule_booking(
        self,
        *,
        yclients_record_id: str,
        services: list[str],
        client_data: dict[str, Any],
        seance_length: int,
        datetime_iso: str,
        staff_id: str,
        company_id: str | int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Reschedule a YClients record by direct record update."""

        try:
            return await endpoint_reschedule_booking(
                self._client,
                company_id=self.require_company_id(company_id),
                record_id=yclients_record_id,
                services=services,
                client_data=client_data,
                seance_length=seance_length,
                datetime_iso=datetime_iso,
                staff_id=staff_id,
            )
        except YClientsError as exc:
            await self._notify_or_log("reschedule_booking", exc)
            raise

    async def cancel_booking(
        self,
        *,
        yclients_record_id: str,
        company_id: str | int | None = None,
    ) -> YClientsCancelBookingResult:
        """Cancel a YClients record by id."""

        try:
            return await endpoint_cancel_booking(
                self._client,
                company_id=self.require_company_id(company_id),
                record_id=yclients_record_id,
            )
        except YClientsError as exc:
            await self._notify_or_log("cancel_booking", exc)
            raise

    async def health_check(self, *, company_id: str | int | None = None) -> YClientsHealthCheckResult:
        """Run a safe read-only YClients health check."""

        try:
            await get_company(self._client, company_id=self.require_company_id(company_id))
            return YClientsHealthCheckResult(ok=True, status_code=200, short_message="Подключение к YClients работает")
        except YClientsError as exc:
            await self._notify_or_log("health_check", exc)
            return health_result_from_error(exc)

    async def _notify_or_log(self, action: str, exc: Exception) -> None:
        message = f"YClients error during {action}: {type(exc).__name__}: {str(exc)[:200]}"
        logger.warning(message)
        if self._notifier is not None:
            await self._notifier.notify_error(message)


# Convenience function aliases requested by the integration layer contract.
async def get_available_services(service: YClientsServiceLayer, **kwargs: Any) -> list[YClientsService]:
    return await service.get_available_services(**kwargs)


async def get_available_masters(service: YClientsServiceLayer, **kwargs: Any) -> list[YClientsStaff]:
    return await service.get_available_masters(**kwargs)


async def get_available_slots(service: YClientsServiceLayer, **kwargs: Any) -> list[YClientsSlot]:
    return await service.get_available_slots(**kwargs)


async def create_booking(service: YClientsServiceLayer, **kwargs: Any):
    return await service.create_booking(**kwargs)


async def find_client(service: YClientsServiceLayer, **kwargs: Any) -> list[YClientsClientCard]:
    return await service.find_client(**kwargs)


async def get_client_card(service: YClientsServiceLayer, **kwargs: Any) -> YClientsClientCard | None:
    return await service.get_client_card(**kwargs)


async def get_client_visits(service: YClientsServiceLayer, **kwargs: Any) -> list[YClientsVisit]:
    return await service.get_client_visits(**kwargs)


async def get_client_records(service: YClientsServiceLayer, **kwargs: Any) -> dict[str, Any] | list[Any]:
    return await service.get_client_records(**kwargs)


async def get_booking_details(service: YClientsServiceLayer, **kwargs: Any) -> dict[str, Any] | list[Any]:
    return await service.get_booking_details(**kwargs)


async def reschedule_booking(service: YClientsServiceLayer, **kwargs: Any) -> dict[str, Any] | list[Any]:
    return await service.reschedule_booking(**kwargs)


async def cancel_booking(service: YClientsServiceLayer, **kwargs: Any) -> YClientsCancelBookingResult:
    return await service.cancel_booking(**kwargs)


def build_yclients_client_from_env() -> YClientsClient:
    """Build a YClients client from optional environment variables.

    Missing values do not affect application startup unless this factory is
    called to construct a real YClients client.
    """

    partner_token = os.getenv("YCLIENTS_PARTNER_TOKEN", "").strip()
    if not partner_token:
        raise YClientsConfigError("YCLIENTS_PARTNER_TOKEN is required to build a YClients client")

    company_id = os.getenv("YCLIENTS_COMPANY_ID", "").strip() or None
    timeout_seconds = _float_env("YCLIENTS_TIMEOUT_SECONDS", DEFAULT_YCLIENTS_TIMEOUT_SECONDS)
    return YClientsClient(
        partner_token=partner_token,
        user_token=os.getenv("YCLIENTS_USER_TOKEN", "").strip() or None,
        company_id=company_id,
        base_url=os.getenv("YCLIENTS_BASE_URL", DEFAULT_YCLIENTS_BASE_URL).strip() or DEFAULT_YCLIENTS_BASE_URL,
        timeout_seconds=timeout_seconds,
    )


def health_result_from_error(exc: YClientsError) -> YClientsHealthCheckResult:
    """Map custom YClients exceptions to simple health-check results."""

    if isinstance(exc, YClientsAuthError):
        return YClientsHealthCheckResult(ok=False, status_code=401, short_message="Ошибка токенов доступа")
    if isinstance(exc, YClientsRateLimitError):
        return YClientsHealthCheckResult(ok=False, status_code=429, short_message="Слишком много запросов к API")
    if isinstance(exc, YClientsServerError):
        return YClientsHealthCheckResult(ok=False, status_code=503, short_message="Сервис YClients временно недоступен")
    if isinstance(exc, YClientsTransportError):
        return YClientsHealthCheckResult(ok=False, status_code=None, short_message="Нет соединения с API YClients")
    if isinstance(exc, YClientsConfigError):
        return YClientsHealthCheckResult(ok=False, status_code=None, short_message="Не заполнены ключи YClients")
    return YClientsHealthCheckResult(ok=False, status_code=None, short_message="Ошибка проверки YClients")


def _category_from_payload(item: dict[str, Any]) -> YClientsServiceCategory:
    return YClientsServiceCategory(
        id=safe_str(item.get("id") or item.get("category_id")),
        title=safe_str(item.get("title") or item.get("name")) or None,
        raw=item,
    )


def _service_from_payload(item: dict[str, Any]) -> YClientsService:
    return YClientsService(
        id=safe_str(item.get("id") or item.get("service_id")),
        title=safe_str(item.get("title") or item.get("name")) or None,
        category_id=safe_str(item.get("category_id") or item.get("category")) or None,
        price_min=item.get("price_min") or item.get("price"),
        price_max=item.get("price_max") or item.get("price"),
        active=truthy_bool(item.get("active") if "active" in item else item.get("is_active")),
        bookable=truthy_bool(item.get("bookable") if "bookable" in item else item.get("is_bookable")),
        raw=item,
    )


def _staff_from_payload(item: dict[str, Any]) -> YClientsStaff:
    return YClientsStaff(
        id=safe_str(item.get("id") or item.get("staff_id")),
        name=safe_str(item.get("name") or item.get("title")) or None,
        specialization=safe_str(item.get("specialization") or item.get("position") or item.get("profession")) or None,
        avatar=safe_str(item.get("avatar") or item.get("image") or item.get("photo")) or None,
        bookable=truthy_bool(item.get("bookable") if "bookable" in item else item.get("is_bookable")),
        raw=item,
    )



def _extract_slot_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    candidates: list[Any]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("times"), list):
            candidates = list(data["times"])
        elif isinstance(data, list):
            candidates = list(data)
        else:
            candidates = [payload]
    elif isinstance(payload, list):
        candidates = list(payload)
    else:
        candidates = []
    rows: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, dict):
            rows.append(item)
        elif item is not None:
            rows.append({"time": item})
    return rows

def _slot_from_payload(item: dict[str, Any], *, fallback_staff_id: str | None = None) -> YClientsSlot:
    return YClientsSlot(
        datetime=safe_str(item.get("datetime") or item.get("date")) or None,
        time=safe_str(item.get("time")) or None,
        staff_id=safe_str(item.get("staff_id") or fallback_staff_id) or None,
        raw=item,
    )


def _client_card_from_payload(item: dict[str, Any]) -> YClientsClientCard:
    return YClientsClientCard(
        id=safe_str(item.get("id") or item.get("client_id")),
        name=safe_str(item.get("name") or item.get("fullname")) or None,
        phone=_first_phone(item),
        email=safe_str(item.get("email")) or None,
        raw=item,
    )


def _visit_from_payload(item: dict[str, Any]) -> YClientsVisit:
    return YClientsVisit(
        id=safe_str(item.get("id") or item.get("record_id") or item.get("visit_id")),
        datetime=safe_str(item.get("datetime") or item.get("date")) or None,
        status=safe_str(item.get("status") or item.get("attendance") or item.get("state")) or None,
        raw=item,
    )


def _first_phone(item: dict[str, Any]) -> str | None:
    for key in ("phone", "tel"):
        value = item.get(key)
        if value:
            return safe_str(value) or None
    phones = item.get("phones")
    if isinstance(phones, Sequence) and not isinstance(phones, str):
        for phone in phones:
            if isinstance(phone, dict):
                value = phone.get("phone") or phone.get("number")
            else:
                value = phone
            if value:
                return safe_str(value) or None
    return None


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise YClientsConfigError(f"{name} must be a number") from exc
