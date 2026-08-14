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
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.repeat_visit_events import RepeatVisitEvent, RepeatVisitEventsRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, User, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.notifications import send_business_notification
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials, load_active_yclients_settings

logger = logging.getLogger(__name__)

REPEAT_VISIT_NOTIFICATION_TYPE = "repeat_visit"
REPEAT_VISIT_DELAY_DAYS = 30
REPEAT_VISIT_ANTISPAM_HOURS = 48
BUTTON_CB_PREFIX = "repeat_visit:book:"
FALLBACK_TEXT = "Пора позаботиться о себе? 😊\n\nПодберём удобное время для следующего визита."
REPEAT_VISIT_TEXTS = [FALLBACK_TEXT]
_COMPLETED = {"visit", "done", "paid", "completed", "show"}


@dataclass(frozen=True)
class RepeatVisitSummary:
    scheduled: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def build_repeat_visit_booking_keyboard(event_id: int) -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([[MaxButton(text="✨ Записаться", payload=f"{BUTTON_CB_PREFIX}{event_id}")]])


def repeat_visit_keyboard(event_id: int | None = None) -> MaxInlineKeyboard:
    return build_repeat_visit_booking_keyboard(event_id or 0)


def select_repeat_visit_text(settings: dict[str, Any] | None = None, *, event_id: int | None = None, user_tg_id: int | str | None = None) -> tuple[int, str]:
    raw = (settings or {}).get("templates")
    if raw is None:
        raw = REPEAT_VISIT_TEXTS
    active: list[tuple[int, str]] = []
    if isinstance(raw, list):
        active = [(idx, str(text).strip()) for idx, text in enumerate(raw, start=1) if str(text or "").strip()]
    logger.info("repeat_visit_text_selection_started available_text_count=%s event_id=%s user_tg_id=%s", len(active), event_id, user_tg_id)
    if not active:
        logger.info("repeat_visit_text_default_used available_text_count=0 event_id=%s user_tg_id=%s", event_id, user_tg_id)
        return 0, FALLBACK_TEXT
    selected_idx, selected_text = random.choice(active)
    logger.info("repeat_visit_text_selected available_text_count=%s selected_text_index=%s event_id=%s user_tg_id=%s", len(active), selected_idx, event_id, user_tg_id)
    return selected_idx, selected_text


async def schedule_repeat_visit_events(*, database_path: str, now: datetime | None = None, limit: int = 500, settings: dict[str, Any] | None = None) -> int:
    """Scan mapped MAX clients and create Telegram-equivalent repeat visit events."""

    cfg = settings if settings is not None else AppSettingsRepository(database_path).get_automation_setting("repeat_visit")
    if cfg.get("enabled") is False:
        logger.info("repeat_visit_scan_skipped_disabled")
        return 0
    yclients_settings = load_active_yclients_settings(YClientsSettingsRepository(database_path), operation="schedule_repeat_visit_events")
    if not has_required_yclients_credentials(yclients_settings):
        return 0
    tz_name = normalize_branch_timezone(yclients_settings.branch_timezone, flow="repeat_visit", operation="schedule_repeat_visit_events")
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    delay_days = int(cfg.get("delay_days", REPEAT_VISIT_DELAY_DAYS))
    cooldown_hours = int(cfg.get("min_interval_hours", REPEAT_VISIT_ANTISPAM_HOURS))
    exclude_future = bool(cfg.get("exclude_has_future_booking", True))
    respect_antispam = bool(cfg.get("respect_anti_spam", True))
    repo = RepeatVisitEventsRepository(database_path)
    users = [user for user in UsersRepository(database_path).list_all_users() if user.platform == PLATFORM_MAX and user.yclients_client_id]
    scheduled = 0
    async with build_yclients_client_from_active_settings(yclients_settings) as client:
        service = YClientsServiceLayer(client, company_id=yclients_settings.company_id)
        for user in users[: max(1, int(limit))]:
            yc_id = str(user.yclients_client_id or "").strip()
            if not user.notifications_enabled:
                scheduled += _create_skip(repo, user, yc_id, None, None, None, None, delay_days, tz_name, "skipped_unsubscribed")
                continue
            try:
                records = _record_items(await service.get_client_records(company_id=yclients_settings.company_id, yclients_client_id=yc_id, count=50))
            except Exception as exc:  # noqa: BLE001 - diagnostics only, keep scan moving.
                logger.warning("MAX repeat visit diagnostic: platform_user_id_present=%s yclients_client_id_present=%s error_class=%s", bool(user.platform_user_id), bool(yc_id), type(exc).__name__)
                continue
            last_done: tuple[dict[str, Any], datetime] | None = None
            future_exists = False
            for rec in records:
                dt = _record_datetime(rec, tz_name)
                if dt is None:
                    continue
                dt_utc = dt.astimezone(UTC)
                if dt_utc > now_utc and not _is_cancelled(rec):
                    future_exists = True
                if _record_is_completed(rec) and (last_done is None or dt_utc > last_done[1]):
                    last_done = (rec, dt_utc)
            if last_done is None:
                continue
            rec, visit_dt = last_done
            visit_id = _clean(rec.get("id"))
            service_id, service_name = _extract_main_service(rec)
            if exclude_future and future_exists:
                scheduled += _create_skip(repo, user, yc_id, rec, visit_dt, service_id, service_name, delay_days, tz_name, "skipped_has_future_booking")
                continue
            if (now_utc - visit_dt).days < delay_days:
                continue
            if repo.has_event_for_visit(user.platform_user_id, visit_id, service_id, platform=PLATFORM_MAX):
                scheduled += _create_skip(repo, user, yc_id, rec, visit_dt, service_id, service_name, delay_days, tz_name, "skipped_duplicate")
                continue
            if respect_antispam and repo.has_recent_sent(
                user.platform_user_id,
                cooldown_hours,
                platform=PLATFORM_MAX,
                now=now_utc,
            ):
                scheduled += _create_skip(repo, user, yc_id, rec, visit_dt, service_id, service_name, delay_days, tz_name, "skipped_antispam")
                continue
            template_idx, template_text = select_repeat_visit_text(cfg, user_tg_id=user.platform_user_id)
            scheduled_at = now_utc.isoformat()
            event = repo.create_event(
                platform=PLATFORM_MAX,
                platform_user_id=user.platform_user_id,
                yclients_record_id=visit_id,
                yclients_client_id=yc_id,
                yclients_visit_id=visit_id,
                yclients_service_id=service_id,
                service_name=service_name,
                last_visit_datetime_utc=visit_dt.isoformat(),
                delay_days=delay_days,
                scheduled_at=scheduled_at,
                scheduled_send_at_utc=scheduled_at,
                selected_template_index=template_idx,
                selected_template_text=template_text,
                status="pending",
                branch_timezone=tz_name,
                source="yclients",
                is_test=False,
            )
            if event is not None:
                scheduled += 1
    return scheduled


async def process_due_repeat_visit_events(sender: MaxMessageSender, *, database_path: str, limit: int = 100, settings: dict[str, Any] | None = None) -> int:
    now_iso = datetime.now(UTC).isoformat()
    cfg = settings if settings is not None else AppSettingsRepository(database_path).get_automation_setting("repeat_visit")
    await schedule_repeat_visit_events(database_path=database_path, settings=cfg)
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
    user = UsersRepository(database_path).find_by_platform_user_id(event.platform_user_id, platform=event.platform)
    if user is None:
        repository.mark_status(event.id, "skipped_no_telegram")
        return False
    if not user.notifications_enabled:
        repository.mark_status(event.id, "skipped_unsubscribed")
        return False
    recipient_type, recipient_id = ("chat", user.chat_id) if user.chat_id else ("user", user.max_user_id or user.platform_user_id)
    text = event.selected_template_text or FALLBACK_TEXT
    try:
        history = await send_business_notification(
            sender,
            database_path=database_path,
            platform=event.platform,
            platform_user_id=event.platform_user_id,
            max_user_id=user.max_user_id,
            chat_id=user.chat_id,
            yclients_record_id=event.yclients_visit_id or event.yclients_record_id or str(event.id),
            yclients_client_id=event.yclients_client_id or user.yclients_client_id,
            notification_type=REPEAT_VISIT_NOTIFICATION_TYPE,
            scheduled_for=event.scheduled_send_at_utc or event.scheduled_at,
            text=text,
            keyboard=build_repeat_visit_booking_keyboard(event.id),
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            metadata={"source": event.source or "repeat_visit", "repeat_visit_event_id": event.id, "is_test": event.is_test},
        )
    except Exception as exc:  # noqa: BLE001
        repository.mark_status(event.id, "failed", error_summary=_safe_error(exc))
        return False
    status = history.status if history else "failed"
    if status == "sent":
        repository.mark_status(event.id, "sent", sent=True)
        return True
    if status == "blocked":
        repository.mark_status(event.id, "blocked", error_summary="blocked")
        return False
    repository.mark_status(event.id, "failed", error_summary=status)
    return False


def _create_skip(repo: RepeatVisitEventsRepository, user: User, yc_id: str, rec: dict[str, Any] | None, visit_dt: datetime | None, service_id: str | None, service_name: str | None, delay_days: int, tz_name: str, status: str) -> int:
    visit_id = _clean((rec or {}).get("id")) or f"skip-{status}-{user.platform_user_id}-{datetime.now(UTC).timestamp()}"
    # Keep Telegram dedup fields exact while avoiding old MAX unique(platform,user,record) conflicts for skip audit rows.
    record_id = visit_id if status not in {"skipped_duplicate", "skipped_has_future_booking", "skipped_antispam", "skipped_unsubscribed"} else f"{visit_id}:{status}:{datetime.now(UTC).timestamp()}"
    event = repo.create_event(
        platform=PLATFORM_MAX,
        platform_user_id=user.platform_user_id,
        yclients_record_id=record_id,
        yclients_client_id=yc_id,
        yclients_visit_id=visit_id,
        yclients_service_id=service_id,
        service_name=service_name,
        last_visit_datetime_utc=visit_dt.isoformat() if visit_dt else None,
        delay_days=delay_days,
        scheduled_at=datetime.now(UTC).isoformat(),
        scheduled_send_at_utc=datetime.now(UTC).isoformat(),
        status=status,
        branch_timezone=tz_name,
        source="yclients",
    )
    return 1 if event is not None else 0


def _record_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _record_is_completed(record: dict[str, Any]) -> bool:
    attendance = record.get("attendance")
    if attendance is None:
        attendance = record.get("visit_attendance")
    if attendance is not None:
        return str(attendance).strip() == "1"
    status = str(record.get("status") or "").strip().lower()
    return status in _COMPLETED


def _is_cancelled(record: dict[str, Any]) -> bool:
    return "cancel" in str(record.get("status") or "").lower()


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
    return parsed.astimezone(UTC)


def _extract_main_service(rec: dict[str, Any]) -> tuple[str | None, str | None]:
    services = rec.get("services")
    if isinstance(services, list) and services:
        first = services[0] if isinstance(services[0], dict) else {}
        return _clean(first.get("id")), _clean(first.get("title") or first.get("name"))
    return None, _clean(rec.get("service_name") or rec.get("service"))


def _safe_error(exc: BaseException) -> str:
    return str(exc or type(exc).__name__)[:180]


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
