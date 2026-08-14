"""Lost clients calculation from YClients data for the MAX bot."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.endpoints import list_bookings_by_date_range, list_user_bookings
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.lost_client_events import LostClientEventsRepository
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
        date_to = now_local.date().isoformat()
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
        f"Недоступны в MAX: {max(0, result.total - result.mappable_count)}",
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
    return _is_completed_visit(record)


def _is_active_future_booking(record: dict[str, Any], now_utc: datetime) -> bool:
    if bool(record.get("deleted")):
        return False
    event_dt = _record_datetime_utc(record)
    if not event_dt or event_dt <= now_utc:
        return False
    return True


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


@dataclass
class LostClientsScanSummary:
    """Telegram-equivalent lost clients scan counters."""

    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


LOST_CLIENTS_BOOKING_BUTTON_TEXT = "✨ Записаться"
LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX = "lost_clients:book:"


def build_lost_client_booking_keyboard(event_id: int) -> MaxInlineKeyboard:
    """Build the Telegram-equivalent booking CTA for a lost-client message."""

    return MaxInlineKeyboard(rows=((MaxButton(text=LOST_CLIENTS_BOOKING_BUTTON_TEXT, payload=f"{LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX}{int(event_id)}"),),))


async def run_lost_clients_scan(
    sender: MaxMessageSender,
    *,
    database_path: str,
    force: bool = False,
    source: str = "yclients",
    is_test: bool = False,
    now: datetime | None = None,
) -> LostClientsScanSummary:
    """Scan inactive MAX users and send Telegram-equivalent lost-client messages."""

    summary = LostClientsScanSummary()
    settings = _load_lost_clients_settings(database_path)
    anti_spam = _load_anti_spam_settings(database_path)
    if not settings.get("enabled") and not force:
        logger.info("lost_clients_scan_skipped_disabled")
        return summary
    ok, reason = _check_lost_clients_working_hours(settings, now=now)
    if not ok and not force:
        logger.info("lost_clients_scan_skipped_hours reason=%s", reason)
        return summary

    settings_repo = YClientsSettingsRepository(database_path)
    users_repo = UsersRepository(database_path)
    events_repo = LostClientEventsRepository(database_path)
    yc_settings = load_active_yclients_settings(settings_repo, operation="run_lost_clients_scan")
    if not has_required_yclients_credentials(yc_settings):
        return summary

    thresholds = _lost_thresholds(settings)
    cooldown_days = max(1, int(float(anti_spam.get("min_interval_hours", 48)) / 24))
    now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    async with _build_client(yc_settings) as client:
        users = [u for u in users_repo.list_users_for_broadcast_audience(platform=PLATFORM_MAX) if u.yclients_client_id]
        for user in users:
            if not user.notifications_enabled:
                summary.skipped += 1
                continue
            try:
                payload = await list_user_bookings(client, company_id=str(yc_settings.company_id), client_id=str(user.yclients_client_id), count=50)
                records = _extract_rows(payload)
                last_visit, last_visit_id = _latest_completed_visit(records)
                if last_visit is None:
                    continue
                if _has_future_booking(records, now_utc) and bool(settings.get("exclude_has_future_booking", True)):
                    events_repo.create_event(
                        yclients_client_id=user.yclients_client_id,
                        client_tg_id=user.platform_user_id,
                        platform_user_id=user.platform_user_id,
                        max_user_id=user.max_user_id,
                        chat_id=user.chat_id,
                        threshold_days=0,
                        segment_key="lost_skip_future",
                        last_visit_datetime_utc=last_visit.isoformat(),
                        last_visit_id=last_visit_id,
                        has_future_booking=True,
                        status="skipped_has_future_booking",
                        source=source,
                        is_test=is_test,
                    )
                    summary.skipped += 1
                    continue
                days_inactive = (now_utc - last_visit).days
                threshold = next((value for value in thresholds if days_inactive >= value), None)
                if threshold is None:
                    continue
                if not is_test and events_repo.has_recent_sent(user.platform_user_id, threshold, cooldown_days):
                    summary.skipped += 1
                    continue
                summary.candidates += 1
                event = events_repo.create_event(
                    yclients_client_id=user.yclients_client_id,
                    client_tg_id=user.platform_user_id,
                    platform_user_id=user.platform_user_id,
                    max_user_id=user.max_user_id,
                    chat_id=user.chat_id,
                    threshold_days=threshold,
                    segment_key=f"lost_{threshold}",
                    last_visit_datetime_utc=last_visit.isoformat(),
                    last_visit_id=last_visit_id,
                    has_future_booking=False,
                    scheduled_send_at_utc=now_utc.isoformat(),
                    status="pending",
                    source=source,
                    is_test=is_test,
                )
                text = str(settings.get(f"text_{threshold}", "") or "")
                allowed, decision = _can_send_lost_client(anti_spam, is_test=is_test)
                if not allowed:
                    events_repo.mark_status(event.id, "skipped", error_summary=decision)
                    summary.skipped += 1
                    continue
                result = await sender.send_to_user(
                    user.max_user_id or user.platform_user_id,
                    text,
                    keyboard=build_lost_client_booking_keyboard(event.id),
                    metadata={"lost_client_event_id": event.id, "threshold_days": threshold, "source": source, "is_test": is_test},
                )
                if result.ok:
                    events_repo.mark_status(event.id, "sent", sent=True)
                    summary.sent += 1
                elif result.is_blocked:
                    events_repo.mark_status(event.id, "blocked", error_summary=result.error_message or result.error_code or "blocked")
                    summary.errors += 1
                else:
                    events_repo.mark_status(event.id, "failed", error_summary=(result.error_message or result.error_code or "send_failed")[:180])
                    summary.errors += 1
            except YClientsError:
                summary.errors += 1
            except Exception as exc:  # pragma: no cover - defensive production logging
                logger.exception("lost_clients_client_processing_failed platform_user_id=%s err=%s", user.platform_user_id, exc)
                summary.errors += 1
    return summary


async def run_lost_clients_loop(
    sender: MaxMessageSender,
    *,
    database_path: str,
    stop_event: asyncio.Event,
    interval_seconds: int,
    error_callback: object | None = None,
) -> None:
    """Run lost-client scans until graceful shutdown."""

    while not stop_event.is_set():
        try:
            await run_lost_clients_scan(sender, database_path=database_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - runtime loop must survive one failed scan.
            if callable(error_callback):
                await error_callback(exc)
            else:
                logger.warning("MAX lost clients diagnostic: error_class=%s", type(exc).__name__, exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(300, int(interval_seconds)))
        except TimeoutError:
            continue


def _load_lost_clients_settings(database_path: str) -> dict[str, Any]:
    return dict(AppSettingsRepository(database_path).get_automation_setting("lost_clients"))


def _load_anti_spam_settings(database_path: str) -> dict[str, Any]:
    return dict(AppSettingsRepository(database_path).get_automation_setting("anti_spam"))


def _load_json_setting(database_path: str, key: str, *, default: dict[str, Any]) -> dict[str, Any]:
    repo = AppSettingsRepository(database_path)
    with repo._connect() as connection:  # small internal helper parity shim for existing key-value store
        row = connection.execute("SELECT value FROM app_settings WHERE key = ? LIMIT 1", (key,)).fetchone()
    if row is None or row["value"] is None:
        return dict(default)
    try:
        parsed = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        parsed = {"enabled": str(row["value"]).strip().lower() in {"1", "true", "yes", "on", "enabled"}}
    if not isinstance(parsed, dict):
        return dict(default)
    merged = dict(default)
    merged.update(parsed)
    return merged


def _lost_thresholds(settings: dict[str, Any]) -> list[int]:
    raw = settings.get("threshold_days", [30, 60, 90])
    values = [int(value) for value in raw if int(value) > 0] if isinstance(raw, list) else [30, 60, 90]
    return sorted(values or [30, 60, 90], reverse=True)


def _latest_completed_visit(records: list[dict[str, Any]]) -> tuple[datetime | None, str | None]:
    last_visit: datetime | None = None
    last_visit_id: str | None = None
    for record in records:
        dt = _record_datetime_utc(record)
        if dt is None or not _is_completed_visit(record):
            continue
        if last_visit is None or dt > last_visit:
            last_visit = dt
            last_visit_id = _normalize_id(record.get("id") or record.get("record_id")) or None
    return last_visit, last_visit_id


def _is_completed_visit(record: dict[str, Any]) -> bool:
    attendance = record.get("attendance")
    if attendance is None:
        attendance = record.get("visit_attendance")
    if attendance is not None:
        return str(attendance).strip() == "1"
    status = str(record.get("status") or "").strip().lower()
    return status in {"visit", "done", "paid", "completed", "show"}


def _has_future_booking(records: list[dict[str, Any]], now_utc: datetime) -> bool:
    return any((dt := _record_datetime_utc(record)) is not None and dt > now_utc for record in records)


def _can_send_lost_client(anti_spam: dict[str, Any], *, is_test: bool) -> tuple[bool, str]:
    if is_test:
        return True, "allowed"
    if anti_spam.get("allow_send") is False or anti_spam.get("enabled") is False:
        return False, "anti_spam_disabled"
    return True, "allowed"


def _check_lost_clients_working_hours(settings: dict[str, Any], *, now: datetime | None) -> tuple[bool, str | None]:
    hours = settings.get("working_hours")
    if not isinstance(hours, dict) or not hours.get("enabled"):
        return True, None
    start = str(hours.get("start") or "").strip()
    end = str(hours.get("end") or "").strip()
    if not start or not end:
        return True, None
    current = (now or datetime.now(timezone.utc)).time()
    try:
        start_t = datetime.strptime(start, "%H:%M").time()
        end_t = datetime.strptime(end, "%H:%M").time()
    except ValueError:
        return True, None
    if start_t <= current <= end_t:
        return True, None
    return False, "outside_working_hours"
