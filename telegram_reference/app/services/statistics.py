from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.db.telegram_attribution_repo import list_active_telegram_record_ids
from app.integrations.yclients.endpoints import get_booking_details, list_bookings_by_date_range, list_client_visits
from app.integrations.yclients.service import build_yclients_client
from app.repositories.users import count_registered_clients

COMPLETED_STATUSES = {"done", "completed", "visit", "paid"}
CANCELLED_STATUSES = {"cancelled", "canceled", "cancel", "deleted"}
NO_SHOW_STATUSES = {"no_show", "noshow", "not_come", "missed"}
BOT_COMMENT_MARKER = "Клиент записался из телеграм бота"


@dataclass(frozen=True)
class Period:
    title: str
    start: date
    end: date


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


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


def _extract_dt(item: dict[str, Any]) -> datetime | None:
    for key in ("datetime", "date", "start"):
        raw = _s(item.get(key))
        if not raw:
            continue
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            continue
    return None


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


def _is_paid(item: dict[str, Any]) -> bool:
    if any(bool(item.get(key)) for key in ("paid", "is_paid", "paid_full", "fully_paid")):
        return True
    payment_state = _s(item.get("payment_status") or item.get("payment_state") or item.get("paid_status")).lower()
    if payment_state in {"paid", "fully_paid", "closed"}:
        return True
    return _extract_paid_amount(item) > 0 and _extract_status(item) in COMPLETED_STATUSES


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


def _is_cancelled(item: dict[str, Any]) -> bool:
    if item.get("deleted") is True or item.get("is_deleted") is True:
        return True
    deleted = _s(item.get("deleted") if item.get("deleted") is not None else item.get("is_deleted")).lower()
    if deleted in {"1", "true", "yes"}:
        return True
    return _extract_status(item) in CANCELLED_STATUSES


def _is_completed(item: dict[str, Any]) -> bool:
    attendance = _extract_attendance(item)
    if attendance:
        return attendance == "1"
    return _extract_status(item) in COMPLETED_STATUSES


def _is_no_show(item: dict[str, Any]) -> bool:
    attendance = _extract_attendance(item)
    if attendance:
        return attendance == "-1"
    return _extract_status(item) in NO_SHOW_STATUSES


def _is_bot_created_record(item: dict[str, Any], attributed_ids: set[str]) -> bool:
    comment = _s(item.get("comment") or item.get("comments") or item.get("record_comment"))
    if BOT_COMMENT_MARKER.casefold() in comment.casefold():
        return True
    record_id = _extract_record_id(item)
    return bool(record_id and record_id in attributed_ids)


def build_today_period() -> Period:
    today = date.today()
    return Period(title="Сегодня", start=today, end=today)


def build_week_period(anchor: date) -> Period:
    start = anchor - timedelta(days=anchor.weekday())
    end = start + timedelta(days=6)
    return Period(title="Неделя", start=start, end=end)


def build_month_period(anchor: date) -> Period:
    start = anchor.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = next_month - timedelta(days=1)
    return Period(title="Месяц", start=start, end=end)


def build_all_time_period() -> Period:
    return Period(title="За всё время", start=date(2000, 1, 1), end=date.today())


async def fetch_records_for_period(*, company_id: str, period: Period) -> list[dict[str, Any]]:
    client, _ = await build_yclients_client()
    try:
        page = 1
        result: list[dict[str, Any]] = []
        while True:
            payload = await list_bookings_by_date_range(
                client,
                company_id=company_id,
                date_from=period.start.isoformat(),
                date_to=period.end.isoformat(),
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
    finally:
        await client.close()


async def _load_record_details(company_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    client, _ = await build_yclients_client()
    try:
        detailed: list[dict[str, Any]] = []
        for row in rows:
            record_id = _extract_record_id(row)
            if not record_id:
                detailed.append(row)
                continue
            payload = await get_booking_details(client, company_id=company_id, record_id=record_id)
            details = _extract_payload_dict(payload)
            detailed.append({**row, **details} if details else row)
        return detailed
    finally:
        await client.close()


async def business_summary_metrics(*, company_id: str) -> dict[str, Any]:
    bot_registered_clients = await count_registered_clients()
    clients_total = bot_registered_clients
    rows = await fetch_records_for_period(company_id=company_id, period=build_all_time_period())
    attributed_ids = set(await list_active_telegram_record_ids(company_id=company_id))
    candidate_rows = [row for row in rows if _is_bot_created_record(row, attributed_ids)]
    bot_rows = await _load_record_details(company_id, candidate_rows)

    total_revenue = 0.0
    records_with_amount = 0
    completed = 0
    cancelled = 0
    no_show = 0

    for row in bot_rows:
        if _is_completed(row):
            completed += 1
        if _is_cancelled(row):
            cancelled += 1
        if _is_no_show(row):
            no_show += 1

        amount = _extract_record_amount(row)
        if amount <= 0:
            continue
        total_revenue += amount
        records_with_amount += 1

    return {
        "bot_registered_clients": bot_registered_clients,
        "clients_total": clients_total,
        "records_total": len(bot_rows),
        "revenue": total_revenue,
        "avg_check": total_revenue / records_with_amount if records_with_amount else 0.0,
        "completed": completed,
        "cancelled": cancelled,
        "no_show": no_show,
    }


async def branch_day_metrics(*, company_id: str, target_date: date) -> dict[str, Any]:
    period = Period(title=target_date.strftime("%d.%m.%Y"), start=target_date, end=target_date)
    rows = await fetch_records_for_period(company_id=company_id, period=period)
    return await _build_period_metrics(company_id=company_id, rows=rows)


async def _build_period_metrics(*, company_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    client, _ = await build_yclients_client()
    try:
        paid_revenue = 0.0
        paid_count = 0
        completed = 0
        cancelled = 0
        no_show = 0
        service_revenue: defaultdict[str, float] = defaultdict(float)
        master_revenue: defaultdict[str, float] = defaultdict(float)

        for row in rows:
            status = _extract_status(row)
            if status in COMPLETED_STATUSES:
                completed += 1
            elif status in CANCELLED_STATUSES:
                cancelled += 1
            elif status in NO_SHOW_STATUSES:
                no_show += 1

            if not _is_paid(row):
                continue
            record_id = _extract_record_id(row)
            details = row
            if record_id:
                try:
                    details_payload = await get_booking_details(client, company_id=company_id, record_id=record_id)
                    details_rows = _extract_rows(details_payload)
                    if details_rows:
                        details = details_rows[0]
                    elif isinstance(details_payload, dict):
                        details = details_payload.get("data") if isinstance(details_payload.get("data"), dict) else details_payload
                except Exception:
                    details = row
            paid_amount = _extract_paid_amount(details)
            if paid_amount <= 0:
                continue
            paid_revenue += paid_amount
            paid_count += 1

            services = details.get("services") if isinstance(details.get("services"), list) else []
            if services:
                split = paid_amount / max(len([x for x in services if isinstance(x, dict)]), 1)
                for service in services:
                    if not isinstance(service, dict):
                        continue
                    service_name = _s(service.get("title") or service.get("name")) or "Другое"
                    service_revenue[service_name] += split
            else:
                service_name = _s(details.get("service_name") or details.get("service")) or "Другое"
                service_revenue[service_name] += paid_amount

            master_name = _s(details.get("staff_name") or details.get("master_name"))
            if not master_name:
                staff = details.get("staff")
                if isinstance(staff, dict):
                    master_name = _s(staff.get("name"))
            master_revenue[master_name or "Не указан"] += paid_amount

        bookings_total = len(rows)
        avg_check = paid_revenue / paid_count if paid_count else 0.0
        return {
            "bookings_total": bookings_total,
            "paid_visits": paid_count,
            "revenue": paid_revenue,
            "avg_check": avg_check,
            "completed": completed,
            "cancelled": cancelled,
            "no_show": no_show,
            "top_services": sorted(service_revenue.items(), key=lambda item: item[1], reverse=True)[:3],
            "top_masters": sorted(master_revenue.items(), key=lambda item: item[1], reverse=True)[:3],
        }
    finally:
        await client.close()


async def telegram_metrics(*, company_id: str, period: Period, selected_date: date | None = None) -> dict[str, Any]:
    rows = await fetch_records_for_period(company_id=company_id, period=period)
    attributed_ids = set(await list_active_telegram_record_ids(company_id=company_id))
    telegram_rows = [row for row in rows if _extract_record_id(row) in attributed_ids]

    base = await _build_period_metrics(company_id=company_id, rows=telegram_rows)
    all_base = await _build_period_metrics(company_id=company_id, rows=rows)

    client, _ = await build_yclients_client()
    try:
        returning_clients: set[str] = set()
        new_clients: set[str] = set()
        seen_clients: set[str] = set()
        for row in telegram_rows:
            if not _is_paid(row):
                continue
            cid = _extract_client_id(row)
            if not cid:
                continue
            if cid in seen_clients:
                continue
            seen_clients.add(cid)
            dt = _extract_dt(row)
            end_date = (dt.date() - timedelta(days=1)).isoformat() if dt else period.start.isoformat()
            history_payload = await list_client_visits(
                client,
                company_id=company_id,
                client_id=cid,
                page=1,
                count=50,
                end_date=end_date,
            )
            history_rows = _extract_rows(history_payload)
            has_previous_paid = any(_is_paid(item) for item in history_rows)
            if has_previous_paid:
                returning_clients.add(cid)
            else:
                new_clients.add(cid)
    finally:
        await client.close()

    records_count = len(telegram_rows)
    share_revenue = (base["revenue"] / all_base["revenue"] * 100.0) if all_base["revenue"] > 0 else 0.0
    share_records = (records_count / all_base["bookings_total"] * 100.0) if all_base["bookings_total"] > 0 else 0.0

    return {
        "period_title": period.title if selected_date is None else f"{period.title} ({selected_date.strftime('%d.%m.%Y')})",
        "records_count": records_count,
        "revenue": base["revenue"],
        "returning_clients": len(returning_clients),
        "new_clients": len(new_clients),
        "avg_check": base["avg_check"],
        "share_revenue": share_revenue,
        "share_records": share_records,
    }


async def branch_overview_metrics(*, company_id: str, selected_date: date) -> dict[str, Any]:
    today = date.today()
    tomorrow = today + timedelta(days=1)

    today_rows = await fetch_records_for_period(company_id=company_id, period=Period("Сегодня", today, today))
    tomorrow_rows = await fetch_records_for_period(company_id=company_id, period=Period("Завтра", tomorrow, tomorrow))
    selected_rows = await fetch_records_for_period(company_id=company_id, period=Period("Дата", selected_date, selected_date))

    day = await _build_period_metrics(company_id=company_id, rows=selected_rows)
    week = await _build_period_metrics(company_id=company_id, rows=await fetch_records_for_period(company_id=company_id, period=build_week_period(selected_date)))
    month = await _build_period_metrics(company_id=company_id, rows=await fetch_records_for_period(company_id=company_id, period=build_month_period(selected_date)))

    return {
        "clients_today": len(today_rows),
        "clients_tomorrow": len(tomorrow_rows),
        "clients_selected": len(selected_rows),
        "day": day,
        "week": week,
        "month": month,
    }
