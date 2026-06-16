"""Repeat visit funnel ported from Telegram business behavior."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.repeat_visit_events import RepeatVisitEvent, RepeatVisitEventsRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.notifications import get_notification_history, mark_notification_history_skipped, send_business_notification
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials, load_active_yclients_settings
from max_barbershop_bot.ui.buttons import MENU_BOOKING_PAYLOAD

logger = logging.getLogger(__name__)

REPEAT_VISIT_NOTIFICATION_TYPE = "repeat_visit"
REPEAT_VISIT_DELAY_DAYS = 30
REPEAT_VISIT_ANTISPAM_HOURS = 48
REPEAT_VISIT_TEXTS = [
    "Пора обновить стрижку? 😊\n\nОбычно к этому времени форма уже начинает теряться.",
    "Кажется, самое время снова заглянуть к нам ✂️\n\nПодберём удобное окно для визита?",
    "Ваша стрижка уже могла немного потерять форму 😊\n\nСамое время освежить образ.",
    "Давно не виделись ✂️\n\nМожем подобрать удобное время к вашему мастеру.",
    "Хотите снова выглядеть свежо? 😊\n\nЗапишитесь на удобное время — мы всё подготовим.",
]
_COMPLETED = {"visit", "done", "paid", "completed", "show"}


@dataclass(frozen=True)
class RepeatVisitSummary:
    scheduled: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def repeat_visit_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([[MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)]])


def select_repeat_visit_text() -> str:
    return random.choice(REPEAT_VISIT_TEXTS)


async def schedule_repeat_visit_events(*, database_path: str, now: datetime | None = None, limit: int = 500) -> int:
    """Scan attributed MAX records and create due-later repeat visit events after completed visits."""

    settings = load_active_yclients_settings(YClientsSettingsRepository(database_path), operation="schedule_repeat_visit_events")
    if not has_required_yclients_credentials(settings):
        return 0
    tz_name = normalize_branch_timezone(settings.branch_timezone, flow="repeat_visit", operation="schedule_repeat_visit_events")
    tz = zoneinfo_or_default(tz_name, flow="repeat_visit", operation="schedule_repeat_visit_events")
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    users = UsersRepository(database_path)
    repo = RepeatVisitEventsRepository(database_path)
    scheduled = 0
    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=settings.company_id)
        for attribution in PlatformAttributionRepository(database_path).list_with_yclients_record_ids(limit=limit):
            user = users.find_by_platform_user_id(attribution.platform_user_id, platform=attribution.platform)
            record_id = _clean(attribution.yclients_record_id)
            client_id = _clean(attribution.yclients_client_id) or _clean(getattr(user, "yclients_client_id", None))
            if user is None or not record_id:
                _log(platform_user_id=attribution.platform_user_id, yclients_record_id=record_id, skipped_reason="user_or_record_missing")
                continue
            if repo.get_event(platform=attribution.platform, platform_user_id=attribution.platform_user_id, yclients_record_id=record_id):
                _log(platform_user_id=attribution.platform_user_id, yclients_record_id=record_id, history_existing=True)
                continue
            try:
                record = _extract_record(await service.get_booking_details(company_id=settings.company_id, yclients_record_id=record_id))
            except Exception as exc:  # noqa: BLE001 - YClients details are diagnostic-only here.
                _log(platform_user_id=attribution.platform_user_id, yclients_record_id=record_id, error_class=type(exc).__name__)
                continue
            completed = _record_is_completed(record)
            visit_dt = _record_datetime(record, tz_name)
            if not completed or visit_dt is None:
                _log(platform_user_id=attribution.platform_user_id, yclients_record_id=record_id, yclients_client_id=client_id, visit_completed=completed, skipped_reason="not_completed")
                continue
            scheduled_at = (visit_dt.astimezone(tz) + timedelta(days=REPEAT_VISIT_DELAY_DAYS)).astimezone(UTC).isoformat()
            event = repo.create_event(
                platform=PLATFORM_MAX,
                platform_user_id=user.platform_user_id,
                yclients_record_id=record_id,
                yclients_client_id=client_id,
                scheduled_at=scheduled_at,
            )
            if event is not None:
                scheduled += 1
                _log(
                    platform_user_id=user.platform_user_id,
                    yclients_record_id=record_id,
                    yclients_client_id=client_id,
                    visit_completed=True,
                    scheduled_at=scheduled_at,
                    due_now=scheduled_at <= now_utc.isoformat(),
                )
    return scheduled


async def process_due_repeat_visit_events(sender: MaxMessageSender, *, database_path: str, limit: int = 100) -> int:
    now_iso = datetime.now(UTC).isoformat()
    await schedule_repeat_visit_events(database_path=database_path)
    repo = RepeatVisitEventsRepository(database_path)
    sent = 0
    for event in repo.find_due(now_iso, limit=limit):
        if await _process_event(sender, database_path=database_path, repository=repo, event=event, now_iso=now_iso):
            sent += 1
    return sent


async def run_repeat_visit_loop(sender: MaxMessageSender, *, database_path: str, stop_event: asyncio.Event, interval_seconds: int, error_callback: object | None = None) -> None:
    while not stop_event.is_set():
        try:
            await process_due_repeat_visit_events(sender, database_path=database_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if callable(error_callback):
                await error_callback(exc)
            else:
                logger.warning("MAX repeat visit diagnostic: error_class=%s", type(exc).__name__, exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(30, interval_seconds))
        except TimeoutError:
            continue


async def _process_event(sender: MaxMessageSender, *, database_path: str, repository: RepeatVisitEventsRepository, event: RepeatVisitEvent, now_iso: str) -> bool:
    history_existing = get_notification_history(database_path, platform=event.platform, platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, notification_type=REPEAT_VISIT_NOTIFICATION_TYPE)
    user = UsersRepository(database_path).find_by_platform_user_id(event.platform_user_id, platform=event.platform)
    if user is None or not user.notifications_enabled:
        _skip(database_path, repository, event, "notifications_disabled" if user else "user_not_found")
        return False
    if history_existing and (history_existing.is_blocked or history_existing.is_stopped):
        _skip(database_path, repository, event, "blocked_or_stopped")
        return False
    visit_completed, has_future = await _verify_yclients(database_path, event, user)
    if not visit_completed:
        _skip(database_path, repository, event, "visit_not_completed")
        return False
    if has_future:
        _skip(database_path, repository, event, "has_future_booking")
        return False
    recent = _has_recent_repeat_history(database_path, event.platform_user_id, now_iso)
    if recent:
        _skip(database_path, repository, event, "skipped_antispam")
        return False
    recipient_type, recipient_id = ("chat", user.chat_id) if user.chat_id else ("user", user.max_user_id or user.platform_user_id)
    try:
        history = await send_business_notification(sender, database_path=database_path, platform=event.platform, platform_user_id=event.platform_user_id, max_user_id=user.max_user_id, chat_id=user.chat_id, yclients_record_id=event.yclients_record_id, yclients_client_id=event.yclients_client_id or user.yclients_client_id, notification_type=REPEAT_VISIT_NOTIFICATION_TYPE, scheduled_for=event.scheduled_at, text=select_repeat_visit_text(), keyboard=repeat_visit_keyboard(), recipient_type=recipient_type, recipient_id=recipient_id, metadata={"source": "repeat_visit"})
    except Exception as exc:  # noqa: BLE001
        repository.set_status(event.id, "failed", skipped_reason=type(exc).__name__)
        _log(platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, yclients_client_id=event.yclients_client_id, visit_completed=visit_completed, has_future_booking=has_future, scheduled_at=event.scheduled_at, due_now=True, history_existing=bool(history_existing), send_attempted=True, send_status="failed", error_class=type(exc).__name__)
        return False
    status = history.status if history else "skipped"
    if status == "sent":
        repository.set_status(event.id, "sent", sent_at=now_iso)
    else:
        repository.set_status(event.id, status, skipped_reason=status)
    _log(platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, yclients_client_id=event.yclients_client_id, visit_completed=visit_completed, has_future_booking=has_future, scheduled_at=event.scheduled_at, due_now=True, history_existing=bool(history_existing), send_attempted=True, send_status=status, blocked_or_stopped=bool(history and (history.is_blocked or history.is_stopped)))
    return status == "sent"


async def _verify_yclients(database_path: str, event: RepeatVisitEvent, user: Any) -> tuple[bool, bool]:
    settings = load_active_yclients_settings(YClientsSettingsRepository(database_path), operation="repeat_visit_verify")
    if not has_required_yclients_credentials(settings):
        return False, False
    tz_name = normalize_branch_timezone(settings.branch_timezone, flow="repeat_visit", operation="repeat_visit_verify")
    now = datetime.now(UTC)
    client_id = event.yclients_client_id or getattr(user, "yclients_client_id", None)
    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=settings.company_id)
        record = _extract_record(await service.get_booking_details(company_id=settings.company_id, yclients_record_id=event.yclients_record_id))
        visit_completed = _record_is_completed(record)
        has_future = False
        if client_id:
            rows = _record_items(await service.get_client_records(company_id=settings.company_id, yclients_client_id=client_id, count=50))
            for item in rows:
                dt = _record_datetime(item, tz_name)
                if dt and dt.astimezone(UTC) > now and not _is_cancelled(item) and not _record_is_completed(item):
                    has_future = True
                    break
        _log(platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, yclients_client_id=client_id, visit_completed=visit_completed, has_future_booking=has_future)
        return visit_completed, has_future


def _skip(database_path: str, repository: RepeatVisitEventsRepository, event: RepeatVisitEvent, reason: str) -> None:
    repository.set_status(event.id, "skipped", skipped_reason=reason)
    mark_notification_history_skipped(database_path, platform=event.platform, platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, notification_type=REPEAT_VISIT_NOTIFICATION_TYPE, scheduled_for=event.scheduled_at, reason=reason, metadata={"source": "repeat_visit"})
    _log(platform_user_id=event.platform_user_id, yclients_record_id=event.yclients_record_id, yclients_client_id=event.yclients_client_id, scheduled_at=event.scheduled_at, due_now=True, send_attempted=False, skipped_reason=reason, blocked_or_stopped=reason == "blocked_or_stopped")


def _has_recent_repeat_history(database_path: str, platform_user_id: str, now_iso: str) -> bool:
    import sqlite3
    from contextlib import closing
    cutoff = (datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(hours=REPEAT_VISIT_ANTISPAM_HOURS)).isoformat()
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute("""
            SELECT 1 FROM notification_history
            WHERE platform = ? AND platform_user_id = ? AND notification_type = ? AND sent_at IS NOT NULL AND sent_at >= ?
            LIMIT 1
        """, (PLATFORM_MAX, platform_user_id, REPEAT_VISIT_NOTIFICATION_TYPE, cutoff)).fetchone()
    return row is not None


def _extract_record(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return payload
    return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}


def _record_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _record_is_completed(record: dict[str, Any]) -> bool:
    attendance = record.get("attendance", record.get("visit_attendance"))
    if attendance is not None:
        return str(attendance).strip() == "1"
    return str(record.get("status") or record.get("record_status") or record.get("state") or "").strip().lower() in _COMPLETED


def _is_cancelled(record: dict[str, Any]) -> bool:
    return "cancel" in str(record.get("status") or record.get("record_status") or record.get("state") or "").lower()


def _record_datetime(record: dict[str, Any], timezone_name: str) -> datetime | None:
    raw = str(record.get("datetime") or record.get("date") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = zoneinfo_or_default(timezone_name, flow="repeat_visit", operation="_record_datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _log(**fields: Any) -> None:
    allowed = {"platform_user_id_present", "yclients_record_id_present", "yclients_client_id_present", "visit_completed", "has_future_booking", "scheduled_at", "due_now", "history_existing", "send_attempted", "send_status", "skipped_reason", "blocked_or_stopped", "error_class"}
    safe = {k: v for k, v in fields.items() if k in allowed}
    safe["platform_user_id_present"] = bool(fields.get("platform_user_id"))
    safe["yclients_record_id_present"] = bool(fields.get("yclients_record_id"))
    safe["yclients_client_id_present"] = bool(fields.get("yclients_client_id"))
    logger.info("MAX repeat visit diagnostic: %s", safe)
