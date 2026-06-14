"""Admin bookings reports service for MAX."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from os import getenv
from typing import Any

from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.integrations.yclients import (
    YClientsAuthError,
    YClientsConfigError,
    YClientsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
)
from max_barbershop_bot.integrations.yclients.endpoints import get_booking_details, list_bookings_by_date_range, list_staff
from max_barbershop_bot.integrations.yclients.utils import extract_data_rows, extract_first_record, safe_str
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import CompanyTimeService
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 10
STALE_LIST_TEXT = "Этот список записей уже устарел 🙏\n\nОткройте отчёт заново."


@dataclass(frozen=True)
class AdminBookingsFilter:
    """Current admin bookings filter."""

    day: str = "today"
    master_id: str | None = None
    status: str | None = None
    page: int = 0


@dataclass(frozen=True)
class AdminBookingsResult:
    """Loaded YClients bookings plus display metadata."""

    title: str
    bookings: list[dict[str, Any]]
    page_bookings: list[dict[str, Any]]
    statuses: list[str]
    counters: dict[str, int]
    page: int
    max_page: int


class AdminBookingsSettingsMissingError(RuntimeError):
    """Raised when YClients settings are missing."""


class AdminBookingsLoadError(RuntimeError):
    """Raised when YClients bookings cannot be loaded safely."""


async def load_admin_bookings(filters: AdminBookingsFilter, *, actor_platform_user_id_present: bool = False) -> AdminBookingsResult:
    """Load company bookings from YClients for the selected Telegram-equivalent filters."""

    settings = _active_settings()
    if not has_required_yclients_credentials(settings):
        raise AdminBookingsSettingsMissingError("YClients settings are incomplete")
    company_time = CompanyTimeService(_settings_repo())
    base_date = company_time.today() + timedelta(days=1 if filters.day == "tomorrow" else 0)
    title = "Завтра" if filters.day == "tomorrow" else "Сегодня"
    period = base_date.isoformat()
    client = None
    try:
        client = build_yclients_client_from_active_settings(settings)
        company_id = str(settings.company_id)
        payload = await list_bookings_by_date_range(
            client,
            company_id=company_id,
            date_from=period,
            date_to=period,
            staff_id=filters.master_id,
            status=filters.status,
            page=1,
            count=200,
        )
        rows = _extract_rows(payload)
        if filters.status:
            rows = [row for row in rows if _record_status(row) == filters.status]
        rows = sorted(rows, key=lambda row: company_time.localize_datetime(_record_datetime(row)) or company_time.now())
        rows = _enrich_sources(rows)
        statuses = sorted({_record_status(row) for row in rows if _record_status(row)})
        counters = {"confirmed": 0, "pending": 0, "cancelled": 0}
        for row in rows:
            counters[_status_bucket(_record_status(row))] += 1
        max_page = max((len(rows) - 1) // PAGE_SIZE, 0)
        page = min(max(filters.page, 0), max_page)
        start = page * PAGE_SIZE
        logger.info(
            "MAX admin bookings diagnostic: actor_platform_user_id_present=%s period=%s status_filter=%s source_filter=%s yclients_records_count=%s bookings_count=%s selected_record_id_present=%s yclients_error_category=%s http_status=%s trace_id=%s",
            actor_platform_user_id_present,
            filters.day,
            filters.status or "all",
            "all",
            len(rows),
            len(rows),
            False,
            None,
            None,
            None,
        )
        return AdminBookingsResult(title, rows, rows[start : start + PAGE_SIZE], statuses, counters, page, max_page)
    except (YClientsConfigError, ValueError) as exc:
        _log_error(exc, filters, actor_platform_user_id_present)
        raise AdminBookingsSettingsMissingError from exc
    except (YClientsAuthError, YClientsRateLimitError, YClientsServerError, YClientsTransportError, YClientsError) as exc:
        _log_error(exc, filters, actor_platform_user_id_present)
        raise AdminBookingsLoadError from exc
    finally:
        if client is not None:
            await client.close()


async def load_admin_booking_detail(record: dict[str, Any]) -> dict[str, Any]:
    """Refresh one selected booking from YClients by id; fall back to cached row if needed."""

    record_id = _record_id(record)
    if not record_id:
        return record
    settings = _active_settings()
    if not has_required_yclients_credentials(settings):
        return record
    client = None
    try:
        client = build_yclients_client_from_active_settings(settings)
        company_id = str(settings.company_id)
        payload = await get_booking_details(client, company_id=company_id, record_id=record_id)
        item = extract_first_record(payload) or record
        return _enrich_sources([item])[0]
    finally:
        if client is not None:
            await client.close()


async def load_master_options() -> list[tuple[str, str]]:
    """Load staff filter options from YClients."""

    settings = _active_settings()
    if not has_required_yclients_credentials(settings):
        raise AdminBookingsSettingsMissingError("YClients settings are incomplete")
    client = None
    try:
        client = build_yclients_client_from_active_settings(settings)
        company_id = str(settings.company_id)
        payload = await list_staff(client, company_id=company_id)
        options: list[tuple[str, str]] = []
        for row in _extract_rows(payload):
            staff_id = safe_str(row.get("id") or row.get("staff_id"))
            name = safe_str(row.get("name") or row.get("fullname") or row.get("title"))
            if staff_id and name:
                options.append((staff_id, name))
        return options[:30]
    finally:
        if client is not None:
            await client.close()


def format_admin_bookings_list(result: AdminBookingsResult) -> str:
    """Format Telegram-equivalent admin bookings list."""

    lines = [f"📋 Записи — {result.title}"]
    if result.bookings:
        lines.append(f"✅ Подтверждено: {result.counters['confirmed']} | ⏳ Ожидает: {result.counters['pending']} | ❌ Отменено: {result.counters['cancelled']}")
    else:
        lines.append("\n😌 На выбранный день записей нет.")
    return "\n".join(lines)


def format_admin_booking_list_item(item: dict[str, Any]) -> str:
    """Format one booking button label like Telegram."""

    time_label = _company_time().format_time(_record_datetime(item))
    master = _pick(item, ("staff_name", "master_name", "staff"), "Мастер")
    service = _service_label(item)
    phone = _mask_phone(_pick(item, ("phone", "client_phone", "tel"), ""))
    return f"🕒 {time_label} • 👤 {master[:12]} • ✂️ {service[:16]} • 📞 {phone}"


def format_admin_booking_card(item: dict[str, Any]) -> str:
    """Format safe admin booking detail card."""

    lines = [
        "📋 Карточка записи",
        "",
        f"🧾 ID записи: {_record_id(item) or '—'}",
        f"🕒 Дата и время: {_company_time().format_datetime(_record_datetime(item))}",
        f"👤 Мастер: {_pick(item, ('staff_name', 'master_name', 'staff'), '—')}",
        f"✂️ Услуга: {_service_label(item)}",
        f"👤 Клиент: {_pick(item, ('client_name', 'fullname', 'name'), '—')}",
        f"📞 Телефон: {_mask_phone(_pick(item, ('phone', 'client_phone', 'tel'), ''))}",
        f"🧾 Статус: {_record_status(item) or '—'}",
        f"📍 Источник: {_source_label(item)}",
    ]
    comment = _pick(item, ("comment", "notes"), "")
    if comment:
        lines.append(f"📝 Комментарий: {comment}")
    lines.extend(["", "🛠️ Действия: выберите кнопку ниже 👇"])
    return "\n".join(lines)


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    rows = extract_data_rows(payload)
    if isinstance(payload, dict):
        for key in ("records", "result", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = [item for item in value if isinstance(item, dict)]
                break
    return rows


def _pick(item: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = safe_str(item.get(key))
        if value:
            return value
    return default


def _record_id(item: dict[str, Any]) -> str:
    return _pick(item, ("id", "record_id", "booking_id", "visit_id"), "")


def _record_datetime(item: dict[str, Any]) -> str:
    return _pick(item, ("datetime", "date", "start_time", "start"), "")


def _record_status(item: dict[str, Any]) -> str:
    return _pick(item, ("status", "record_status", "state"), "")


def _service_label(item: dict[str, Any]) -> str:
    services = item.get("services")
    if isinstance(services, list) and services and isinstance(services[0], dict):
        title = safe_str(services[0].get("title") or services[0].get("name"))
        if title:
            return title
    return _pick(item, ("service_name", "service", "title"), "Услуга")


def _mask_phone(phone: str | None) -> str:
    raw = safe_str(phone)
    if len(raw) < 6:
        return raw or "—"
    return f"{raw[:2]}***{raw[-3:]}"


def _status_bucket(raw: str) -> str:
    value = raw.lower()
    if "cancel" in value or "отмен" in value:
        return "cancelled"
    if "wait" in value or "нов" in value or "pending" in value:
        return "pending"
    return "confirmed"


def _enrich_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repo = PlatformAttributionRepository(_database_path())
    enriched: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        record_id = _record_id(copy)
        try:
            attribution = repo.get_by_yclients_record_id(record_id) if record_id else None
        except Exception as exc:  # noqa: BLE001 - attribution must not become booking truth.
            logger.warning(
                "MAX admin bookings diagnostic: attribution_lookup_failed=%s selected_record_id_present=%s",
                type(exc).__name__,
                bool(record_id),
            )
            attribution = None
        if attribution is not None:
            copy["_platform_source"] = attribution.platform
        enriched.append(copy)
    return enriched


def _source_label(item: dict[str, Any]) -> str:
    source = safe_str(item.get("_platform_source")).lower()
    if source == "max":
        return "MAX"
    if source:
        return source.capitalize()
    return "не найден"


def _company_time() -> CompanyTimeService:
    return CompanyTimeService(_settings_repo())


def _settings_repo() -> YClientsSettingsRepository:
    return YClientsSettingsRepository(_database_path())


def _active_settings():
    return load_active_yclients_settings(_settings_repo(), operation="admin_bookings")


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


def _log_error(exc: Exception, filters: AdminBookingsFilter, actor_platform_user_id_present: bool) -> None:
    logger.warning(
        "MAX admin bookings diagnostic: actor_platform_user_id_present=%s period=%s status_filter=%s source_filter=%s yclients_records_count=%s bookings_count=%s selected_record_id_present=%s yclients_error_category=%s http_status=%s trace_id=%s",
        actor_platform_user_id_present,
        filters.day,
        filters.status or "all",
        "all",
        0,
        0,
        False,
        type(exc).__name__,
        getattr(exc, "status_code", None),
        getattr(exc, "trace_id", None),
    )
