"""Lost clients calculation from YClients data for the MAX bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.endpoints import list_bookings_by_date_range
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings, YClientsSettingsRepository
from max_barbershop_bot.services.broadcasts import BroadcastRecipient
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

LOST_CLIENTS_DAYS = 30
LOST_CLIENTS_LIST_LIMIT = 20
YCLIENTS_PAGE_SIZE = 200
LOOKBACK_DAYS = 365
FUTURE_LOOKAHEAD_DAYS = 365


@dataclass(frozen=True)
class LostClient:
    """One YClients client considered lost and optionally mapped to MAX."""

    yclients_client_id: str | None
    name: str | None
    phone: str | None
    last_visit_at: str | None
    days_since_last_visit: int
    future_booking_at: str | None
    visits_count: int
    reason: str
    is_mappable_to_max: bool = False
    platform_user_id: str | None = None
    max_user_id: str | None = None
    chat_id: str | None = None


@dataclass(frozen=True)
class LostClientsResult:
    """Calculated lost clients result with mapped MAX recipient metadata."""

    total: int
    mappable_count: int
    clients: list[LostClient] = field(default_factory=list)
    branch_timezone: str = DEFAULT_BRANCH_TIMEZONE
    calculated_at: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


class LostClientsNotConfiguredError(RuntimeError):
    """YClients settings are absent or incomplete."""


class LostClientsLoadError(RuntimeError):
    """Lost clients data could not be loaded safely."""


class LostClientsService:
    """Calculate lost clients from YClients records and map them to MAX users."""

    def __init__(self, settings_repository: YClientsSettingsRepository, users_repository: UsersRepository) -> None:
        self._settings_repository = settings_repository
        self._users_repository = users_repository

    async def get_lost_clients(self) -> LostClientsResult:
        """Load YClients records, calculate lost clients and map reachable MAX recipients."""

        settings = self._require_settings()
        tz = _zoneinfo(settings.branch_timezone)
        now_local = datetime.now(tz)
        date_from = (now_local - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
        date_to = (now_local + timedelta(days=FUTURE_LOOKAHEAD_DAYS)).date().isoformat()
        try:
            records = await self._fetch_records(settings, date_from=date_from, date_to=date_to)
        except YClientsError as exc:
            logger.warning("lost_clients_yclients_error error_class=%s", type(exc).__name__)
            raise LostClientsLoadError("lost_clients_yclients_error") from exc

        now_utc = now_local.astimezone(timezone.utc)
        threshold = now_local - timedelta(days=LOST_CLIENTS_DAYS)
        accumulators: dict[str, _LostClientAccumulator] = {}
        for record in records:
            identity = _record_client_identity(record)
            key = _business_client_key(identity)
            if not key:
                continue
            accumulator = accumulators.setdefault(key, _LostClientAccumulator.from_identity(identity))
            event_dt = _record_datetime_utc(record)
            if _is_active_future_booking(record, now_utc):
                accumulator.add_future_booking(event_dt)
            elif _is_valid_past_visit(record, now_utc):
                accumulator.add_visit(event_dt)

        lost = [
            item
            for item in accumulators.values()
            if item.last_visit_at
            and not item.future_booking_at
            and _parse_datetime(item.last_visit_at).astimezone(tz) <= threshold
        ]
        lost.sort(key=lambda item: item.last_visit_at or "", reverse=True)
        clients = [item.to_lost_client(now_local=now_local, branch_timezone=settings.branch_timezone) for item in lost]
        mapped_clients = map_lost_clients_to_max_users(clients, self._users_repository)
        result = LostClientsResult(
            total=len(mapped_clients),
            mappable_count=sum(1 for client in mapped_clients if client.is_mappable_to_max),
            clients=mapped_clients,
            branch_timezone=settings.branch_timezone,
            calculated_at=now_local.isoformat(),
            diagnostics={
                "date_from": date_from,
                "date_to": date_to,
                "records_count": len(records),
                "lost_days": LOST_CLIENTS_DAYS,
            },
        )
        logger.info(
            "lost_clients_loaded total=%s mappable_count=%s records_count=%s",
            result.total,
            result.mappable_count,
            len(records),
        )
        return result

    def _require_settings(self) -> YClientsSettings:
        settings = load_active_yclients_settings(self._settings_repository, operation="get_lost_clients")
        if not has_required_yclients_credentials(settings):
            raise LostClientsNotConfiguredError("yclients_settings_missing")
        return settings

    async def _fetch_records(self, settings: YClientsSettings, *, date_from: str, date_to: str) -> list[dict[str, Any]]:
        async with _build_client(settings) as client:
            records: list[dict[str, Any]] = []
            page = 1
            while True:
                payload = await list_bookings_by_date_range(
                    client,
                    company_id=str(settings.company_id),
                    date_from=date_from,
                    date_to=date_to,
                    page=page,
                    count=YCLIENTS_PAGE_SIZE,
                )
                data = _extract_rows(payload)
                if not data:
                    break
                records.extend(data)
                if len(data) < YCLIENTS_PAGE_SIZE:
                    break
                page += 1
            return records


@dataclass
class _LostClientAccumulator:
    yclients_client_id: str | None
    name: str | None
    phone: str | None
    last_visit_at: str | None = None
    future_booking_at: str | None = None
    visits_count: int = 0

    @classmethod
    def from_identity(cls, identity: dict[str, Any]) -> "_LostClientAccumulator":
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

    def to_lost_client(self, *, now_local: datetime, branch_timezone: str) -> LostClient:
        days_since_last_visit = _days_since(self.last_visit_at, now_local, branch_timezone)
        return LostClient(
            yclients_client_id=self.yclients_client_id,
            name=self.name,
            phone=self.phone,
            last_visit_at=self.last_visit_at,
            days_since_last_visit=days_since_last_visit,
            future_booking_at=self.future_booking_at,
            visits_count=self.visits_count,
            reason=build_lost_client_reason(
                days_since_last_visit=days_since_last_visit,
                has_future_booking=bool(self.future_booking_at),
            ),
        )


def build_lost_client_reason(*, days_since_last_visit: int | None, has_future_booking: bool) -> str:
    """Build a friendly reason explaining why a client is considered lost."""

    if has_future_booking:
        return "есть будущая запись"
    if days_since_last_visit is None:
        return "нет будущих записей"
    return f"последний визит был {days_since_last_visit} дней назад, будущих записей нет"


def map_lost_clients_to_max_users(clients: list[LostClient], users_repository: UsersRepository) -> list[LostClient]:
    """Attach reachable MAX user ids using local DB only as mapping data."""

    users = users_repository.list_broadcast_recipients(platform=PLATFORM_MAX, notifications_enabled=True)
    by_client_id = {str(user.yclients_client_id).strip(): user for user in users if user.yclients_client_id}
    by_phone = {_normalize_phone(user.phone): user for user in users if _normalize_phone(user.phone)}
    mapped: list[LostClient] = []
    for client in clients:
        user = None
        if client.yclients_client_id:
            user = by_client_id.get(str(client.yclients_client_id).strip())
        if user is None:
            user = by_phone.get(_normalize_phone(client.phone))
        if user is None:
            mapped.append(client)
            continue
        mapped.append(
            LostClient(
                yclients_client_id=client.yclients_client_id,
                name=client.name,
                phone=client.phone,
                last_visit_at=client.last_visit_at,
                days_since_last_visit=client.days_since_last_visit,
                future_booking_at=client.future_booking_at,
                visits_count=client.visits_count,
                reason=client.reason,
                is_mappable_to_max=True,
                platform_user_id=user.platform_user_id,
                max_user_id=user.max_user_id,
                chat_id=user.chat_id,
            )
        )
    return mapped


def lost_clients_to_broadcast_recipients(clients: list[LostClient]) -> list[BroadcastRecipient]:
    """Convert mapped lost clients to broadcast recipients without duplicates."""

    recipients: dict[str, BroadcastRecipient] = {}
    for client in clients:
        if not client.is_mappable_to_max or not client.platform_user_id:
            continue
        recipients[client.platform_user_id] = BroadcastRecipient(
            platform_user_id=client.platform_user_id,
            max_user_id=client.max_user_id,
            chat_id=client.chat_id,
            display_name=client.name,
        )
    return list(recipients.values())


def format_lost_clients_summary(result: LostClientsResult, *, limit: int = LOST_CLIENTS_LIST_LIMIT) -> str:
    """Build user-facing lost clients summary with masked phones and reasons."""

    if result.total == 0:
        return "Потерянных клиентов сейчас нет ✅"

    lines = [
        "😔 Потерянные клиенты",
        "",
        f"Всего: {result.total}",
        f"Доступны для рассылки в MAX: {result.mappable_count}",
        "",
    ]
    shown_clients = result.clients[: max(1, limit)]
    for index, client in enumerate(shown_clients, start=1):
        name = client.name or "Клиент"
        lines.append(f"{index}. {name} — {mask_phone(client.phone)}")
        lines.append(f"   Причина: {client.reason}")
        lines.append("")
    if result.total > len(shown_clients):
        lines.append(f"Показаны первые {len(shown_clients)} из {result.total}.")
    return "\n".join(lines).rstrip()


def mask_phone(phone: str | None) -> str:
    """Mask a phone number for safe lost clients screens."""

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
        attendance = record.get("visit_attendance")
    if attendance is not None:
        return str(attendance).strip() == "1"
    status = str(record.get("status") or "").strip().lower()
    return status in {"visit", "done", "paid", "completed", "show"}


def _is_active_future_booking(record: dict[str, Any], now_utc: datetime) -> bool:
    if bool(record.get("deleted")):
        return False
    event_dt = _record_datetime_utc(record)
    if not event_dt or event_dt <= now_utc:
        return False
    attendance = record.get("attendance")
    if attendance is None:
        attendance = record.get("visit_attendance")
    if attendance is None:
        status = str(record.get("status") or "").strip().lower()
        return status not in {"cancelled", "canceled", "deleted", "no_show", "noshow"}
    return str(attendance).strip() in {"0", "2"}


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
        for key in ("data", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _days_since(value: str | None, now_local: datetime, tz_name: str) -> int:
    if not value:
        return 0
    last_local = _parse_datetime(value).astimezone(_zoneinfo(tz_name))
    return max(0, (now_local.date() - last_local.date()).days)


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _zoneinfo(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_BRANCH_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("lost_clients_timezone_invalid timezone=%s", tz_name)
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)
