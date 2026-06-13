"""Small DTOs used by the transport-neutral YClients integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class YClientsCredentials:
    """YClients credentials needed to authorize API requests."""

    partner_token: str
    user_token: str | None = None
    company_id: str | None = None


@dataclass(frozen=True)
class YClientsServiceCategory:
    id: str
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsService:
    id: str
    title: str | None = None
    category_id: str | None = None
    price_min: int | float | None = None
    price_max: int | float | None = None
    active: bool | None = None
    bookable: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsStaff:
    id: str
    name: str | None = None
    specialization: str | None = None
    avatar: str | None = None
    bookable: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsSlot:
    datetime: str | None = None
    time: str | None = None
    staff_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsBookingRecord:
    record_id: str
    datetime: str | None = None
    staff_name: str | None = None
    service_name: str | None = None
    raw_payload: dict[str, Any] | list[Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsCancelBookingResult:
    record_id: str
    status: str | None = None
    raw_payload: dict[str, Any] | list[Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsClientCard:
    id: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsVisit:
    id: str
    datetime: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class YClientsHealthCheckResult:
    ok: bool
    status_code: int | None
    short_message: str
    error_category: str | None = None
