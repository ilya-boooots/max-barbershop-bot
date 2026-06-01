from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db.sqlite import execute, fetchall, fetchone
from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import get_services, get_staff, list_bookings_by_date_range, list_clients, list_service_categories, search_clients
from app.repositories.yclients_settings import get_yclients_settings
from app.services.company_time import resolve_company_timezone

logger = logging.getLogger(__name__)

PARTIAL_DATA_WARNING = "⚠️ Сегмент рассчитан по доступным данным. Для полной точности нужна синхронизация с YClients."
YCLIENTS_PICKER_TIMEOUT_S = 8
SEGMENT_CACHE_TTL_HOURS = 4

SEGMENTS = {
    "all_clients": "👥 Все клиенты",
    "active_30": "🔥 Активные за 30 дней",
    "inactive_30": "😴 Не были 30 дней",
    "inactive_60": "😴 Не были 60 дней",
    "inactive_90": "😴 Не были 90 дней",
    "no_future_booking": "📅 Без будущей записи",
    "cancelled_30": "❌ Отменили запись",
    "bad_rating": "⭐ С плохой оценкой",
    "birthday_soon": "🎂 День рождения скоро",
}

DESCRIPTIONS = {
    "all_clients": "Все клиенты, которых бот может идентифицировать и которым потенциально можно отправлять уведомления.",
    "active_30": "Клиенты, которые были активны за последние 30 дней.",
    "inactive_30": "Клиенты, которые не были 30+ дней и не имеют будущей записи.",
    "inactive_60": "Клиенты, которые не были 60+ дней и могут нуждаться в мягком возврате.",
    "inactive_90": "Клиенты, которые давно не возвращались и требуют отдельного сценария возврата.",
    "no_future_booking": "Клиенты, у которых сейчас нет будущей записи.",
    "cancelled_30": "Клиенты, которые отменяли запись и могут вернуться через мягкое напоминание.",
    "bad_rating": "Клиенты, которые поставили низкую оценку и требуют аккуратной работы.",
    "birthday_soon": "Клиенты, у которых скоро день рождения. Их можно поздравить и пригласить на визит.",
}


@dataclass
class SegmentSummary:
    key: str
    title: str
    description: str
    count: int
    updated_local: str | None
    warning: str | None = None
    auto_refresh_failed: bool = False


@dataclass
class SegmentCountResult:
    count: int
    partial: bool = False
    error_summary: str | None = None


@dataclass
class NamedSegmentResult:
    title: str
    description: str
    count: int
    updated_local: str
    warning: str | None = PARTIAL_DATA_WARNING



def _short_hash(value: Any) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:12]


def _safe_service_category_callback_id(category_id: str, category_name: str) -> str:
    category_id = _normalize_id(category_id)
    category_name = _normalize_id(category_name)
    if category_id.startswith("name:"):
        raw_name = category_id.removeprefix("name:") or category_name
        candidate = f"name:{raw_name.casefold().strip()}"
        if len(candidate.encode("utf-8")) <= 21:
            return candidate
        return f"namehash:{_short_hash(raw_name.casefold().strip())}"
    if category_id and len(category_id.encode("utf-8")) <= 21:
        return category_id
    if category_id:
        return f"cidhash:{_short_hash(category_id)}"
    if category_name:
        norm_name = category_name.casefold().strip()
        candidate = f"name:{norm_name}"
        if len(candidate.encode("utf-8")) <= 21:
            return candidate
        return f"namehash:{_short_hash(norm_name)}"
    return "uncategorized"


def _service_category_callback_id_matches(callback_id: str, raw_category_id: str, raw_category_name: str) -> bool:
    callback_id = _normalize_id(callback_id)
    raw_category_id = _normalize_id(raw_category_id)
    norm_name = _normalize_id(raw_category_name).casefold().strip()
    if callback_id == "uncategorized":
        return not raw_category_id and not norm_name
    if callback_id.startswith("namehash:"):
        return bool(norm_name) and callback_id == f"namehash:{_short_hash(norm_name)}"
    if callback_id.startswith("name:"):
        return norm_name == callback_id.removeprefix("name:").casefold().strip()
    if callback_id.startswith("cidhash:"):
        return bool(raw_category_id) and callback_id == f"cidhash:{_short_hash(raw_category_id)}"
    return raw_category_id == callback_id

def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        return default


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


class ClientSegmentService:
    def _record_datetime_utc(self, record: dict[str, Any]) -> datetime | None:
        raw = record.get("datetime") or record.get("date")
        if raw is None:
            return None
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw), tz=timezone.utc)
            text = str(raw).strip()
            if not text:
                return None
            if text.isdigit():
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            text = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _record_client_identity(self, record: dict[str, Any]) -> dict[str, Any] | None:
        raw_client = record.get("client") if isinstance(record.get("client"), dict) else {}
        yc_id = _normalize_id(raw_client.get("id") or record.get("client_id"))
        phone = _normalize_id(raw_client.get("phone") or record.get("phone"))
        name = _normalize_id(raw_client.get("name") or record.get("client_name"))
        if not yc_id and not phone:
            return None
        return {"yclients_client_id": yc_id or None, "phone": phone or None, "name": name or None}

    def _client_identity(self, client_row: dict[str, Any]) -> dict[str, Any] | None:
        yc_id = _normalize_id(client_row.get("id") or client_row.get("client_id"))
        phone = _normalize_id(client_row.get("phone"))
        name = _normalize_id(client_row.get("name") or client_row.get("fullname") or client_row.get("title"))
        if not yc_id and not phone:
            return None
        return {"yclients_client_id": yc_id or None, "phone": phone or None, "name": name or None}

    def _business_client_key(self, client_row: dict[str, Any]) -> str:
        yc_id = _normalize_id(client_row.get("yclients_client_id") or client_row.get("id") or client_row.get("client_id"))
        phone = _normalize_id(client_row.get("phone"))
        if yc_id:
            return f"yc:{yc_id}"
        if phone:
            return f"phone:{phone}"
        return ""

    def _record_is_active_future_booking(self, record: dict[str, Any], now_utc: datetime) -> bool:
        if bool(record.get("deleted")):
            return False
        event_dt = self._record_datetime_utc(record)
        if not event_dt or event_dt <= now_utc:
            return False
        return record.get("attendance") in (None, 0, 2)

    async def resolve_all_clients_from_yclients(self, *, actor_tg_id: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        started = time.perf_counter()
        logger.info("all_clients_yclients_resolve_started actor_tg_id=%s company_id=%s endpoint=%s method=%s", actor_tg_id, settings.company_id, "/api/v1/clients/{company_id}", "GET")
        client, company_id = await build_yclients_client()
        rows: list[dict[str, Any]] = []
        page = 1
        count = 200
        try:
            while True:
                payload = await list_clients(client, company_id=str(company_id), page=page, count=count)
                data = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(data, list) or not data:
                    break
                rows.extend([item for item in data if isinstance(item, dict)])
                if len(data) < count:
                    break
                page += 1
        finally:
            await client.close()
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            identity = self._client_identity(row)
            if not identity:
                continue
            key = self._business_client_key(identity)
            if key:
                unique[key] = identity
        clients = list(unique.values())
        logger.info("all_clients_yclients_resolve_finished actor_tg_id=%s company_id=%s endpoint=%s method=%s clients_count=%s unique_yclients_clients_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, "/api/v1/clients/{company_id}", "GET", len(rows), len(clients), int((time.perf_counter() - started) * 1000))
        return clients, {"company_id": str(settings.company_id), "clients_count": len(rows)}

    async def resolve_active_clients_from_yclients(self, days: int = 30, *, actor_tg_id: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        tz_name = await self.branch_timezone()
        tz = self._zoneinfo(tz_name)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=days)).date().isoformat()
        date_to = now_local.date().isoformat()
        started = time.perf_counter()
        logger.info("active_clients_yclients_resolve_started actor_tg_id=%s company_id=%s days=%s endpoint=%s method=%s date_from=%s date_to=%s", actor_tg_id, settings.company_id, days, "/api/v1/records/{company_id}", "GET", date_from, date_to)
        client, company_id = await build_yclients_client()
        records: list[dict[str, Any]] = []
        page = 1
        count = 200
        try:
            while True:
                payload = await list_bookings_by_date_range(client, company_id=str(company_id), date_from=date_from, date_to=date_to, page=page, count=count)
                data = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(data, list) or not data:
                    break
                records.extend([r for r in data if isinstance(r, dict)])
                if len(data) < count:
                    break
                page += 1
        finally:
            await client.close()
        now_utc = now_local.astimezone(timezone.utc)
        unique: dict[str, dict[str, Any]] = {}
        for row in records:
            if bool(row.get("deleted")):
                continue
            event_dt = self._record_datetime_utc(row)
            if event_dt and event_dt > now_utc:
                continue
            identity = self._record_client_identity(row)
            if not identity:
                continue
            key = self._business_client_key(identity)
            if key:
                unique[key] = identity
        clients = list(unique.values())
        logger.info("active_clients_yclients_resolve_finished actor_tg_id=%s company_id=%s days=%s endpoint=%s method=%s date_from=%s date_to=%s records_count=%s unique_yclients_clients_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, days, "/api/v1/records/{company_id}", "GET", date_from, date_to, len(records), len(clients), int((time.perf_counter() - started) * 1000))
        return clients, {"company_id": str(settings.company_id), "date_from": date_from, "date_to": date_to, "records_count": len(records)}

    async def resolve_no_future_booking_clients_from_yclients(self, *, actor_tg_id: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        tz_name = await self.branch_timezone()
        tz = self._zoneinfo(tz_name)
        now_local = datetime.now(tz)
        date_from = now_local.date().isoformat()
        date_to = (now_local + timedelta(days=365)).date().isoformat()
        started = time.perf_counter()
        all_clients, all_diag = await self.resolve_all_clients_from_yclients(actor_tg_id=actor_tg_id)
        logger.info("no_future_booking_yclients_records_started actor_tg_id=%s company_id=%s endpoint=%s method=%s date_from=%s date_to=%s", actor_tg_id, settings.company_id, "/api/v1/records/{company_id}", "GET", date_from, date_to)
        client, company_id = await build_yclients_client()
        records: list[dict[str, Any]] = []
        page = 1
        count = 200
        try:
            while True:
                payload = await list_bookings_by_date_range(client, company_id=str(company_id), date_from=date_from, date_to=date_to, page=page, count=count)
                data = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(data, list) or not data:
                    break
                records.extend([r for r in data if isinstance(r, dict)])
                if len(data) < count:
                    break
                page += 1
        finally:
            await client.close()
        now_utc = now_local.astimezone(timezone.utc)
        future_keys: set[str] = set()
        for row in records:
            if not self._record_is_active_future_booking(row, now_utc):
                continue
            identity = self._record_client_identity(row)
            if not identity:
                continue
            key = self._business_client_key(identity)
            if key:
                future_keys.add(key)
        result = [c for c in all_clients if self._business_client_key(c) and self._business_client_key(c) not in future_keys]
        logger.info("no_future_booking_yclients_resolve_finished actor_tg_id=%s company_id=%s clients_count=%s future_records_count=%s excluded_future_booking_count=%s unique_yclients_clients_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, all_diag.get("clients_count"), len(records), len(future_keys), len(result), int((time.perf_counter() - started) * 1000))
        return result, {"company_id": str(settings.company_id), "date_from": date_from, "date_to": date_to, "clients_count": all_diag.get("clients_count"), "records_count": len(records), "excluded_future_booking_count": len(future_keys)}

    async def resolve_lost_clients_from_yclients(self, days: int, *, actor_tg_id: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        tz_name = await self.branch_timezone()
        tz = self._zoneinfo(tz_name)
        now_local = datetime.now(tz)
        date_to = now_local.date().isoformat()
        date_from = (now_local - timedelta(days=365)).date().isoformat()
        started = time.perf_counter()
        logger.info("lost_clients_yclients_resolve_started actor_tg_id=%s company_id=%s days=%s endpoint=%s method=%s date_from=%s date_to=%s", actor_tg_id, settings.company_id, days, "/api/v1/records/{company_id}", "GET", date_from, date_to)
        client, company_id = await build_yclients_client()
        records: list[dict[str, Any]] = []
        page = 1
        count = 200
        try:
            while True:
                payload = await list_bookings_by_date_range(client, company_id=str(company_id), date_from=date_from, date_to=date_to, page=page, count=count)
                data = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(data, list) or not data:
                    break
                records.extend([r for r in data if isinstance(r, dict)])
                if len(data) < count:
                    break
                page += 1
        finally:
            await client.close()
        status_dist: dict[str, int] = {}
        clients: dict[str, dict[str, Any]] = {}
        excluded_future = 0
        for row in records:
            attendance = row.get("attendance")
            deleted = bool(row.get("deleted"))
            status_key = f"attendance={attendance};deleted={int(deleted)}"
            status_dist[status_key] = status_dist.get(status_key, 0) + 1
            raw_client = row.get("client") if isinstance(row.get("client"), dict) else {}
            yc_id = _normalize_id(raw_client.get("id") or row.get("client_id"))
            phone = _normalize_id(raw_client.get("phone") or row.get("phone"))
            name = _normalize_id(raw_client.get("name") or row.get("client_name"))
            key = f"yc:{yc_id}" if yc_id else (f"phone:{phone}" if phone else (f"name_phone:{name}:{phone}" if name and phone else ""))
            if not key:
                continue
            event_dt = self._record_datetime_utc(row)
            state = clients.setdefault(key, {"yclients_client_id": yc_id or None, "phone": phone or None, "name": name or None, "last_completed_visit_utc": None, "has_active_future_booking": False})
            if event_dt and event_dt > now_local.astimezone(timezone.utc) and (not deleted) and attendance in (None, 0, 2):
                if not state["has_active_future_booking"]:
                    excluded_future += 1
                state["has_active_future_booking"] = True
            if deleted:
                continue
            if attendance == 1 and event_dt:
                prev = state.get("last_completed_visit_utc")
                if prev is None or event_dt > prev:
                    state["last_completed_visit_utc"] = event_dt
        threshold = now_local - timedelta(days=days)
        result = []
        for c in clients.values():
            if c.get("has_active_future_booking"):
                continue
            last_visit = c.get("last_completed_visit_utc")
            if not last_visit:
                continue
            if last_visit.astimezone(tz) <= threshold:
                result.append({k: v for k, v in c.items() if k != "last_completed_visit_utc"})
        logger.info("lost_clients_status_distribution actor_tg_id=%s company_id=%s days=%s distribution=%s", actor_tg_id, settings.company_id, days, status_dist)
        logger.info("lost_clients_future_booking_exclusion actor_tg_id=%s company_id=%s days=%s excluded_future_booking_count=%s", actor_tg_id, settings.company_id, days, excluded_future)
        logger.info("lost_clients_yclients_resolve_finished actor_tg_id=%s company_id=%s days=%s endpoint=%s method=%s date_from=%s date_to=%s records_count=%s unique_yclients_clients_count=%s excluded_future_booking_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, days, "/api/v1/records/{company_id}", "GET", date_from, date_to, len(records), len(result), excluded_future, int((time.perf_counter() - started) * 1000))
        return result, {"company_id": str(settings.company_id), "date_from": date_from, "date_to": date_to, "records_count": len(records), "excluded_future_booking_count": excluded_future}
    @staticmethod
    def _extract_services_payload(payload: Any) -> list[dict[str, Any]]:
        candidate: Any = payload
        if isinstance(payload, dict):
            for key in ("data", "services"):
                if isinstance(payload.get(key), list):
                    candidate = payload.get(key)
                    break
                if isinstance(payload.get(key), dict):
                    nested = payload.get(key)
                    if isinstance(nested, dict):
                        for nested_key in ("data", "services", "items"):
                            if isinstance(nested.get(nested_key), list):
                                candidate = nested.get(nested_key)
                                break
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("data", "items", "services"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [item for item in rows if isinstance(item, dict)]
        return []
    @staticmethod
    def _service_category_fields(service: dict[str, Any]) -> tuple[str, str]:
        category_raw = service.get("category")
        category_dict = category_raw if isinstance(category_raw, dict) else {}
        category_id = _normalize_id(
            service.get("category_id")
            or service.get("categoryId")
            or category_dict.get("id")
        )
        category_name = _normalize_id(
            service.get("category_title")
            or service.get("category_name")
            or category_dict.get("title")
            or category_dict.get("name")
            or (category_raw if isinstance(category_raw, str) else None)
        )
        return category_id, category_name

    async def fetch_master_segment_clients(
        self,
        master_id: str,
        *,
        actor_tg_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        timeout_s: int = 12,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        normalized_master_id = _normalize_id(master_id)
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        tz_name = await self.branch_timezone()
        now_local = datetime.now(ZoneInfo(tz_name))
        end_local = date_to.astimezone(ZoneInfo(tz_name)) if date_to else now_local
        start_local = date_from.astimezone(ZoneInfo(tz_name)) if date_from else (end_local - timedelta(days=365))
        date_from_str = start_local.date().isoformat()
        date_to_str = end_local.date().isoformat()
        started = time.perf_counter()
        logger.info(
            "master_segment_yclients_fetch_started actor_tg_id=%s selected_callback_master_id=%s yclients_staff_id=%s company_id=%s date_from=%s date_to=%s endpoint=%s method=%s",
            actor_tg_id, master_id, normalized_master_id, settings.company_id, date_from_str, date_to_str, "/api/v1/records/{company_id}", "GET",
        )
        client, company_id = await build_yclients_client()
        page = 1
        count = 200
        records: list[dict[str, Any]] = []
        try:
            while True:
                payload = await asyncio.wait_for(
                    list_bookings_by_date_range(
                        client,
                        company_id=str(company_id),
                        date_from=date_from_str,
                        date_to=date_to_str,
                        staff_id=normalized_master_id,
                        page=page,
                        count=count,
                    ),
                    timeout=timeout_s,
                )
                data = payload.get("data") if isinstance(payload, dict) else payload
                if not isinstance(data, list) or not data:
                    break
                records.extend([item for item in data if isinstance(item, dict)])
                if len(data) < count:
                    break
                page += 1
        finally:
            await client.close()
        logger.info(
            "master_segment_yclients_fetch_finished actor_tg_id=%s selected_callback_master_id=%s yclients_staff_id=%s company_id=%s endpoint=%s method=%s records_count=%s elapsed_ms=%s",
            actor_tg_id, master_id, normalized_master_id, company_id, "/api/v1/records/{company_id}", "GET", len(records), int((time.perf_counter() - started) * 1000),
        )
        unique: dict[str, dict[str, Any]] = {}
        for row in records:
            raw_client = row.get("client")
            client_payload = raw_client if isinstance(raw_client, dict) else {}
            yc_id = _normalize_id(client_payload.get("id") or row.get("client_id"))
            phone = _normalize_id(client_payload.get("phone") or row.get("phone"))
            name = _normalize_id(client_payload.get("name") or row.get("client_name"))
            if yc_id:
                key = f"yc:{yc_id}"
            elif phone:
                key = f"phone:{phone}"
            elif name:
                key = f"name:{name}"
            else:
                continue
            unique[key] = {"yclients_client_id": yc_id or None, "phone": phone or None, "name": name or None}
        clients = list(unique.values())
        logger.info(
            "master_segment_clients_deduplicated actor_tg_id=%s selected_callback_master_id=%s yclients_staff_id=%s company_id=%s records_count=%s unique_yclients_clients_count=%s",
            actor_tg_id, master_id, normalized_master_id, settings.company_id, len(records), len(clients),
        )
        return clients, {"company_id": str(settings.company_id), "date_from": date_from_str, "date_to": date_to_str, "records_count": len(records)}
    async def get_cached_segment_row(self, key: str) -> Any:
        return await fetchone(
            """
            SELECT client_count, calculated_at_utc, branch_timezone, error_summary
            FROM client_segment_cache
            WHERE segment_key=? AND (segment_filter_json='{}' OR segment_filter_json IS NULL)
            ORDER BY updated_at_utc DESC LIMIT 1
            """,
            (key,),
        )

    def _is_cache_stale(self, calculated_at_utc: str | None, ttl_hours: int = SEGMENT_CACHE_TTL_HOURS) -> bool:
        if not calculated_at_utc:
            return True
        try:
            ts = datetime.fromisoformat(str(calculated_at_utc).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) - ts.astimezone(timezone.utc) > timedelta(hours=ttl_hours)
        except Exception:
            return True

    async def ensure_segment_fresh(self, key: str, *, force: bool = False) -> tuple[Any, bool]:
        started = time.perf_counter()
        row = await self.get_cached_segment_row(key)
        if not force and row and not self._is_cache_stale(_row_value(row, "calculated_at_utc")):
            logger.info("segment_auto_refresh_skipped_fresh_cache segment_key=%s source_used=cache calculated_at_utc=%s", key, _row_value(row, "calculated_at_utc"))
            return row, False
        old_count = int(_row_value(row, "client_count", 0) or 0) if row else None
        logger.info("segment_auto_refresh_started segment_key=%s old_count=%s source_used=local_db", key, old_count)
        result = await self.get_segment_count(key)
        tz_name = await self.branch_timezone()
        now_utc = datetime.now(timezone.utc).isoformat()
        await self._save_cache(key, result.count, tz_name, result.error_summary if result.partial else None, now_utc)
        updated = await self.get_cached_segment_row(key)
        logger.info(
            "segment_auto_refresh_finished segment_key=%s old_count=%s new_count=%s source_used=local_db calculated_at_utc=%s elapsed_ms=%s error_summary=%s",
            key,
            old_count,
            result.count,
            now_utc,
            int((time.perf_counter() - started) * 1000),
            result.error_summary,
        )
        return updated, True

    async def branch_timezone(self) -> str:
        settings = await get_yclients_settings()
        if settings and settings.company_id:
            try:
                return (await resolve_company_timezone(settings.company_id)).timezone_name
            except Exception:
                logger.exception("segment_timezone_resolve_failed company_id=%s", settings.company_id)
        return "Europe/Moscow"

    def _zoneinfo(self, tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("segment_timezone_invalid timezone=%s", tz_name)
            return ZoneInfo("Europe/Moscow")

    async def refresh_segment_cache(self) -> dict[str, int]:
        started = time.perf_counter()
        tz_name = await self.branch_timezone()
        now_utc = datetime.now(timezone.utc).isoformat()
        counts: dict[str, int] = {}
        for key in SEGMENTS:
            result = await self.get_segment_count(key)
            counts[key] = result.count
            await self._save_cache(key, result.count, tz_name, result.error_summary if result.partial else None, now_utc)
        logger.info("segment_refresh_completed elapsed_ms=%s keys=%s", int((time.perf_counter() - started) * 1000), len(counts))
        return counts

    async def _save_cache(self, key: str, count: int, tz_name: str, error_summary: str | None, calculated_at_utc: str | None = None) -> None:
        now_utc = datetime.now(timezone.utc).isoformat()
        filter_json = "{}"
        await execute(
            """
            INSERT INTO client_segment_cache (segment_key, segment_filter_json, client_count, calculated_at_utc, branch_timezone, error_summary, created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(segment_key, segment_filter_json) DO UPDATE SET
            client_count=excluded.client_count,
            calculated_at_utc=excluded.calculated_at_utc,
            branch_timezone=excluded.branch_timezone,
            error_summary=excluded.error_summary,
            updated_at_utc=excluded.updated_at_utc
            """,
            (key, filter_json, max(0, int(count or 0)), calculated_at_utc or now_utc, tz_name, error_summary, now_utc, now_utc),
        )

    async def get_segment_summary(self, key: str) -> SegmentSummary:
        if key not in SEGMENTS:
            raise KeyError(key)
        started = time.perf_counter()
        tz_name = await self.branch_timezone()
        warning = None
        try:
            row, _ = await self.ensure_segment_fresh(key)
        except Exception as exc:
            logger.exception("segment_auto_refresh_failed segment_key=%s error_summary=%s", key, str(exc)[:200])
            row = await self.get_cached_segment_row(key)
            warning = PARTIAL_DATA_WARNING
        if row:
            auto_refresh_failed = False
            count = int(_row_value(row, "client_count", 0) or 0)
            updated_local = self._fmt_local(_row_value(row, "calculated_at_utc"), _row_value(row, "branch_timezone") or tz_name)
            if _row_value(row, "error_summary"):
                warning = PARTIAL_DATA_WARNING
                auto_refresh_failed = True
        else:
            raise RuntimeError("segment_cache_missing_after_refresh")
        logger.info("segment_summary_loaded segment_key=%s count=%s elapsed_ms=%s", key, count, int((time.perf_counter() - started) * 1000))
        return SegmentSummary(key, SEGMENTS[key], DESCRIPTIONS[key], count, updated_local, warning, auto_refresh_failed=auto_refresh_failed)

    async def get_segment_count(self, key: str, *, master_id: str | None = None, service_id: str | None = None) -> SegmentCountResult:
        try:
            return SegmentCountResult(await self.count_segment_clients(key, master_id=master_id, service_id=service_id), partial=self._is_partial_by_design(key))
        except Exception as exc:
            if key == "cancelled_30":
                logger.exception(
                    "cancelled_recent_segment_resolve_failed segment_key=%s source_table=%s error_summary=%s",
                    key,
                    "cancellation_recovery_events",
                    str(exc)[:200],
                )
            logger.exception("segment_summary_failed segment_key=%s error=%s", key, str(exc)[:200])
            return SegmentCountResult(0, partial=True, error_summary=str(exc)[:200])

    def _is_partial_by_design(self, key: str) -> bool:
        return key in {"active_30", "inactive_30", "inactive_60", "inactive_90", "no_future_booking", "cancelled_30", "bad_rating", "birthday_soon", "by_master", "by_service"}

    def _fmt_local(self, utc_iso: str | None, tz_name: str) -> str | None:
        if not utc_iso:
            return None
        try:
            dt = datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(self._zoneinfo(tz_name)).strftime("%d.%m.%Y в %H:%M")
        except Exception:
            logger.exception("segment_updated_format_failed value=%s timezone=%s", utc_iso, tz_name)
            return None

    async def count_segment_clients(self, key: str, *, master_id: str | None = None, service_id: str | None = None) -> int:
        tz_name = await self.branch_timezone()
        tz = self._zoneinfo(tz_name)
        now_local = datetime.now(tz)
        if key == "all_clients":
            clients, _ = await self.resolve_all_clients_from_yclients()
            return len(clients)
        elif key == "active_30":
            clients, _ = await self.resolve_active_clients_from_yclients(30)
            return len(clients)
        elif key in {"inactive_30", "inactive_60", "inactive_90"}:
            days = int(key.split("_")[-1])
            clients, _ = await self.resolve_lost_clients_from_yclients(days)
            return len(clients)
        elif key == "no_future_booking":
            clients, _ = await self.resolve_no_future_booking_clients_from_yclients()
            return len(clients)
        elif key == "cancelled_30":
            cutoff = (now_local - timedelta(days=30)).astimezone(timezone.utc).isoformat()
            logger.info(
                "cancelled_recent_segment_resolve_started segment_key=%s source_table=%s cutoff_utc=%s",
                key,
                "cancellation_recovery_events",
                cutoff,
            )
            row = await fetchone(
                "SELECT COUNT(DISTINCT client_tg_id) AS c FROM cancellation_recovery_events WHERE client_tg_id IS NOT NULL AND cancellation_detected_at_utc>=? AND COALESCE(is_test,0)=0",
                (cutoff,),
            )
            logger.info(
                "cancelled_recent_segment_resolve_finished segment_key=%s source_table=%s segment_count=%s cutoff_utc=%s",
                key,
                "cancellation_recovery_events",
                int(_row_value(row, "c", 0) or 0),
                cutoff,
            )
        elif key == "bad_rating":
            row = await fetchone("SELECT COUNT(DISTINCT client_tg_id) AS c FROM post_visit_feedback_events WHERE client_tg_id IS NOT NULL AND rating BETWEEN 1 AND 3 AND COALESCE(is_test,0)=0")
        elif key == "birthday_soon":
            return await self._count_birthdays_soon(now_local.date())
        elif key == "by_master" and master_id:
            clients, _ = await self.fetch_master_segment_clients(master_id)
            return len(clients)
        elif key == "by_service" and service_id:
            row = await fetchone("SELECT COUNT(DISTINCT client_tg_id) AS c FROM post_visit_feedback_events WHERE client_tg_id IS NOT NULL AND service_id=? AND COALESCE(is_test,0)=0", (str(service_id),))
        else:
            return 0
        return max(0, int(_row_value(row, "c", 0) or 0))

    async def _count_birthdays_soon(self, today: date) -> int:
        clients, _ = await self.resolve_birthday_soon_clients(today=today)
        return len(clients)

    def _is_birthday_soon(self, parsed: date, today: date, end: date) -> bool:
        try:
            next_birthday = parsed.replace(year=today.year)
        except ValueError:
            next_birthday = date(today.year, 2, 28)
        if next_birthday < today:
            try:
                next_birthday = parsed.replace(year=today.year + 1)
            except ValueError:
                next_birthday = date(today.year + 1, 2, 28)
        return today <= next_birthday <= end

    async def resolve_birthday_soon_clients(self, *, today: date | None = None, actor_tg_id: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        tz_name = await self.branch_timezone()
        tz = self._zoneinfo(tz_name)
        today = today or datetime.now(tz).date()
        end = today + timedelta(days=7)
        date_from = today.isoformat()
        date_to = end.isoformat()
        logger.info("birthday_segment_resolve_started actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s", actor_tg_id, None, tz_name, date_from, date_to)
        yclients_checked = 0
        local_checked = 0
        merged: dict[str, dict[str, Any]] = {}
        logger.info("birthday_segment_yclients_clients_fetched actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s", actor_tg_id, None, tz_name, date_from, date_to, yclients_checked)
        try:
            client, company_id = await build_yclients_client()
            page = 1
            per_page = 200
            try:
                while True:
                    payload = await search_clients(client, company_id=str(company_id), query="", page=page, count=per_page)
                    rows = self._extract_rows(payload)
                    if not rows:
                        break
                    for row in rows:
                        yclients_checked += 1
                        parsed = self._parse_birth_date(str(row.get("birth_date") or row.get("bdate") or "").strip())
                        if not parsed or not self._is_birthday_soon(parsed, today, end):
                            continue
                        yc_id = _normalize_id(row.get("id"))
                        phone = _normalize_id(row.get("phone"))
                        key = f"yc:{yc_id}" if yc_id else (f"phone:{phone}" if phone else "")
                        if not key:
                            continue
                        merged[key] = {"yclients_client_id": yc_id or None, "phone": phone or None, "tg_id": None, "local_user_id": None}
                    if len(rows) < per_page:
                        break
                    page += 1
            finally:
                await client.close()
            logger.info("birthday_segment_yclients_clients_fetched actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s", actor_tg_id, company_id, tz_name, date_from, date_to, yclients_checked)
        except Exception as exc:
            logger.exception("birthday_segment_yclients_clients_fetched actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s exception_type=%s exception_message=%s", actor_tg_id, None, tz_name, date_from, date_to, yclients_checked, type(exc).__name__, str(exc)[:200])

        users = await fetchall("SELECT user_id, yclients_client_id, phone, birth_date FROM users WHERE user_id IS NOT NULL AND COALESCE(is_registered,0)=1 AND birth_date IS NOT NULL AND birth_date!=''")
        for user in users:
            local_checked += 1
            parsed = self._parse_birth_date(str(_row_value(user, "birth_date", "") or "").strip())
            if not parsed or not self._is_birthday_soon(parsed, today, end):
                continue
            yc_id = _normalize_id(_row_value(user, "yclients_client_id"))
            phone = _normalize_id(_row_value(user, "phone"))
            tg_id = _normalize_id(_row_value(user, "user_id"))
            key = f"yc:{yc_id}" if yc_id else (f"phone:{phone}" if phone else (f"tg:{tg_id}" if tg_id else ""))
            if not key:
                continue
            if key not in merged:
                merged[key] = {"yclients_client_id": yc_id or None, "phone": phone or None, "tg_id": int(tg_id) if tg_id else None, "local_user_id": int(tg_id) if tg_id else None}
            else:
                merged[key]["tg_id"] = merged[key].get("tg_id") or (int(tg_id) if tg_id else None)
                merged[key]["local_user_id"] = merged[key].get("local_user_id") or (int(tg_id) if tg_id else None)
        logger.info("birthday_segment_local_fallback_checked actor_tg_id=%s company_id=%s local_clients_checked=%s", actor_tg_id, None, local_checked)
        logger.info("birthday_segment_birthdays_matched actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s birthday_clients_matched=%s", actor_tg_id, None, tz_name, date_from, date_to, len(merged))
        logger.info("birthday_segment_clients_deduplicated actor_tg_id=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s local_clients_checked=%s unique_birthday_clients_count=%s", actor_tg_id, None, tz_name, date_from, date_to, yclients_checked, local_checked, len(merged))
        return list(merged.values()), {"date_from": date_from, "date_to": date_to, "branch_timezone": tz_name, "yclients_clients_checked": yclients_checked, "local_clients_checked": local_checked}

    def _parse_birth_date(self, value: str) -> date | None:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                if fmt == "%d.%m":
                    parsed = parsed.replace(year=2000)
                return parsed
            except ValueError:
                continue
        return None

    async def get_master_segment_summary(self, master_id: str) -> NamedSegmentResult:
        name = await self.resolve_master_name(master_id)
        result = await self.get_segment_count("by_master", master_id=master_id)
        return NamedSegmentResult(
            title=f"💈 Клиенты мастера: {name or master_id}",
            description="Клиенты, которые посещали выбранного мастера.",
            count=result.count,
            updated_local=self._fmt_local(datetime.now(timezone.utc).isoformat(), await self.branch_timezone()) or "—",
            warning=PARTIAL_DATA_WARNING if result.partial else None,
        )

    async def get_service_segment_summary(self, service_id: str) -> NamedSegmentResult:
        name = await self.resolve_service_name(service_id)
        result = await self.get_segment_count("by_service", service_id=service_id)
        return NamedSegmentResult(
            title=f"✂️ Клиенты услуги: {name or service_id}",
            description="Клиенты, которые пользовались выбранной услугой.",
            count=result.count,
            updated_local=self._fmt_local(datetime.now(timezone.utc).isoformat(), await self.branch_timezone()) or "—",
            warning=PARTIAL_DATA_WARNING if result.partial else None,
        )

    async def list_service_categories(self, *, actor_tg_id: int | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        started = time.perf_counter()
        logger.info("service_category_yclients_fetch_started actor_tg_id=%s company_id=%s endpoint=%s method=%s", actor_tg_id, settings.company_id, "/api/v1/service_categories/{company_id}", "GET")
        client, company_id = await build_yclients_client()
        categories: list[dict[str, str]] = []
        try:
            payload = await asyncio.wait_for(list_service_categories(client, company_id=str(company_id)), timeout=YCLIENTS_PICKER_TIMEOUT_S)
            rows = self._extract_rows(payload)
            dedup: dict[str, str] = {}
            for item in rows:
                category_id = _normalize_id(item.get("id"))
                category_name = _normalize_id(item.get("title") or item.get("name"))
                if not category_id or not category_name:
                    continue
                dedup[category_id] = category_name
            categories = [{"id": _safe_service_category_callback_id(cid, name), "name": name} for cid, name in dedup.items()]
            categories.sort(key=lambda row: row["name"].casefold())
            if categories:
                logger.info("service_category_yclients_fetch_finished actor_tg_id=%s company_id=%s endpoint=%s method=%s raw_services_count=%s unique_categories_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, "/api/v1/service_categories/{company_id}", "GET", 0, len(categories), int((time.perf_counter()-started)*1000))
                return categories, {"company_id":str(settings.company_id),"raw_services_count":0,"unique_categories_count":len(categories)}
            logger.info("service_category_direct_empty_fallback actor_tg_id=%s company_id=%s", actor_tg_id, settings.company_id)
        except Exception as exc:
            logger.info("service_category_direct_failed_fallback actor_tg_id=%s company_id=%s error=%s", actor_tg_id, settings.company_id, type(exc).__name__)
            payload = None

        try:
            payload = await asyncio.wait_for(get_services(client, company_id=str(company_id)), timeout=YCLIENTS_PICKER_TIMEOUT_S)
        finally:
            await client.close()
        services = self._extract_services_payload(payload)
        payload_type = type(payload).__name__
        payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        first_service_keys = sorted(services[0].keys()) if services else []
        first_category_raw = services[0].get("category") if services else None
        first_category_type = type(first_category_raw).__name__ if services else "none"
        first_category_keys = sorted(first_category_raw.keys()) if isinstance(first_category_raw, dict) else []
        logger.info(
            "service_category_yclients_payload_diag actor_tg_id=%s company_id=%s endpoint=%s response_top_type=%s response_top_keys=%s services_count=%s first_service_keys=%s first_category_type=%s first_category_keys=%s",
            actor_tg_id, settings.company_id, "/api/v1/company/{company_id}/services", payload_type, payload_keys, len(services), first_service_keys, first_category_type, first_category_keys,
        )
        categories = []
        seen_ids=set(); seen_names=set(); has_uncat=False
        for srv in services:
            cid, cname = self._service_category_fields(srv)
            norm_name=cname.casefold().strip()
            label = cname or (f"Категория {cid}" if cid else "")
            logger.info("service_category_label_resolved actor_tg_id=%s company_id=%s category_id=%s category_name=%s", actor_tg_id, settings.company_id, cid, label)
            if cid:
                if cid in seen_ids: continue
                seen_ids.add(cid)
                categories.append({"id":_safe_service_category_callback_id(cid, label),"name":label})
                continue
            if norm_name:
                if norm_name in seen_names: continue
                seen_names.add(norm_name)
                categories.append({"id":_safe_service_category_callback_id("", cname),"name":cname})
            else:
                has_uncat=True
        if has_uncat:
            categories.append({"id":"uncategorized","name":"Без категории"})
        logger.info("service_category_buttons_built actor_tg_id=%s company_id=%s raw_services_count=%s unique_categories_count=%s",actor_tg_id,settings.company_id,len(services),len(categories))
        logger.info("service_category_yclients_fetch_finished actor_tg_id=%s company_id=%s endpoint=%s method=%s raw_services_count=%s unique_categories_count=%s elapsed_ms=%s", actor_tg_id, settings.company_id, "/api/v1/company/{company_id}/services", "GET", len(services), len(categories), int((time.perf_counter()-started)*1000))
        return categories, {"company_id":str(settings.company_id),"raw_services_count":len(services),"unique_categories_count":len(categories)}

    async def resolve_master_name(self, master_id: str) -> str | None:
        for mid, name in await self.list_masters():
            if str(mid) == str(master_id):
                return name
        row = await fetchone("SELECT staff_name FROM post_visit_feedback_events WHERE staff_id=? AND staff_name IS NOT NULL AND staff_name!='' ORDER BY updated_at_utc DESC LIMIT 1", (str(master_id),))
        return _row_value(row, "staff_name")

    async def resolve_service_name(self, service_id: str) -> str | None:
        for sid, name in await self.list_services():
            if str(sid) == str(service_id):
                return name
        row = await fetchone("SELECT service_name FROM post_visit_feedback_events WHERE service_id=? AND service_name IS NOT NULL AND service_name!='' ORDER BY updated_at_utc DESC LIMIT 1", (str(service_id),))
        return _row_value(row, "service_name")

    async def list_masters(self) -> list[tuple[str, str]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            return await self._list_local_masters()
        client, company_id = await build_yclients_client()
        try:
            payload = await asyncio.wait_for(get_staff(client, company_id=company_id), timeout=YCLIENTS_PICKER_TIMEOUT_S)
        except (YClientsError, asyncio.TimeoutError) as exc:
            logger.warning("master_picker_yclients_failed company_id=%s error=%s", settings.company_id, type(exc).__name__)
            return await self._list_local_masters()
        finally:
            await client.close()
        masters = self._extract_named_items(payload, name_fields=("name", "fullname", "title", "specialization"))
        return masters or await self._list_local_masters()

    async def resolve_master_debug_info(self, callback_master_id: str) -> dict[str, Any]:
        normalized = _normalize_id(callback_master_id)
        settings = await get_yclients_settings()
        company_id = str(settings.company_id) if settings and settings.company_id else None
        info: dict[str, Any] = {
            "selected_callback_master_id": normalized,
            "local_master_id": None,
            "yclients_staff_id": normalized,
            "master_name": await self.resolve_master_name(normalized),
            "company_id": company_id,
            "is_active": None,
            "is_deleted": None,
            "is_fired": None,
        }
        try:
            client, resolved_company_id = await build_yclients_client()
            info["company_id"] = str(resolved_company_id or info["company_id"] or "")
            try:
                payload = await asyncio.wait_for(get_staff(client, company_id=resolved_company_id), timeout=YCLIENTS_PICKER_TIMEOUT_S)
                data = payload.get("data") if isinstance(payload, dict) else payload
                for item in (data or []):
                    if str(item.get("id")) == normalized:
                        info["master_name"] = str(item.get("name") or item.get("fullname") or info["master_name"] or normalized)
                        info["is_active"] = item.get("is_active")
                        info["is_deleted"] = item.get("is_deleted")
                        info["is_fired"] = item.get("is_fired")
                        break
            finally:
                await client.close()
        except Exception:
            logger.exception("master_segment_debug_info_resolve_failed selected_callback_master_id=%s", normalized)
        return info

    async def list_services(self) -> list[tuple[str, str]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            return await self._list_local_services()
        client, company_id = await build_yclients_client()
        try:
            payload = await asyncio.wait_for(get_services(client, company_id=company_id), timeout=YCLIENTS_PICKER_TIMEOUT_S)
        except (YClientsError, asyncio.TimeoutError) as exc:
            logger.warning("service_picker_yclients_failed company_id=%s error=%s", settings.company_id, type(exc).__name__)
            return await self._list_local_services()
        finally:
            await client.close()
        services = self._extract_named_items(payload, name_fields=("title", "name"))
        return services or await self._list_local_services()


    async def fetch_service_category_segment_clients(self, category_id: str, category_name: str, *, actor_tg_id: int | None = None, date_from: datetime | None = None, date_to: datetime | None = None, timeout_s: int = 12) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        settings = await get_yclients_settings()
        if not settings or not settings.company_id:
            raise RuntimeError("yclients_settings_missing")
        client, company_id = await build_yclients_client()
        try:
            payload = await asyncio.wait_for(get_services(client, company_id=str(company_id)), timeout=YCLIENTS_PICKER_TIMEOUT_S)
            services = self._extract_services_payload(payload)
            service_ids=[]
            for srv in services:
                sid=_normalize_id(srv.get("id"))
                if not sid: continue
                cid, cname = self._service_category_fields(srv)
                if _service_category_callback_id_matches(category_id, cid, cname):
                    service_ids.append(sid)
            tz_name=await self.branch_timezone(); now_local=datetime.now(ZoneInfo(tz_name)); end_local=date_to.astimezone(ZoneInfo(tz_name)) if date_to else now_local; start_local=date_from.astimezone(ZoneInfo(tz_name)) if date_from else (end_local-timedelta(days=365))
            dfrom=start_local.date().isoformat(); dto=end_local.date().isoformat()
            logger.info("service_category_segment_yclients_records_fetch_started actor_tg_id=%s company_id=%s category_id=%s category_name=%s service_ids=%s date_from=%s date_to=%s endpoint=%s method=%s",actor_tg_id,company_id,category_id,category_name,service_ids,dfrom,dto,"/api/v1/records/{company_id}","GET")
            page=1; count=200; records=[]
            while True:
                rec=await asyncio.wait_for(list_bookings_by_date_range(client,company_id=str(company_id),date_from=dfrom,date_to=dto,page=page,count=count),timeout=timeout_s)
                rows=rec.get("data") if isinstance(rec,dict) else rec
                if not isinstance(rows,list) or not rows: break
                for r in rows:
                    if not isinstance(r,dict): continue
                    rec_services=r.get("services") if isinstance(r.get("services"),list) else []
                    rec_service_ids={_normalize_id(x.get("id") if isinstance(x,dict) else x) for x in rec_services}
                    if rec_service_ids.intersection(set(service_ids)):
                        records.append(r)
                if len(rows)<count: break
                page+=1
            unique={}
            for row in records:
                c=row.get("client") if isinstance(row.get("client"),dict) else {}
                yc=_normalize_id(c.get("id") or row.get("client_id")); ph=_normalize_id(c.get("phone") or row.get("phone")); nm=_normalize_id(c.get("name") or row.get("client_name"))
                key=f"yc:{yc}" if yc else (f"phone:{ph}" if ph else (f"name_phone:{nm}:{ph}" if nm and ph else ""))
                if key: unique[key]={"yclients_client_id": yc or None, "phone": ph or None, "name": nm or None}
            clients=list(unique.values())
            logger.info("service_category_segment_yclients_records_fetch_finished actor_tg_id=%s company_id=%s category_id=%s category_name=%s service_ids=%s records_count=%s",actor_tg_id,company_id,category_id,category_name,service_ids,len(records))
            logger.info("service_category_clients_deduplicated actor_tg_id=%s company_id=%s category_id=%s category_name=%s records_count=%s unique_yclients_clients_count=%s",actor_tg_id,company_id,category_id,category_name,len(records),len(clients))
            return clients,{"company_id":str(company_id),"service_ids":service_ids,"records_count":len(records),"date_from":dfrom,"date_to":dto}
        finally:
            await client.close()

    async def _list_local_masters(self) -> list[tuple[str, str]]:
        try:
            rows = await fetchall("SELECT staff_id, MAX(staff_name) AS staff_name FROM post_visit_feedback_events WHERE staff_id IS NOT NULL AND staff_id!='' GROUP BY staff_id ORDER BY staff_name LIMIT 50")
            return [(str(_row_value(row, "staff_id")), str(_row_value(row, "staff_name") or _row_value(row, "staff_id"))) for row in rows]
        except Exception:
            logger.exception("master_picker_local_failed")
            return []

    async def _list_local_services(self) -> list[tuple[str, str]]:
        try:
            rows = await fetchall("SELECT service_id, MAX(service_name) AS service_name FROM post_visit_feedback_events WHERE service_id IS NOT NULL AND service_id!='' GROUP BY service_id ORDER BY service_name LIMIT 50")
            return [(str(_row_value(row, "service_id")), str(_row_value(row, "service_name") or _row_value(row, "service_id"))) for row in rows]
        except Exception:
            logger.exception("service_picker_local_failed")
            return []

    def _extract_named_items(self, payload: Any, *, name_fields: tuple[str, ...]) -> list[tuple[str, str]]:
        data = payload.get("data") if isinstance(payload, dict) else payload
        items: list[tuple[str, str]] = []
        seen: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                item_id = _normalize_id(value.get("id"))
                name = next((str(value.get(field)).strip() for field in name_fields if value.get(field)), "")
                if item_id and name and item_id not in seen:
                    seen.add(item_id)
                    items.append((item_id, name))
                for child_key in ("services", "items", "children"):
                    if child_key in value:
                        walk(value[child_key])
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(data)
        return items[:50]


segment_service = ClientSegmentService()
