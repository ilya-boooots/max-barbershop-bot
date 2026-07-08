"""Client segment calculation from YClients data for the MAX bot."""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from os import getenv
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.endpoints import get_service_categories, get_services, get_staff, list_bookings_by_date_range, list_clients, search_clients
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings, YClientsSettingsRepository
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

SEGMENT_LIST_LIMIT = 20
LOST_CLIENTS_DAYS = 30
YCLIENTS_PAGE_SIZE = 200
LOOKBACK_DAYS = 365
FUTURE_LOOKAHEAD_DAYS = 365


class ClientSegmentType(StrEnum):
    """Supported client segments."""

    ALL_CLIENTS = "all_clients"
    ACTIVE_7 = "active_7"
    ACTIVE_30 = "active_30"
    ACTIVE_90 = "active_90"
    LOST = "lost"
    LOST_30 = "lost_30"
    LOST_60 = "lost_60"
    LOST_90 = "lost_90"
    CANCELLED = "cancelled"
    BY_MASTER = "by_master"
    BY_SERVICE = "by_service"
    BIRTHDAY_SOON = "birthday_soon"
    NO_FUTURE_BOOKINGS = "no_future_bookings"


@dataclass(frozen=True)
class ClientSegmentMember:
    """One YClients client included in a segment."""

    yclients_client_id: str | None
    name: str | None
    phone: str | None
    last_visit_at: str | None = None
    future_booking_at: str | None = None
    visits_count: int = 0
    source: str = "yclients"


@dataclass(frozen=True)
class ClientSegmentResult:
    """Calculated segment with safe display metadata."""

    segment_type: str
    title: str
    members: list[ClientSegmentMember] = field(default_factory=list)
    description: str = ""
    branch_timezone: str = DEFAULT_BRANCH_TIMEZONE
    calculated_at: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.members)


class ClientSegmentsNotConfiguredError(RuntimeError):
    """YClients settings are absent or incomplete."""


class ClientSegmentsLoadError(RuntimeError):
    """YClients segment data could not be loaded safely."""


@dataclass(frozen=True)
class ClientSegmentsOverview:
    """YClients-based counts for the broadcast segments menu."""

    all_count: int
    active_30_count: int
    lost_30_count: int = 0
    lost_60_count: int = 0
    lost_90_count: int = 0
    no_future_booking_count: int = 0
    branch_timezone: str = DEFAULT_BRANCH_TIMEZONE
    diagnostics: dict[str, Any] = field(default_factory=dict)


SEGMENT_TITLES = {
    ClientSegmentType.ALL_CLIENTS: "👥 Все клиенты",
    ClientSegmentType.ACTIVE_7: "🔥 Активные 7 дней",
    ClientSegmentType.ACTIVE_30: "🔥 Активные за 30 дней",
    ClientSegmentType.ACTIVE_90: "🗓 Активные 90 дней",
    ClientSegmentType.LOST: "😔 Потерянные клиенты",
    ClientSegmentType.LOST_30: "😴 Не были 30 дней",
    ClientSegmentType.LOST_60: "😴 Не были 60 дней",
    ClientSegmentType.LOST_90: "😴 Не были 90 дней",
    ClientSegmentType.CANCELLED: "❌ Отменили запись",
    ClientSegmentType.BY_MASTER: "💈 По мастеру",
    ClientSegmentType.BY_SERVICE: "✂️ По услуге",
    ClientSegmentType.BIRTHDAY_SOON: "🎂 День рождения скоро",
    ClientSegmentType.NO_FUTURE_BOOKINGS: "📅 Без будущей записи",
}

SEGMENT_DESCRIPTIONS = {
    ClientSegmentType.ALL_CLIENTS: "Все клиенты, которых бот может идентифицировать и которым потенциально можно отправлять уведомления.",
    ClientSegmentType.ACTIVE_7: "Клиенты, которые были активны за последние 7 дней.",
    ClientSegmentType.ACTIVE_30: "Клиенты, которые были активны за последние 30 дней.",
    ClientSegmentType.ACTIVE_90: "Клиенты, которые были активны за последние 90 дней.",
    ClientSegmentType.LOST: "Клиенты с последним визитом 30+ дней назад и без будущей записи.",
    ClientSegmentType.LOST_30: "Клиенты, которые не были 30 дней и не имеют будущей записи.",
    ClientSegmentType.LOST_60: "Клиенты, которые не были 60 дней и не имеют будущей записи.",
    ClientSegmentType.LOST_90: "Клиенты, которые не были 90 дней и не имеют будущей записи.",
    ClientSegmentType.CANCELLED: "Клиенты, которые отменяли запись и могут вернуться через мягкое напоминание.",
    ClientSegmentType.BY_MASTER: "Выбор клиентов по мастеру из истории YClients.",
    ClientSegmentType.BY_SERVICE: "Выбор клиентов по услуге из истории YClients.",
    ClientSegmentType.BIRTHDAY_SOON: "Клиенты, у которых скоро день рождения.",
    ClientSegmentType.NO_FUTURE_BOOKINGS: "Клиенты, у которых сейчас нет будущей записи.",
}


class ClientSegmentService:
    """Calculate client segments from YClients records and clients only."""

    def __init__(self, settings_repository: YClientsSettingsRepository, database_path: str | None = None) -> None:
        self._settings_repository = settings_repository
        self._database_path = database_path or getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH

    async def get_core_segments_overview(self) -> ClientSegmentsOverview:
        """Return all/active 7/30/90 counts from YClients with one clients fetch and one 90-day records fetch."""

        settings = self._require_settings()
        started = time.perf_counter()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=90)).date().isoformat()
        date_to = (now_local + timedelta(days=90)).date().isoformat()
        try:
            client_rows = await self._fetch_clients(settings)
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=overview error_class=%s", type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        client_members = _dedupe_client_rows(client_rows)
        active_members, active_diagnostics = _active_keys_by_window(records, now_local, allowed_keys=set(client_members))
        diagnostics = {
            "yclients_clients_count": len(client_rows),
            "raw_records_count": len(records),
            "yclients_records_count": len(records),
            "all_count": len(client_members),
            "active_7_count": len(active_members[7]),
            "active_30_count": len(active_members[30]),
            "active_90_count": len(active_members[90]),
            "deduped_count": len(client_members),
            "branch_timezone": settings.branch_timezone,
            "date_range_from": date_from,
            "date_range_to": date_to,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            **active_diagnostics,
        }
        logger.info(
            "MAX broadcast parity diagnostic: segment=overview yclients_clients_count=%s raw_records_count=%s records_with_client_identity_count=%s records_with_datetime_count=%s records_with_created_at_count=%s skipped_cancelled_count=%s skipped_deleted_count=%s skipped_no_identity_count=%s skipped_no_date_count=%s active_7_count=%s active_30_count=%s active_90_count=%s branch_timezone=%s date_range_from=%s date_range_to=%s sample_record_statuses=%s sample_date_fields_present=%s duration_ms=%s",
            diagnostics["yclients_clients_count"], diagnostics["raw_records_count"], diagnostics["records_with_client_identity_count"], diagnostics["records_with_datetime_count"], diagnostics["records_with_created_at_count"], diagnostics["skipped_cancelled_count"], diagnostics["skipped_deleted_count"], diagnostics["skipped_no_identity_count"], diagnostics["skipped_no_date_count"], diagnostics["active_7_count"], diagnostics["active_30_count"], diagnostics["active_90_count"], diagnostics["branch_timezone"], diagnostics["date_range_from"], diagnostics["date_range_to"], diagnostics["sample_record_statuses"], diagnostics["sample_date_fields_present"], diagnostics["duration_ms"],
        )
        return ClientSegmentsOverview(
            all_count=len(client_members),
            active_30_count=len(active_members[30]),
            branch_timezone=settings.branch_timezone,
            diagnostics=diagnostics,
        )

    async def get_all_clients(self) -> ClientSegmentResult:
        settings = self._require_settings()
        started = time.perf_counter()
        try:
            client_rows = await self._fetch_clients(settings)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", ClientSegmentType.ALL_CLIENTS.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        members: dict[str, _MemberAccumulator] = {}
        for client_row in client_rows:
            if _client_is_deleted_or_archived(client_row):
                continue
            identity = _client_identity(client_row)
            key = _business_client_key(identity)
            if key:
                members[key] = _MemberAccumulator.from_identity(identity)

        result = self._build_result(ClientSegmentType.ALL_CLIENTS, members.values(), settings.branch_timezone, {"clients_count": len(client_rows), "duration_ms": int((time.perf_counter() - started) * 1000)})
        logger.info("client_segment_loaded segment_type=%s segment_count=%s clients_count=%s", ClientSegmentType.ALL_CLIENTS.value, result.count, len(client_rows))
        return result

    async def get_active_clients(self, days: int) -> ClientSegmentResult:
        if days not in {7, 30, 90}:
            raise ValueError("days must be one of 7, 30 or 90")
        segment_type = ClientSegmentType(f"active_{days}")
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=days)).date().isoformat()
        date_to = now_local.date().isoformat()
        started = time.perf_counter()
        try:
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", segment_type.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        now_utc = now_local.astimezone(timezone.utc)
        members: dict[str, _MemberAccumulator] = {}
        for record in records:
            if _record_is_deleted(record):
                continue
            event_dt = _record_datetime_utc(record)
            if event_dt and event_dt > now_utc:
                continue
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            accumulator = members.setdefault(key, _MemberAccumulator.from_identity(identity))
            accumulator.add_visit(event_dt)

        diagnostics = {
            "date_from": date_from,
            "date_to": date_to,
            "date_range_from": date_from,
            "date_range_to": date_to,
            "records_count": len(records),
            "raw_records_count": len(records),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        result = self._build_result(segment_type, members.values(), settings.branch_timezone, diagnostics)
        logger.info("client_segment_loaded segment_type=%s segment_count=%s records_count=%s", segment_type.value, result.count, len(records))
        return result

    async def get_lost_clients(self, days: int = LOST_CLIENTS_DAYS) -> ClientSegmentResult:
        if days not in {30, 60, 90}:
            days = LOST_CLIENTS_DAYS
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
        date_to = now_local.date().isoformat()
        try:
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", ClientSegmentType.LOST.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        now_utc = now_local.astimezone(timezone.utc)
        threshold = now_local - timedelta(days=days)
        accumulators: dict[str, _MemberAccumulator] = {}
        for record in records:
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            accumulator = accumulators.setdefault(key, _MemberAccumulator.from_identity(identity))
            event_dt = _record_datetime_utc(record)
            if _is_active_future_booking(record, now_utc):
                accumulator.add_future_booking(event_dt)
            elif _is_valid_past_visit(record, now_utc):
                accumulator.add_visit(event_dt)

        lost = [item for item in accumulators.values() if item.last_visit_at and not item.future_booking_at and _parse_datetime(item.last_visit_at).astimezone(tz) <= threshold]
        result = self._build_result(ClientSegmentType(f"lost_{days}"), lost, settings.branch_timezone, {"date_from": date_from, "date_to": date_to, "records_count": len(records), "lost_days": days})
        logger.info("client_segment_loaded segment_type=%s segment_count=%s records_count=%s", ClientSegmentType.LOST.value, result.count, len(records))
        return result

    async def get_clients_without_future_bookings(self) -> ClientSegmentResult:
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = now_local.date().isoformat()
        date_to = (now_local + timedelta(days=FUTURE_LOOKAHEAD_DAYS)).date().isoformat()
        try:
            all_clients = await self._fetch_clients(settings)
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", ClientSegmentType.NO_FUTURE_BOOKINGS.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        now_utc = now_local.astimezone(timezone.utc)
        members: dict[str, _MemberAccumulator] = {}
        for client_row in all_clients:
            identity = _client_identity(client_row)
            key = _business_client_key(identity)
            if key:
                members[key] = _MemberAccumulator.from_identity(identity)

        future_keys: set[str] = set()
        for record in records:
            if not _is_active_future_booking(record, now_utc):
                continue
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            future_keys.add(key)
            if key in members:
                members[key].add_future_booking(_record_datetime_utc(record))

        without_future = [item for key, item in members.items() if key not in future_keys]
        result = self._build_result(ClientSegmentType.NO_FUTURE_BOOKINGS, without_future, settings.branch_timezone, {"date_from": date_from, "date_to": date_to, "clients_count": len(all_clients), "records_count": len(records), "excluded_future_booking_count": len(future_keys)})
        logger.info("client_segment_loaded segment_type=%s segment_count=%s clients_count=%s records_count=%s", ClientSegmentType.NO_FUTURE_BOOKINGS.value, result.count, len(all_clients), len(records))
        return result

    async def get_cancelled_clients(self, days: int = 30) -> ClientSegmentResult:
        """Return Telegram-parity cancelled segment from local recovery events.

        Telegram counts distinct cancellation recovery event users over the recent window.
        MAX has no cancellation_detected_at_utc/is_test columns today, so created_at is the
        read-only detection timestamp fallback and no test-event predicate is invented.
        scheduled_at is intentionally not used because it is recovery delivery timing.
        """

        started = time.perf_counter()
        tz_name = self._branch_timezone_or_default()
        tz = _zoneinfo(tz_name)
        now_local = datetime.now(tz)
        cutoff_utc = (now_local - timedelta(days=days)).astimezone(timezone.utc)
        try:
            rows, diagnostics = self._fetch_cancelled_event_rows(cutoff_utc)
        except sqlite3.Error as exc:
            logger.warning("client_segment_local_events_error segment_type=%s error_class=%s", ClientSegmentType.CANCELLED.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_local_events_error") from exc

        members: dict[str, _MemberAccumulator] = {}
        for row in rows:
            platform_user_id = _normalize_id(row.get("platform_user_id"))
            if not platform_user_id:
                continue
            accumulator = members.setdefault(
                platform_user_id,
                _MemberAccumulator(
                    yclients_client_id=_normalize_id(row.get("yclients_client_id")) or None,
                    name=None,
                    phone=None,
                ),
            )
            accumulator.add_visit(_parse_raw_datetime(row.get(diagnostics["timestamp_column"])))

        diagnostics.update({
            "date_from": cutoff_utc.isoformat(),
            "date_to": now_local.astimezone(timezone.utc).isoformat(),
            "events_count": len(rows),
            "distinct_platform_user_id_count": len(members),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        })
        return self._build_result(ClientSegmentType.CANCELLED, members.values(), tz_name, diagnostics)

    async def list_masters(self) -> list[dict[str, str]]:
        settings = self._require_settings()
        try:
            async with _build_client(settings) as client:
                payload = await get_staff(client, company_id=str(settings.company_id))
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=by_master_picker error_class=%s", type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc
        items: list[dict[str, str]] = []
        for row in _extract_rows(payload):
            if _truthy(row.get("is_deleted") or row.get("deleted") or row.get("is_fired")):
                continue
            item_id = _normalize_id(row.get("id") or row.get("staff_id"))
            if not item_id:
                continue
            items.append({"id": item_id, "title": _normalize_id(row.get("name") or row.get("fullname") or row.get("title")) or f"Мастер {item_id}"})
        return items

    async def list_service_categories(self) -> list[dict[str, str]]:
        settings = self._require_settings()
        try:
            async with _build_client(settings) as client:
                payload = await get_service_categories(client, company_id=str(settings.company_id))
                rows = _extract_rows(payload)
                if not rows:
                    services_payload = await get_services(client, company_id=str(settings.company_id))
                    rows = _service_categories_from_services(_extract_rows(services_payload))
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=by_service_picker error_class=%s", type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc
        items: list[dict[str, str]] = []
        for row in rows:
            raw_id = _normalize_id(row.get("id") or row.get("category_id"))
            title = _normalize_id(row.get("title") or row.get("name")) or (f"Категория {raw_id}" if raw_id else "")
            if not raw_id or not title:
                continue
            item_id = _safe_service_category_callback_id(raw_id, title)
            items.append({"id": item_id, "title": title})
        deduped = list({item["id"]: item for item in items}.values())
        deduped.sort(key=lambda item: item["title"].casefold())
        return deduped

    async def get_clients_by_master(self, master_id: str) -> ClientSegmentResult:
        selected_master_id = str(master_id).strip()
        if not selected_master_id:
            raise ClientSegmentsLoadError("segment_stale_master_id")
        result = await self._get_clients_by_record_filter(
            ClientSegmentType.BY_MASTER,
            lambda record: _record_master_id(record) == selected_master_id,
            {"master_id": selected_master_id},
        )
        master_name = await self._resolve_master_name(selected_master_id)
        return replace(result, title=_master_segment_title(master_name))

    async def _resolve_master_name(self, master_id: str) -> str | None:
        selected_master_id = str(master_id).strip()
        for item in await self.list_masters():
            if str(item.get("id", "")).strip() == selected_master_id:
                return str(item.get("title", "")).strip() or None
        return None

    async def get_clients_by_service_category(self, category_id: str) -> ClientSegmentResult:
        cid = str(category_id).strip()
        if not cid or cid == "picker":
            raise ClientSegmentsLoadError("segment_stale_callback")
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
        date_to = now_local.date().isoformat()
        started = time.perf_counter()
        try:
            services = await self._fetch_services(settings)
            service_ids: set[str] = set()
            category_name = ""
            for service in services:
                sid = _normalize_id(service.get("id") or service.get("service_id"))
                raw_cid, raw_name = _service_category_fields(service)
                if _service_category_callback_id_matches(cid, raw_cid, raw_name):
                    if sid:
                        service_ids.add(sid)
                    category_name = category_name or raw_name
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", ClientSegmentType.BY_SERVICE.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        members: dict[str, _MemberAccumulator] = {}
        records_count = 0
        for record in records:
            if _record_is_deleted(record) or _record_is_cancelled(record):
                continue
            if not _record_service_ids(record).intersection(service_ids):
                continue
            records_count += 1
            identity = _record_client_identity(record)
            key = _business_client_key(identity) or _record_name_phone_key(identity)
            if not key:
                continue
            accumulator = members.setdefault(key, _MemberAccumulator.from_identity(identity))
            accumulator.add_visit(_record_datetime_utc(record))

        result = self._build_result(
            ClientSegmentType.BY_SERVICE,
            members.values(),
            "Europe/Moscow",
            {
                "category_id": cid,
                "category_name": category_name or f"Категория {cid}",
                "service_ids": sorted(service_ids),
                "records_count": records_count,
                "date_from": date_from,
                "date_to": date_to,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return ClientSegmentResult(
            segment_type=result.segment_type,
            title=f"✂️ Клиенты категории: {category_name or f'Категория {cid}'}",
            members=result.members,
            description="Клиенты, которые пользовались услугами из выбранной категории.",
            branch_timezone="Europe/Moscow",
            calculated_at=result.calculated_at,
            diagnostics=result.diagnostics,
        )

    async def get_birthday_soon_clients(self, window_days: int = 7) -> ClientSegmentResult:
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        today = datetime.now(tz).date()
        end = today + timedelta(days=window_days)
        try:
            client_rows = await self._search_clients(settings)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", ClientSegmentType.BIRTHDAY_SOON.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc
        members: dict[str, _MemberAccumulator] = {}
        yclients_checked = 0
        for row in client_rows:
            yclients_checked += 1
            if _client_is_deleted_or_archived(row):
                continue
            bday = _parse_birthday(row.get("birth_date") or row.get("bdate"))
            if bday is None or not _is_birthday_soon(bday, today, end):
                continue
            identity = _client_identity(row)
            key = _business_client_key(identity)
            if key:
                members[key] = _MemberAccumulator.from_identity(identity)
        local_checked = self._merge_local_birthday_users(members, today=today, end=end)
        return self._build_result(
            ClientSegmentType.BIRTHDAY_SOON,
            members.values(),
            settings.branch_timezone,
            {
                "clients_count": yclients_checked,
                "yclients_clients_checked": yclients_checked,
                "local_clients_checked": local_checked,
                "date_from": today.isoformat(),
                "date_to": end.isoformat(),
            },
        )

    async def _get_clients_by_record_filter(self, segment_type: ClientSegmentType, predicate, extra: dict[str, Any]) -> ClientSegmentResult:
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
        date_to = (now_local + timedelta(days=FUTURE_LOOKAHEAD_DAYS)).date().isoformat()
        started = time.perf_counter()
        try:
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", segment_type.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc
        members: dict[str, _MemberAccumulator] = {}
        for record in records:
            if _record_is_deleted(record) or _record_is_cancelled(record) or not predicate(record):
                continue
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            accumulator = members.setdefault(key, _MemberAccumulator.from_identity(identity))
            event_dt = _record_datetime_utc(record)
            if event_dt and event_dt > now_local.astimezone(timezone.utc):
                accumulator.add_future_booking(event_dt)
            else:
                accumulator.add_visit(event_dt)
        return self._build_result(segment_type, members.values(), settings.branch_timezone, {"date_from": date_from, "date_to": date_to, "records_count": len(records), "duration_ms": int((time.perf_counter() - started) * 1000), **extra})

    def _require_settings(self) -> YClientsSettings:
        settings = load_active_yclients_settings(self._settings_repository, operation="get_client_segments")
        if not has_required_yclients_credentials(settings):
            raise ClientSegmentsNotConfiguredError("yclients_settings_missing")
        return settings


    def _branch_timezone_or_default(self) -> str:
        try:
            return self._require_settings().branch_timezone
        except Exception:
            return DEFAULT_BRANCH_TIMEZONE

    def _fetch_cancelled_event_rows(self, cutoff_utc: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        with closing(sqlite3.connect(self._database_path)) as connection:
            connection.row_factory = sqlite3.Row
            columns = {row[1] for row in connection.execute("PRAGMA table_info(cancellation_recovery_events)").fetchall()}
            if not columns:
                raise sqlite3.OperationalError("cancellation_recovery_events table is missing")
            timestamp_column = "cancellation_detected_at_utc" if "cancellation_detected_at_utc" in columns else "created_at"
            selected_columns = ["platform_user_id", "yclients_client_id", timestamp_column]
            sql = f"SELECT {', '.join(selected_columns)} FROM cancellation_recovery_events WHERE platform_user_id IS NOT NULL"
            if "is_test" in columns:
                sql += " AND COALESCE(is_test, 0) = 0"
            fetched = [dict(row) for row in connection.execute(sql).fetchall()]
        rows = []
        for row in fetched:
            event_dt = _parse_raw_datetime(row.get(timestamp_column))
            if event_dt is None or event_dt < cutoff_utc:
                continue
            rows.append(row)
        return rows, {
            "source_table": "cancellation_recovery_events",
            "timestamp_column": timestamp_column,
            "is_test_column_present": "is_test" in columns,
            "scheduled_at_used": False,
        }

    async def _fetch_clients(self, settings: YClientsSettings) -> list[dict[str, Any]]:
        async with _build_client(settings) as client:
            rows: list[dict[str, Any]] = []
            page = 1
            while True:
                payload = await list_clients(client, company_id=str(settings.company_id), page=page, count=YCLIENTS_PAGE_SIZE)
                data = _extract_rows(payload)
                if not data:
                    break
                rows.extend(data)
                if len(data) < YCLIENTS_PAGE_SIZE:
                    break
                page += 1
            return rows

    async def _search_clients(self, settings: YClientsSettings) -> list[dict[str, Any]]:
        async with _build_client(settings) as client:
            rows: list[dict[str, Any]] = []
            page = 1
            while True:
                payload = await search_clients(client, company_id=str(settings.company_id), query="", page=page, count=YCLIENTS_PAGE_SIZE)
                data = _extract_rows(payload)
                if not data:
                    break
                rows.extend(data)
                if len(data) < YCLIENTS_PAGE_SIZE:
                    break
                page += 1
            return rows

    async def _fetch_services(self, settings: YClientsSettings) -> list[dict[str, Any]]:
        async with _build_client(settings) as client:
            return _extract_rows(await get_services(client, company_id=str(settings.company_id)))

    async def _fetch_records(self, settings: YClientsSettings, *, date_from: str, date_to: str) -> list[dict[str, Any]]:
        async with _build_client(settings) as client:
            records: list[dict[str, Any]] = []
            page = 1
            while True:
                payload = await list_bookings_by_date_range(client, company_id=str(settings.company_id), date_from=date_from, date_to=date_to, page=page, count=YCLIENTS_PAGE_SIZE)
                data = _extract_rows(payload)
                if not data:
                    break
                records.extend(data)
                if len(data) < YCLIENTS_PAGE_SIZE:
                    break
                page += 1
            return records

    def _build_result(self, segment_type: ClientSegmentType, accumulators: list[_MemberAccumulator] | Any, branch_timezone: str, diagnostics: dict[str, Any]) -> ClientSegmentResult:
        members = [item.to_member() for item in accumulators]
        members.sort(key=lambda member: member.last_visit_at or member.future_booking_at or "", reverse=True)
        return ClientSegmentResult(
            segment_type=segment_type.value,
            title=SEGMENT_TITLES[segment_type],
            description=SEGMENT_DESCRIPTIONS[segment_type],
            members=members,
            branch_timezone=branch_timezone,
            calculated_at=datetime.now(_zoneinfo(branch_timezone)).isoformat(),
            diagnostics=diagnostics,
        )

    def _merge_local_birthday_users(self, members: dict[str, "_MemberAccumulator"], *, today, end) -> int:
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.row_factory = sqlite3.Row
                columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
                if not columns or "is_registered" not in columns:
                    return 0
                selected = [name for name in ("user_id", "yclients_client_id", "phone", "birth_date", "first_name", "display_name") if name in columns]
                rows = [dict(row) for row in connection.execute(f"SELECT {', '.join(selected)} FROM users WHERE user_id IS NOT NULL AND COALESCE(is_registered,0)=1 AND birth_date IS NOT NULL AND birth_date!=''").fetchall()]
        except Exception:
            logger.exception("birthday_segment_local_fallback_failed")
            return 0
        checked = 0
        for row in rows:
            checked += 1
            parsed = _parse_birthday(row.get("birth_date"))
            if parsed is None or not _is_birthday_soon(parsed, today, end):
                continue
            identity = {
                "yclients_client_id": row.get("yclients_client_id"),
                "phone": row.get("phone"),
                "name": row.get("display_name") or row.get("first_name"),
            }
            user_id = _normalize_id(row.get("user_id"))
            key = _business_client_key(identity) or (f"tg:{user_id}" if user_id else "")
            if key and key not in members:
                members[key] = _MemberAccumulator.from_identity(identity)
        return checked


@dataclass
class _MemberAccumulator:
    yclients_client_id: str | None
    name: str | None
    phone: str | None
    last_visit_at: str | None = None
    future_booking_at: str | None = None
    visits_count: int = 0

    @classmethod
    def from_identity(cls, identity: dict[str, Any]) -> "_MemberAccumulator":
        return cls(
            yclients_client_id=_normalize_id(identity.get("yclients_client_id")) or None,
            name=_normalize_id(identity.get("name")) or None,
            phone=_normalize_id(identity.get("phone")) or None,
        )

    def add_visit(self, event_dt: datetime | None) -> None:
        self.visits_count += 1
        if event_dt is None:
            return
        iso = event_dt.isoformat()
        if self.last_visit_at is None or event_dt > _parse_datetime(self.last_visit_at):
            self.last_visit_at = iso

    def add_future_booking(self, event_dt: datetime | None) -> None:
        if event_dt is None:
            return
        iso = event_dt.isoformat()
        if self.future_booking_at is None or event_dt < _parse_datetime(self.future_booking_at):
            self.future_booking_at = iso

    def to_member(self) -> ClientSegmentMember:
        return ClientSegmentMember(
            yclients_client_id=self.yclients_client_id,
            name=self.name,
            phone=self.phone,
            last_visit_at=self.last_visit_at,
            future_booking_at=self.future_booking_at,
            visits_count=self.visits_count,
            source="yclients",
        )


def format_segment_summary(result: ClientSegmentResult, *, limit: int = SEGMENT_LIST_LIMIT) -> str:
    """Build user-facing segment summary with masked phones."""

    if result.segment_type in {
        ClientSegmentType.ACTIVE_30.value,
        ClientSegmentType.LOST_30.value,
        ClientSegmentType.LOST_60.value,
        ClientSegmentType.LOST_90.value,
        ClientSegmentType.CANCELLED.value,
        ClientSegmentType.BY_SERVICE.value,
        ClientSegmentType.BIRTHDAY_SOON.value,
    }:
        updated = _format_local_datetime(result.calculated_at, result.branch_timezone)
        if result.segment_type == ClientSegmentType.NO_FUTURE_BOOKINGS.value:
            yclients_count = result.count
            telegram_count = result.diagnostics.get("telegram_recipients_count")
            if telegram_count is None:
                telegram_count = result.diagnostics.get("telegram_count", 0)
            lines = [
                result.title,
                "",
                result.description,
                "",
                f"Клиентов в YClients: {yclients_count}",
                f"Получателей в Telegram: {max(0, int(telegram_count or 0))}",
                f"Обновлено: {updated or '—'}",
            ]
            if yclients_count != int(telegram_count or 0):
                lines.extend(["", "ℹ️ YClients показывает бизнес-аудиторию, а Telegram — только клиентов, связанных с ботом для отправки."])
            if not result.members:
                lines.extend(["", "😌 В этом сегменте пока нет клиентов."])
            return "\n".join(lines)

        lines = [
            result.title,
            "",
            result.description,
            "",
            f"Количество клиентов: {result.count}",
            f"Обновлено: {updated or '—'}",
        ]
        if not result.members:
            lines.extend(["", "😌 В этом сегменте пока нет клиентов."])
        return "\n".join(lines)

    lines = [f"🎯 Сегмент: {_plain_segment_title(result.title)}", f"Клиентов: {result.count}", "Источник: YClients"]
    if not result.members:
        lines.extend(["", "В этом сегменте пока нет клиентов 🙏"])
        return "\n".join(lines)

    lines.append("")
    shown_members = result.members[: max(1, limit)]
    for index, member in enumerate(shown_members, start=1):
        name = member.name or "Клиент"
        phone = mask_phone(member.phone)
        lines.append(f"{index}. {name} — {phone}")
        if member.last_visit_at:
            lines.append(f"   Последний визит: {_format_local_date(member.last_visit_at, result.branch_timezone)}")
        if member.future_booking_at:
            lines.append(f"   Будущая запись: {_format_local_date(member.future_booking_at, result.branch_timezone)}")
        if member.visits_count:
            lines.append(f"   Визитов в расчёте: {member.visits_count}")
        lines.append("")

    if result.count > len(shown_members):
        lines.append(f"Показаны первые {len(shown_members)} из {result.count}.")
    return "\n".join(lines).rstrip()


def _format_lost_segment_detail(result: ClientSegmentResult) -> str:
    """Format lost segment details like the Telegram segment detail screen."""

    text = (
        f"{result.title}\n\n"
        f"{result.description}\n\n"
        f"Количество клиентов: {result.count}\n"
        f"Обновлено: {_format_local_date(result.calculated_at, result.branch_timezone) if result.calculated_at else '—'}"
    )
    if not result.count:
        text += "\n\n😌 В этом сегменте пока нет клиентов."
    return text


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for safe segment screens."""

    clean = _normalize_id(phone)
    if not clean:
        return "телефон не указан"
    digits = "".join(ch for ch in clean if ch.isdigit())
    if len(digits) < 7:
        return "***"
    prefix = "+" if clean.startswith("+") else ""
    return f"{prefix}{digits[:4]}***{digits[-4:]}"


def _build_client(settings: YClientsSettings) -> YClientsClient:
    return build_yclients_client_from_active_settings(settings)


def _is_valid_past_visit(record: dict[str, Any], now_utc: datetime) -> bool:
    if _record_is_deleted(record):
        return False
    event_dt = _record_datetime_utc(record)
    if event_dt and event_dt > now_utc:
        return False
    return record.get("attendance") == 1


def _is_active_future_booking(record: dict[str, Any], now_utc: datetime) -> bool:
    """Return Telegram-parity future active booking predicate for YClients records."""

    if bool(record.get("deleted")):
        return False
    event_dt = _record_date_field_utc(record, ("datetime", "date"))
    if not event_dt or event_dt <= now_utc:
        return False
    return record.get("attendance") in (None, 0, 2)


def _record_datetime_utc(record: dict[str, Any]) -> datetime | None:
    return _record_date_field_utc(record, ("datetime", "date", "appointment_datetime", "record_datetime", "visit_datetime", "seance_date"))


def _record_created_at_utc(record: dict[str, Any]) -> datetime | None:
    return _record_date_field_utc(record, ("created_at", "create_date", "created", "date_create", "create_datetime"))


def _record_date_field_utc(record: dict[str, Any], field_names: tuple[str, ...]) -> datetime | None:
    for field_name in field_names:
        parsed = _parse_raw_datetime(record.get(field_name))
        if parsed is not None:
            return parsed
    return None


def _parse_raw_datetime(raw: Any) -> datetime | None:
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
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _activity_datetime_for_window(record: dict[str, Any], now_local: datetime, days: int) -> datetime | None:
    now_utc = now_local.astimezone(timezone.utc)
    threshold = now_utc - timedelta(days=days)
    event_dt = _record_datetime_utc(record)
    created_at = _record_created_at_utc(record)
    if event_dt and threshold <= event_dt <= now_utc:
        return event_dt
    if created_at and threshold <= created_at <= now_utc:
        return created_at
    return None


def _record_is_deleted(record: dict[str, Any]) -> bool:
    return bool(record.get("deleted") or record.get("is_deleted"))


def _record_is_cancelled(record: dict[str, Any]) -> bool:
    if bool(record.get("cancelled") or record.get("canceled") or record.get("is_cancelled") or record.get("is_canceled")):
        return True
    status = _normalize_id(record.get("status") or record.get("record_status") or record.get("state")).lower()
    if status in {"cancelled", "canceled", "cancel", "deleted", "delete"}:
        return True
    attendance = _normalize_id(record.get("attendance") if record.get("attendance") is not None else record.get("visit_attendance")).lower()
    return attendance in {"-1", "cancelled", "canceled", "no_show", "noshow", "not_come"}


def _record_status_sample(record: dict[str, Any]) -> str:
    return _normalize_id(record.get("status") or record.get("record_status") or record.get("state") or record.get("attendance") or "unknown")[:32]


def _date_fields_present(record: dict[str, Any]) -> str:
    fields = [field for field in ("datetime", "date", "appointment_datetime", "record_datetime", "visit_datetime", "seance_date", "created_at", "create_date", "created", "date_create", "create_datetime") if record.get(field) is not None]
    return "+".join(fields[:4]) or "none"

def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_client_identity(record: dict[str, Any]) -> dict[str, Any]:
    raw_client = record.get("client") if isinstance(record.get("client"), dict) else {}
    return {
        "yclients_client_id": _normalize_id(raw_client.get("id") or record.get("client_id")) or None,
        "phone": _normalize_id(raw_client.get("phone") or record.get("phone")) or None,
        "name": _normalize_id(raw_client.get("name") or record.get("client_name")) or None,
    }


def _client_identity(client_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "yclients_client_id": _normalize_id(client_row.get("id") or client_row.get("client_id")) or None,
        "phone": _normalize_id(client_row.get("phone")) or None,
        "name": _normalize_id(client_row.get("name") or client_row.get("fullname") or client_row.get("title")) or None,
    }


def _business_client_key(identity: dict[str, Any]) -> str:
    yclients_client_id = _normalize_id(identity.get("yclients_client_id"))
    phone = _normalize_phone(identity.get("phone"))
    if yclients_client_id:
        return f"yc:{yclients_client_id}"
    if phone:
        return f"phone:{phone}"
    return ""


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "records", "clients"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _zoneinfo(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_BRANCH_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("client_segment_timezone_invalid timezone=%s", tz_name)
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)


def _format_local_date(value: str, tz_name: str) -> str:
    try:
        return _parse_datetime(value).astimezone(_zoneinfo(tz_name)).strftime("%d.%m.%Y")
    except Exception:
        return value


def _format_local_datetime(value: str, tz_name: str) -> str:
    try:
        return _parse_datetime(value).astimezone(_zoneinfo(tz_name)).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return value


def _plain_segment_title(title: str) -> str:
    return title.replace("👥 ", "").replace("🔥 ", "").replace("📆 ", "").replace("🗓 ", "").strip()


def _master_segment_title(master_name: str | None = None) -> str:
    name = (master_name or "").strip()
    if name:
        return f"💈 Клиенты мастера: {name}"
    return "💈 Клиенты выбранного мастера"


def format_segments_overview(overview: ClientSegmentsOverview) -> str:
    return (
        "🎯 Сегменты клиентов\n\n"
        "Бот автоматически распределяет клиентов по группам на основе визитов, записей и данных из YClients.\n\n"
        f"👥 Все клиенты: {overview.all_count}\n"
        f"🔥 Активные за 30 дней: {overview.active_30_count}"
    )

def _dedupe_client_rows(rows: list[dict[str, Any]]) -> dict[str, _MemberAccumulator]:
    members: dict[str, _MemberAccumulator] = {}
    for row in rows:
        if _client_is_deleted_or_archived(row):
            continue
        identity = _client_identity(row)
        key = _business_client_key(identity)
        if key:
            members[key] = _MemberAccumulator.from_identity(identity)
    return members

def _active_keys_by_window(records: list[dict[str, Any]], now_local: datetime, *, allowed_keys: set[str] | None = None) -> tuple[dict[int, set[str]], dict[str, Any]]:
    windows: dict[int, set[str]] = {7: set(), 30: set(), 90: set()}
    diagnostics: dict[str, Any] = {
        "records_with_client_identity_count": 0,
        "records_with_datetime_count": 0,
        "records_with_created_at_count": 0,
        "skipped_cancelled_count": 0,
        "skipped_deleted_count": 0,
        "skipped_no_identity_count": 0,
        "skipped_no_date_count": 0,
        "sample_record_statuses": [],
        "sample_date_fields_present": [],
    }
    for record in records:
        status_sample = _record_status_sample(record)
        if status_sample not in diagnostics["sample_record_statuses"] and len(diagnostics["sample_record_statuses"]) < 8:
            diagnostics["sample_record_statuses"].append(status_sample)
        date_fields = _date_fields_present(record)
        if date_fields not in diagnostics["sample_date_fields_present"] and len(diagnostics["sample_date_fields_present"]) < 8:
            diagnostics["sample_date_fields_present"].append(date_fields)
        identity = _record_client_identity(record)
        key = _business_client_key(identity)
        if key:
            diagnostics["records_with_client_identity_count"] += 1
        if _record_datetime_utc(record) is not None:
            diagnostics["records_with_datetime_count"] += 1
        if _record_created_at_utc(record) is not None:
            diagnostics["records_with_created_at_count"] += 1
        if _record_is_deleted(record):
            diagnostics["skipped_deleted_count"] += 1
            continue
        if _record_is_cancelled(record):
            diagnostics["skipped_cancelled_count"] += 1
            continue
        if not key or (allowed_keys is not None and key not in allowed_keys):
            diagnostics["skipped_no_identity_count"] += 1
            continue
        matched = False
        for days in windows:
            if _activity_datetime_for_window(record, now_local, days) is not None:
                windows[days].add(key)
                matched = True
        if not matched:
            diagnostics["skipped_no_date_count"] += 1
    return windows, diagnostics


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да"} or value is True


def _record_master_id(record: dict[str, Any]) -> str:
    staff = record.get("staff") if isinstance(record.get("staff"), dict) else {}
    return _normalize_id(record.get("staff_id") or record.get("master_id") or staff.get("id"))


def _record_service_category_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    service = record.get("service") if isinstance(record.get("service"), dict) else {}
    for value in (record.get("service_category_id"), record.get("category_id"), service.get("category_id"), service.get("category")):
        if isinstance(value, dict):
            value = value.get("id")
        clean = _normalize_id(value)
        if clean:
            ids.add(clean)
    services = record.get("services") if isinstance(record.get("services"), list) else []
    for item in services:
        if not isinstance(item, dict):
            continue
        for value in (item.get("category_id"), item.get("category")):
            if isinstance(value, dict):
                value = value.get("id")
            clean = _normalize_id(value)
            if clean:
                ids.add(clean)
    return ids


def _record_service_ids(record: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    service = record.get("service") if isinstance(record.get("service"), dict) else {}
    for value in (record.get("service_id"), service.get("id")):
        clean = _normalize_id(value)
        if clean:
            ids.add(clean)
    services = record.get("services") if isinstance(record.get("services"), list) else []
    for item in services:
        if isinstance(item, dict):
            clean = _normalize_id(item.get("id") or item.get("service_id"))
        else:
            clean = _normalize_id(item)
        if clean:
            ids.add(clean)
    return ids


def _service_category_fields(service: dict[str, Any]) -> tuple[str, str]:
    raw = service.get("category") if isinstance(service.get("category"), dict) else {}
    category_id = _normalize_id(service.get("category_id") or raw.get("id"))
    category_name = _normalize_id(raw.get("title") or raw.get("name") or service.get("category_title") or service.get("category_name"))
    return category_id, category_name


def _service_categories_from_services(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories: dict[str, dict[str, Any]] = {}
    for service in services:
        raw = service.get("category") if isinstance(service.get("category"), dict) else {}
        cid = _normalize_id(service.get("category_id") or raw.get("id"))
        if cid:
            categories[cid] = {"id": cid, "title": _normalize_id(raw.get("title") or raw.get("name") or service.get("category_title")) or f"Категория {cid}"}
    return list(categories.values())


def _short_hash(value: Any) -> str:
    import hashlib

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


def _record_name_phone_key(identity: dict[str, Any]) -> str:
    name = _normalize_id(identity.get("name"))
    phone = _normalize_id(identity.get("phone"))
    if name and phone:
        return f"name_phone:{name}:{phone}"
    return ""


def _parse_birthday(raw: Any):
    text = _normalize_id(raw)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m"):
        try:
            parsed = datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
            return parsed
        except Exception:
            continue
    return None


def _is_birthday_soon(parsed, today, end) -> bool:
    try:
        next_birthday = parsed.replace(year=today.year)
    except ValueError:
        next_birthday = parsed.replace(year=today.year, day=28)
    if next_birthday < today:
        try:
            next_birthday = parsed.replace(year=today.year + 1)
        except ValueError:
            next_birthday = parsed.replace(year=today.year + 1, day=28)
    return today <= next_birthday <= end

def _client_is_deleted_or_archived(row: dict[str, Any]) -> bool:
    return bool(row.get("deleted") or row.get("is_deleted") or row.get("is_archive") or row.get("is_archived") or row.get("archive"))

def _count_deleted_clients(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _client_is_deleted_or_archived(row))
