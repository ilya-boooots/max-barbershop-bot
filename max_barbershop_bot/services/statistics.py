"""Statistics service for the MAX bot using YClients as the business source of truth."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from os import getenv
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.repositories.platform_attribution import PLATFORM_MAX, PlatformAttributionRepository
from max_barbershop_bot.repositories.yclients_settings import DEFAULT_BRANCH_TIMEZONE, YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

COMPLETED_STATUSES = {"done", "completed", "visit", "paid"}
CANCELLED_STATUSES = {"cancelled", "canceled", "cancel", "deleted"}
NO_SHOW_STATUSES = {"no_show", "noshow", "not_come", "missed"}

SETTINGS_MISSING_TEXT = """YClients пока не настроен 🙏

Сначала добавьте данные подключения."""
LOAD_ERROR_TEXT = """Не удалось загрузить статистику 🙏

Пожалуйста, попробуйте позже."""
EMPTY_PERIOD_TEXT = "За этот период записей пока нет 📭"


@dataclass(frozen=True)
class StatisticsPeriod:
    """Resolved statistics period in branch-local dates."""

    label: str
    start: date
    end: date


@dataclass(frozen=True)
class AttributionStats:
    """MAX attribution counters for YClients records in a period."""

    max_records_count: int
    max_revenue: float | None


@dataclass(frozen=True)
class StatisticsResult:
    """Business statistics shown to staff."""

    period_label: str
    records_count: int
    revenue: float | None
    new_clients_count: int | None
    returning_clients_count: int | None
    max_records_count: int
    max_revenue: float | None


class StatisticsSettingsMissingError(RuntimeError):
    """Raised when active YClients settings are missing or incomplete."""


class StatisticsLoadError(RuntimeError):
    """Raised when statistics cannot be loaded from YClients."""


async def get_statistics_for_period(days: int | None, period_name: str) -> StatisticsResult:
    """Load period statistics from YClients and MAX attribution from the local DB."""

    settings = load_active_yclients_settings(
        YClientsSettingsRepository(_database_path()),
        operation="get_statistics",
    )
    if not has_required_yclients_credentials(settings):
        raise StatisticsSettingsMissingError("YClients settings are incomplete")

    timezone = _safe_zoneinfo(settings.branch_timezone)
    period = _build_period(days=days, period_name=period_name, timezone=timezone)

    try:
        async with build_yclients_client_from_active_settings(settings) as client:
            service = YClientsServiceLayer(client, company_id=settings.company_id)
            rows = await get_records_for_period_from_yclients(service, period=period)
            detailed_rows = await _load_record_details(service, rows)
            valid_rows = [row for row in detailed_rows if _is_valid_business_record(row)]
            revenue = calculate_revenue(valid_rows)
            new_count, returning_count = await calculate_new_returning_clients(
                service,
                period=period,
                records=valid_rows,
            )
            attribution = calculate_platform_attribution(valid_rows, revenue_available=revenue is not None)
    except YClientsError as exc:
        logger.warning(
            "Statistics YClients load failed: operation=get_statistics period=%s error_class=%s status_code=%s",
            period.label,
            type(exc).__name__,
            exc.status_code,
        )
        raise StatisticsLoadError("YClients request failed") from exc
    except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
        logger.warning(
            "Statistics load failed: operation=get_statistics period=%s error_class=%s",
            period.label,
            type(exc).__name__,
        )
        raise StatisticsLoadError("Statistics load failed") from exc

    logger.info(
        "Statistics loaded: operation=get_statistics period=%s records_count=%s attribution_count=%s",
        period.label,
        len(valid_rows),
        attribution.max_records_count,
    )
    return StatisticsResult(
        period_label=period.label,
        records_count=len(valid_rows),
        revenue=revenue,
        new_clients_count=new_count,
        returning_clients_count=returning_count,
        max_records_count=attribution.max_records_count,
        max_revenue=attribution.max_revenue,
    )


async def get_records_for_period_from_yclients(
    service: YClientsServiceLayer,
    *,
    period: StatisticsPeriod,
) -> list[dict[str, Any]]:
    """Fetch all YClients records for a period via the records endpoint."""

    page = 1
    result: list[dict[str, Any]] = []
    while True:
        payload = await service.get_client_records(
            start_date=period.start.isoformat(),
            end_date=period.end.isoformat(),
            page=page,
            count=200,
        )
        rows = _extract_rows(payload)
        if not rows:
            break
        result.extend(rows)
        if len(rows) < 200:
            break
        page += 1
    return result


def calculate_revenue(records: list[dict[str, Any]]) -> float | None:
    """Calculate revenue from verified paid/amount fields in YClients records."""

    return sum(_extract_record_amount(row) for row in records)


async def calculate_new_returning_clients(
    service: YClientsServiceLayer,
    *,
    period: StatisticsPeriod,
    records: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    """Calculate new/returning clients using YClients record history."""

    client_ids = sorted({_extract_client_id(row) for row in records if _extract_client_id(row)})
    if not client_ids:
        return (0, 0) if not records else (None, None)

    new_clients = 0
    returning_clients = 0
    day_before_period = period.start - timedelta(days=1)
    for client_id in client_ids:
        payload = await service.get_client_records(
            yclients_client_id=client_id,
            end_date=day_before_period.isoformat(),
            page=1,
            count=1,
        )
        previous_rows = [row for row in _extract_rows(payload) if _is_valid_business_record(row)]
        if previous_rows:
            returning_clients += 1
        else:
            new_clients += 1
    return new_clients, returning_clients


def calculate_platform_attribution(records: list[dict[str, Any]], *, revenue_available: bool = True) -> AttributionStats:
    """Match YClients record ids with local MAX platform attribution rows."""

    record_amount_by_id = {_extract_record_id(row): _extract_record_amount(row) for row in records if _extract_record_id(row)}
    if not record_amount_by_id:
        return AttributionStats(max_records_count=0, max_revenue=0.0 if revenue_available else None)

    attributed_ids = set(
        PlatformAttributionRepository(_database_path()).list_active_yclients_record_ids(
            platform=PLATFORM_MAX,
            yclients_record_ids=record_amount_by_id.keys(),
        )
    )
    max_revenue = sum(amount for record_id, amount in record_amount_by_id.items() if record_id in attributed_ids)
    return AttributionStats(
        max_records_count=len(attributed_ids),
        max_revenue=max_revenue if revenue_available else None,
    )


def format_money(value: float | int | None) -> str:
    """Format ruble values for Russian UI."""

    if value is None:
        return "недоступно"
    return f"{int(round(float(value))):,}".replace(",", " ") + " ₽"


def format_statistics_text(result: StatisticsResult) -> str:
    """Format statistics result for the MAX staff screen."""

    if result.records_count == 0:
        return "\n".join(
            [
                f"📊 Статистика за {result.period_label}",
                "",
                EMPTY_PERIOD_TEXT,
                "",
                f"📅 Записей: {result.records_count}",
                f"💰 Выручка: {format_money(result.revenue)}",
                f"🆕 Новых клиентов: {_format_count(result.new_clients_count)}",
                f"🔁 Возвратных клиентов: {_format_count(result.returning_clients_count)}",
                "",
                "🧩 MAX:",
                f"Записей из MAX: {result.max_records_count}",
                f"Выручка из MAX: {format_money(result.max_revenue)}",
            ]
        )

    return "\n".join(
        [
            f"📊 Статистика за {result.period_label}",
            "",
            f"📅 Записей: {result.records_count}",
            f"💰 Выручка: {format_money(result.revenue)}",
            f"🆕 Новых клиентов: {_format_count(result.new_clients_count)}",
            f"🔁 Возвратных клиентов: {_format_count(result.returning_clients_count)}",
            "",
            "🧩 MAX:",
            f"Записей из MAX: {result.max_records_count}",
            f"Выручка из MAX: {format_money(result.max_revenue)}",
        ]
    )


async def _load_record_details(service: YClientsServiceLayer, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detailed: list[dict[str, Any]] = []
    for row in rows:
        record_id = _extract_record_id(row)
        if not record_id:
            detailed.append(row)
            continue
        try:
            payload = await service.get_booking_details(yclients_record_id=record_id)
        except YClientsError:
            detailed.append(row)
            continue
        details = _extract_payload_dict(payload)
        detailed.append({**row, **details} if details else row)
    return detailed


def _build_period(*, days: int | None, period_name: str, timezone: ZoneInfo) -> StatisticsPeriod:
    today = datetime.now(timezone).date()
    if days is None or days <= 1:
        return StatisticsPeriod(label=period_name or "Сегодня", start=today, end=today)
    return StatisticsPeriod(label=period_name, start=today - timedelta(days=days - 1), end=today)


def _safe_zoneinfo(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((value or DEFAULT_BRANCH_TIMEZONE).strip() or DEFAULT_BRANCH_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)


def _is_valid_business_record(item: dict[str, Any]) -> bool:
    return not _is_cancelled(item) and not _is_no_show(item)


def _is_cancelled(item: dict[str, Any]) -> bool:
    if item.get("deleted") is True or item.get("is_deleted") is True:
        return True
    deleted = _s(item.get("deleted") if item.get("deleted") is not None else item.get("is_deleted")).lower()
    if deleted in {"1", "true", "yes"}:
        return True
    return _extract_status(item) in CANCELLED_STATUSES


def _is_no_show(item: dict[str, Any]) -> bool:
    attendance = _extract_attendance(item)
    if attendance:
        return attendance == "-1"
    return _extract_status(item) in NO_SHOW_STATUSES


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "items", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_payload_dict(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    rows = _extract_rows(payload)
    if rows:
        return rows[0]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _extract_status(item: dict[str, Any]) -> str:
    return _s(item.get("status") or item.get("record_status") or item.get("state")).lower()


def _extract_attendance(item: dict[str, Any]) -> str:
    return _s(item.get("attendance") if item.get("attendance") is not None else item.get("visit_attendance"))


def _extract_record_id(item: dict[str, Any]) -> str:
    return _s(item.get("id") or item.get("record_id") or item.get("booking_id") or item.get("visit_id"))


def _extract_client_id(item: dict[str, Any]) -> str | None:
    direct = _s(item.get("client_id") or item.get("person_id") or item.get("customer_id"))
    if direct:
        return direct
    client = item.get("client")
    if isinstance(client, dict):
        nested = _s(client.get("id") or client.get("client_id") or client.get("person_id"))
        return nested or None
    return None


def _extract_record_amount(item: dict[str, Any]) -> float:
    paid_amount = _extract_paid_amount(item)
    if paid_amount > 0:
        return paid_amount
    for key in ("final_price", "total_price", "amount", "sum", "price", "cost", "price_min"):
        amount = _to_float(item.get(key))
        if amount > 0:
            return amount
    service_amount = _extract_service_amount(item)
    if service_amount > 0:
        return service_amount
    for key in ("service", "appointment"):
        block = item.get(key)
        if not isinstance(block, dict):
            continue
        for nested_key in ("final_price", "total_price", "amount", "sum", "price", "cost", "price_min"):
            amount = _to_float(block.get(nested_key))
            if amount > 0:
                return amount
    return 0.0


def _extract_paid_amount(item: dict[str, Any]) -> float:
    for key in (
        "paid_amount",
        "paid_sum",
        "amount_paid",
        "sum_paid",
        "total_paid",
        "payment_total",
        "invoice_total",
        "amount_to_pay",
        "paid",
    ):
        amount = _to_float(item.get(key))
        if amount > 0:
            return amount

    payments = item.get("payments")
    if isinstance(payments, list):
        total = 0.0
        for payment in payments:
            if not isinstance(payment, dict):
                continue
            val = _to_float(payment.get("amount") or payment.get("sum") or payment.get("paid_amount"))
            if val > 0:
                total += val
        if total > 0:
            return total

    invoice = item.get("invoice")
    if isinstance(invoice, dict):
        val = _to_float(invoice.get("paid") or invoice.get("paid_amount") or invoice.get("sum"))
        if val > 0:
            return val
    return 0.0


def _extract_service_amount(item: dict[str, Any]) -> float:
    services = item.get("services")
    if not isinstance(services, list):
        return 0.0
    total = 0.0
    for service in services:
        if not isinstance(service, dict):
            continue
        for key in ("discount_price", "price", "cost", "first_cost", "price_min", "value"):
            amount = _to_float(service.get(key))
            if amount > 0:
                total += amount
                break
    return total


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = _s(value).replace("₽", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _format_count(value: int | None) -> str:
    return str(value) if value is not None else "недоступно"


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
