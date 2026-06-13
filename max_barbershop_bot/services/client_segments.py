"""Client segment calculation from YClients data for the MAX bot."""

from __future__ import annotations

import logging
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

    ACTIVE_7 = "active_7"
    ACTIVE_30 = "active_30"
    ACTIVE_90 = "active_90"
    LOST = "lost"
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


SEGMENT_TITLES = {
    ClientSegmentType.ACTIVE_7: "🔥 Активные за 7 дней",
    ClientSegmentType.ACTIVE_30: "🔥 Активные за 30 дней",
    ClientSegmentType.ACTIVE_90: "🔥 Активные за 90 дней",
    ClientSegmentType.LOST: "😔 Потерянные клиенты",
    ClientSegmentType.NO_FUTURE_BOOKINGS: "📭 Без будущих записей",
}

SEGMENT_DESCRIPTIONS = {
    ClientSegmentType.ACTIVE_7: "Клиенты, которые были активны за последние 7 дней.",
    ClientSegmentType.ACTIVE_30: "Клиенты, которые были активны за последние 30 дней.",
    ClientSegmentType.ACTIVE_90: "Клиенты, которые были активны за последние 90 дней.",
    ClientSegmentType.LOST: "Клиенты с последним визитом 30+ дней назад и без будущей записи.",
    ClientSegmentType.NO_FUTURE_BOOKINGS: "Клиенты, у которых сейчас нет будущей записи.",
}


class ClientSegmentService:
    """Calculate client segments from YClients records and clients only."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    async def get_active_clients(self, days: int) -> ClientSegmentResult:
        if days not in {7, 30, 90}:
            raise ValueError("days must be one of 7, 30 or 90")
        segment_type = ClientSegmentType(f"active_{days}")
        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=days)).date().isoformat()
        date_to = now_local.date().isoformat()
        try:
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("client_segment_yclients_error segment_type=%s error_class=%s", segment_type.value, type(exc).__name__)
            raise ClientSegmentsLoadError("segment_yclients_error") from exc

        now_utc = now_local.astimezone(timezone.utc)
        members: dict[str, _MemberAccumulator] = {}
        for record in records:
            if not _is_valid_past_visit(record, now_utc):
                continue
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            accumulator = members.setdefault(key, _MemberAccumulator.from_identity(identity))
            accumulator.add_visit(_record_datetime_utc(record))

        result = self._build_result(segment_type, members.values(), settings.branch_timezone, {"date_from": date_from, "date_to": date_to, "records_count": len(records)})
        logger.info("client_segment_loaded segment_type=%s segment_count=%s records_count=%s", segment_type.value, result.count, len(records))
        return result

    async def get_lost_clients(self) -> ClientSegmentResult:
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
        threshold = now_local - timedelta(days=LOST_CLIENTS_DAYS)
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
        result = self._build_result(ClientSegmentType.LOST, lost, settings.branch_timezone, {"date_from": date_from, "date_to": date_to, "records_count": len(records), "lost_days": LOST_CLIENTS_DAYS})
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

    lines = [result.title, "", f"Клиентов: {result.count}"]
    if result.description:
        lines.extend(["", result.description])
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
    if bool(record.get("deleted")):
        return False
    event_dt = _record_datetime_utc(record)
    if event_dt and event_dt > now_utc:
        return False
    attendance = record.get("attendance")
    if attendance is None:
        return True
    return attendance == 1


def _is_active_future_booking(record: dict[str, Any], now_utc: datetime) -> bool:
    if bool(record.get("deleted")):
        return False
    event_dt = _record_datetime_utc(record)
    if not event_dt or event_dt <= now_utc:
        return False
    return record.get("attendance") in (None, 0, 2)


def _record_datetime_utc(record: dict[str, Any]) -> datetime | None:
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
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


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
