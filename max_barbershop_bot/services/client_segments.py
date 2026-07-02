"""Client segment calculation from YClients data for the MAX bot."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.endpoints import list_bookings_by_date_range, list_clients
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
    ClientSegmentType.ACTIVE_30: "Клиенты с любой реальной неотменённой активностью за последние 30 дней.",
    ClientSegmentType.ACTIVE_90: "Клиенты, которые были активны за последние 90 дней.",
    ClientSegmentType.LOST: "Клиенты с последним визитом 30+ дней назад и без будущей записи.",
    ClientSegmentType.LOST_30: "Клиенты, которые не были 30 дней и не имеют будущей записи.",
    ClientSegmentType.LOST_60: "Клиенты, которые не были 60 дней и не имеют будущей записи.",
    ClientSegmentType.LOST_90: "Клиенты, которые не были 90 дней и не имеют будущей записи.",
    ClientSegmentType.CANCELLED: "Клиенты с отменённой записью в истории YClients.",
    ClientSegmentType.BY_MASTER: "Выбор клиентов по мастеру из истории YClients.",
    ClientSegmentType.BY_SERVICE: "Выбор клиентов по услуге из истории YClients.",
    ClientSegmentType.BIRTHDAY_SOON: "Клиенты, у которых скоро день рождения.",
    ClientSegmentType.NO_FUTURE_BOOKINGS: "Клиенты, у которых сейчас нет будущей записи.",
}


class ClientSegmentService:
    """Calculate client segments from YClients records and clients only."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

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
        date_from = (now_local - timedelta(days=90)).date().isoformat()
        date_to = (now_local + timedelta(days=90)).date().isoformat()
        started = time.perf_counter()
        try:
            client_rows = await self._fetch_clients(settings)
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", segment_type.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        client_members = _dedupe_client_rows(client_rows)
        active_keys, active_diagnostics = _active_keys_by_window(records, now_local, allowed_keys=set(client_members))
        members: dict[str, _MemberAccumulator] = {}
        for record in records:
            if _record_is_deleted(record) or _record_is_cancelled(record):
                continue
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key or key not in active_keys[days]:
                continue
            accumulator = members.setdefault(key, client_members.get(key) or _MemberAccumulator.from_identity(identity))
            activity_dt = _activity_datetime_for_window(record, now_local, days)
            if activity_dt and activity_dt > now_local.astimezone(timezone.utc):
                accumulator.add_future_booking(activity_dt)
            else:
                accumulator.add_visit(activity_dt)

        diagnostics = {"date_from": date_from, "date_to": date_to, "date_range_from": date_from, "date_range_to": date_to, "clients_count": len(client_rows), "records_count": len(records), "raw_records_count": len(records), "duration_ms": int((time.perf_counter() - started) * 1000), **active_diagnostics}
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
        date_to = (now_local + timedelta(days=FUTURE_LOOKAHEAD_DAYS)).date().isoformat()
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

    def _require_settings(self) -> YClientsSettings:
        settings = load_active_yclients_settings(self._settings_repository, operation="get_client_segments")
        if not has_required_yclients_credentials(settings):
            raise ClientSegmentsNotConfiguredError("yclients_settings_missing")
        return settings

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
    if _record_is_deleted(record) or _record_is_cancelled(record):
        return False
    event_dt = _record_datetime_utc(record)
    if event_dt and event_dt > now_utc:
        return False
    return True


def _is_active_future_booking(record: dict[str, Any], now_utc: datetime) -> bool:
    if _record_is_deleted(record) or _record_is_cancelled(record):
        return False
    event_dt = _record_datetime_utc(record)
    return bool(event_dt and event_dt > now_utc)


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


def _plain_segment_title(title: str) -> str:
    return title.replace("👥 ", "").replace("🔥 ", "").replace("📆 ", "").replace("🗓 ", "").strip()

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

def _client_is_deleted_or_archived(row: dict[str, Any]) -> bool:
    return bool(row.get("deleted") or row.get("is_deleted") or row.get("is_archive") or row.get("is_archived") or row.get("archive"))

def _count_deleted_clients(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _client_is_deleted_or_archived(row))
