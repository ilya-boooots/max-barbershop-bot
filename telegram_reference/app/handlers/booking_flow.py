from __future__ import annotations

import asyncio
import logging
import re
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.core.action_locks import acquire_action_lock, release_action_lock
from app.core.navigation import clear_state_preserving_navigation, render_main_menu
from app.core.ui_texts import BOOK_APPOINTMENT_BTN
from app.db.booking_links_repo import create_booking_link
from app.db.telegram_attribution_repo import upsert_telegram_attribution
from app.repositories.notification_attributions import find_last_click, has_booking_attribution, log_click, mark_attributed
from app.integrations.yclients import (
    YClientsAuthError,
    YClientsClient,
    YClientsCredentialsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsUnavailableError,
    build_yclients_client,
    get_staff_for_service,
    get_yclients_credentials,
    notify_yclients_exception,
)
from app.integrations.yclients.endpoints import create_booking_or_visit, get_company
from app.integrations.yclients.endpoints import get_services, get_staff, list_service_categories
from app.integrations.yclients.client import YClientsResponse
from app.repositories.master_photos import get_master_photo
from app.repositories.users import get_user
from app.services.company_time import parse_yclients_datetime, resolve_company_timezone
from app.services.contacts import resolve_contacts_for_company
from app.services.birthday_funnel import BIRTHDAY_WARNING, apply_birthday_warning
from app.services.anti_spam import record_delivery_decision
from app.repositories.birthday_funnel_events import mark_status as mark_birthday_status
from app.ui.buttons import BACK, HOME

router = Router()
logger = logging.getLogger(__name__)

CB_PREFIX = "book_flow"
CB_CATEGORY = f"{CB_PREFIX}:cat"
CB_SERVICE = f"{CB_PREFIX}:srv"
CB_STAFF = f"{CB_PREFIX}:stf"
CB_STAFF_ANY = f"{CB_PREFIX}:stf:any"
CB_PAGE_CATEGORY = f"{CB_PREFIX}:page:cat"
CB_PAGE_SERVICE = f"{CB_PREFIX}:page:srv"
CB_PAGE_STAFF = f"{CB_PREFIX}:page:stf"
CB_DATE = f"{CB_PREFIX}:date"
CB_WEEK = f"{CB_PREFIX}:week"
CB_TIME = f"{CB_PREFIX}:time"
CB_PAGE_TIME = f"{CB_PREFIX}:page:time"
CB_BACK = f"{CB_PREFIX}:back"
CB_HOME = f"{CB_PREFIX}:home"
CB_CONFIRM_FINAL = f"{CB_PREFIX}:confirm:final"
CB_CONFIRM_CANCEL = f"{CB_PREFIX}:confirm:cancel"
CB_MY_BOOKINGS = f"{CB_PREFIX}:my_bookings"
CB_PHONE_BACK = f"{CB_PREFIX}:phone:back"
CB_HUB_STAFF = f"{CB_PREFIX}:hub:staff"
CB_HUB_DATETIME = f"{CB_PREFIX}:hub:datetime"
CB_HUB_SERVICE = f"{CB_PREFIX}:hub:service"
CB_PREF_DATE = f"{CB_PREFIX}:pref:date"
CB_PREF_TIME = f"{CB_PREFIX}:pref:time"
BOOKING_USE_REGISTERED_PHONE = "📲 Использовать номер из регистрации"

PAGE_SIZE = 8
TIME_PAGE_SIZE = 15
DATE_PAGE_SIZE = 10
DATE_LOOKAHEAD_DAYS = 28
CACHE_TTL_S = 90
DATE_SLOTS_CACHE_TTL_S = 180
STAFF_AVAILABILITY_CACHE_TTL_S = 300
YCLIENTS_SLOT_CALL_TIMEOUT_S = 9.0
YCLIENTS_SLOT_TOTAL_TIMEOUT_S = 15.0
YCLIENTS_SLOT_CONCURRENCY = 4
CONFIRM_LOCK_SECONDS = 5
DEV_DIAGNOSTICS_TG_ID = 378881880
BOOKING_COMMENT_PREFIX = "Клиент записался из телеграм бота"
ALLOWED_STAFF_SOURCES = {"service_payload", "staff_endpoint", "intersection", "availability_filtered"}
FORBIDDEN_STAFF_FALLBACK_SOURCES = {"all_staff", "unfiltered_staff", "cache_all_staff", "fallback_all_staff"}
NO_ASSIGNED_STAFF_TEXT = "😕 К сожалению, сейчас нет свободных мастеров на эту услугу.\nВыберите, пожалуйста, другую 🙂"
try:
    PROJECT_TZ = ZoneInfo("Europe/Samara")
except Exception:
    PROJECT_TZ = timezone(timedelta(hours=4))


@dataclass(frozen=True)
class ServiceItem:
    id: str
    name: str
    category_id: str
    category_name: str
    price: str | None
    duration: str | None


@dataclass(frozen=True)
class StaffItem:
    id: str
    name: str
    specialization: str | None
    rating: str | None


@dataclass(frozen=True)
class SlotItem:
    time: str
    datetime_iso: str | None


@dataclass(frozen=True)
class StaffResolution:
    staff_list: list[StaffItem]
    supports_any_master: bool
    source: str
    service_payload_count: int | None
    endpoint_count: int
    service_payload_snippet: str
    endpoint_payload_snippet: str
    trace_id: str


class BookingFlowStates(StatesGroup):
    BOOKING_HUB = State()
    CHOOSE_PREF_DATE = State()
    CHOOSE_PREF_TIME = State()
    CHOOSE_CATEGORY = State()
    CHOOSE_SERVICE = State()
    CHOOSE_STAFF = State()
    CHOOSE_DATE = State()
    CHOOSE_TIME = State()
    WAIT_PHONE = State()
    CONFIRM_PHONE = State()


_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_SERVICE_RAW_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _cache_get(key: tuple[str, str]) -> Any | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: tuple[str, str], payload: Any, *, ttl_s: int | float = CACHE_TTL_S) -> None:
    _CACHE[key] = (time.monotonic() + ttl_s, payload)


RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _today() -> date:
    return datetime.now(PROJECT_TZ).date()


async def _company_now(company_id: str) -> datetime:
    try:
        tz_context = await resolve_company_timezone(company_id)
        tz = ZoneInfo(tz_context.timezone_name)
    except Exception:
        tz = PROJECT_TZ
    return datetime.now(tz)


async def _today_for_company(company_id: str) -> date:
    return (await _company_now(company_id)).date()


def _slot_is_future_for_company_day(slot: SlotItem, *, iso_date: str, now: datetime) -> bool:
    parsed: datetime | None = None
    if slot.datetime_iso:
        try:
            parsed = datetime.fromisoformat(str(slot.datetime_iso).replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=now.tzinfo)
            return parsed.astimezone(now.tzinfo) > now
    try:
        slot_dt = datetime.fromisoformat(f"{iso_date}T{slot.time}:00").replace(tzinfo=now.tzinfo)
    except ValueError:
        return False
    return slot_dt > now


async def _build_booking_bot_comment_tag(company_id: str, now_dt: datetime | None = None) -> str:
    tz_context = await resolve_company_timezone(company_id)
    company_tz = ZoneInfo(tz_context.timezone_name)
    dt = now_dt or datetime.now(company_tz)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=company_tz)
    local_dt = dt.astimezone(company_tz)
    return f"{BOOKING_COMMENT_PREFIX} {local_dt.strftime('%d.%m.%Y')} в {local_dt.strftime('%H:%M')}"


def _append_lost_client_discount_comment(base_comment: str, *, booking_origin_type: str | None, lost_days: int | None) -> str:
    if booking_origin_type != "lost_client" or not isinstance(lost_days, int) or lost_days not in {30, 60, 90}:
        return base_comment
    warning = f"Клиент не посещал {lost_days} дней. НУЖНО СДЕЛАТЬ СКИДКУ"
    if warning in base_comment:
        return base_comment
    return f"{base_comment}\n{warning}" if base_comment else warning


def _date_label(value: date) -> str:
    return f"{RU_WEEKDAYS[value.weekday()]} {value.strftime('%d.%m')}"


def _normalize_slot_time(value: Any) -> str | None:
    raw = _safe_str(value)
    if not raw:
        return None
    for sep in ("T", " "):
        if sep in raw:
            raw = raw.split(sep, 1)[1]
    raw = raw[:5]
    if len(raw) == 5 and raw[2] == ":" and raw.replace(":", "").isdigit():
        return raw
    return None


def _extract_slots(payload: dict[str, Any] | list[Any]) -> list[SlotItem]:
    candidates: list[Any]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict) and isinstance(data.get("times"), list):
            candidates = data["times"]
        else:
            candidates = []
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []

    slots: list[SlotItem] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            dt_iso = _safe_str(item.get("datetime") or item.get("date") or item.get("time")) or None
            slot_time = _normalize_slot_time(item.get("time") or item.get("datetime") or item.get("date"))
        else:
            dt_iso = _safe_str(item) or None
            slot_time = _normalize_slot_time(item)
        if not slot_time or slot_time in seen:
            continue
        seen.add(slot_time)
        slots.append(SlotItem(time=slot_time, datetime_iso=dt_iso))
    return sorted(slots, key=lambda s: s.time)


async def _get_company_context() -> tuple[str, bool]:
    credentials, _ = await get_yclients_credentials()
    return credentials.company_id, bool(credentials.user_token)


async def _build_client() -> tuple[YClientsClient, str]:
    return await build_yclients_client()


def _extract_data(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_yclients_id(value: Any) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    if raw.isdigit():
        return str(int(raw))
    if re.fullmatch(r"\d+\.0+", raw):
        return str(int(raw.split(".", 1)[0]))
    return raw


def _log_booking_issue(
    label: str,
    *,
    user_id: int | None = None,
    entry_mode: str | None = None,
    staff_id: str | None = None,
    service_id: str | None = None,
    selected_datetime: str | None = None,
    callback_data: str | None = None,
    endpoint: str | None = None,
    method: str | None = None,
    summary: str | None = None,
) -> None:
    logger.warning(
        "%s tg_user_id=%s entry_mode=%s staff_id=%s service_id=%s selected_datetime=%s callback_data=%s endpoint=%s method=%s summary=%s",
        label,
        user_id or "n/a",
        entry_mode or "n/a",
        staff_id or "n/a",
        service_id or "n/a",
        selected_datetime or "n/a",
        callback_data or "n/a",
        endpoint or "n/a",
        method or "n/a",
        summary or "n/a",
    )


def _extract_duration(service: dict[str, Any]) -> str | None:
    value = service.get("duration") or service.get("seance_length") or service.get("length")
    if value is None:
        return None
    try:
        normalized = int(float(value))
    except (ValueError, TypeError):
        return None
    if normalized <= 0:
        return None
    minutes = ceil(normalized / 60) if normalized > 600 and normalized % 60 == 0 else normalized
    return f"{minutes} мин"


def _extract_price(service: dict[str, Any]) -> str | None:
    price = service.get("price_min") or service.get("price") or service.get("cost")
    if price in (None, ""):
        return None
    return f"{price} ₽"


def _extract_staff_specialization(staff: dict[str, Any]) -> str | None:
    value = staff.get("specialization") or staff.get("position") or staff.get("post")
    text = _safe_str(value)
    return text or None


def _extract_staff_rating(staff: dict[str, Any]) -> str | None:
    value = _safe_str(staff.get("rating"))
    return value or None



def _is_empty_or_nogroup(category_id: str, category_name: str) -> bool:
    if not category_name.strip():
        return True
    if category_name.strip().lower() == "без группы":
        return True
    if category_id.strip() in {"", "0", "none", "null"}:
        return True
    return False


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_str(value).lower() in {"1", "true", "yes", "y", "да"}


def _is_staff_payload_active(staff: dict[str, Any]) -> bool:
    """Keep YClients staff unless the payload explicitly marks them deleted/fired.

    The booking flow must not drop masters because of optional display flags like
    ``hidden``.  Only clear inactive/deleted/fired markers are treated as
    unavailable for online booking.
    """

    for key in ("is_fired", "fired", "is_deleted", "deleted"):
        if key in staff and _truthy(staff.get(key)):
            return False
    status = _safe_str(staff.get("status")).lower()
    if status in {"deleted", "fired", "inactive", "blocked", "dismissed"}:
        return False
    return True


def _extract_staff_id_from_payload(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "staff_id", "employee_id", "master_id", "specialist_id", "user_id"):
            normalized = _normalize_yclients_id(value.get(key))
            if normalized:
                return normalized
        for key in ("staff", "employee", "master", "specialist", "user"):
            nested = value.get(key)
            if isinstance(nested, dict):
                normalized = _extract_staff_id_from_payload(nested)
                if normalized:
                    return normalized
        return ""
    return _normalize_yclients_id(value)


def _extract_service_id_from_payload(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "service_id"):
            normalized = _normalize_yclients_id(value.get(key))
            if normalized:
                return normalized
        service = value.get("service")
        if isinstance(service, dict):
            return _extract_service_id_from_payload(service)
        return ""
    return _normalize_yclients_id(value)


def _iter_list_or_scalar(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [raw]


def _extract_staff_service_ids(staff: dict[str, Any]) -> set[str]:
    service_ids: set[str] = set()
    for key in ("service_id", "service_ids", "services", "service", "assigned_services"):
        for item in _iter_list_or_scalar(staff.get(key)):
            service_id = _extract_service_id_from_payload(item)
            if service_id:
                service_ids.add(service_id)
    return service_ids


def _extract_assigned_staff_rows_from_service(service: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("staff", "staffs", "employees", "masters", "staff_members", "personnel", "specialists"):
        raw = service.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            nested = None
            for nested_key in ("staff", "employee", "master", "specialist", "user"):
                if isinstance(item.get(nested_key), dict):
                    nested = item[nested_key]
                    break
            staff_row = {**item, **nested} if isinstance(nested, dict) else item
            staff_id = _extract_staff_id_from_payload(staff_row)
            if staff_id and staff_id not in seen:
                seen.add(staff_id)
                rows.append(staff_row)
    return rows


def _extract_assigned_staff_ids_from_service(service: dict[str, Any]) -> set[str] | None:
    explicit_empty_keys = (
        "staff_count",
        "employees_count",
        "specialists_count",
        "master_count",
    )
    for key in explicit_empty_keys:
        value = service.get(key)
        if value is None:
            continue
        try:
            if int(value) == 0:
                return set()
        except (TypeError, ValueError):
            pass

    assigned: set[str] = set()
    found_assignment_field = False
    for key in (
        "staff",
        "staffs",
        "employees",
        "masters",
        "staff_members",
        "personnel",
        "specialists",
        "staff_ids",
        "staff_ids[]",
        "employee_ids",
        "master_ids",
        "specialist_ids",
    ):
        if key not in service:
            continue
        found_assignment_field = True
        for item in _iter_list_or_scalar(service.get(key)):
            staff_id = _extract_staff_id_from_payload(item)
            if staff_id:
                assigned.add(staff_id)

    if found_assignment_field:
        return assigned
    return None


def _payload_snippet(payload: dict[str, Any] | list[Any], *, limit: int = 450) -> str:
    try:
        import json

        raw = json.dumps(payload, ensure_ascii=False)
    except Exception:
        raw = str(payload)
    compact = " ".join(raw.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}…"


def _supports_any_master(payload: dict[str, Any] | list[Any]) -> bool:
    if isinstance(payload, list):
        return any(_supports_any_master(item) for item in payload if isinstance(item, dict))
    if isinstance(payload, dict):
        for key in ("any_master", "allow_any_master", "can_choose_any_master"):
            value = payload.get(key)
            if isinstance(value, bool):
                return value
            if str(value).lower() in {"1", "true"}:
                return True
    return False


def _is_service_online_bookable(service: dict[str, Any]) -> bool:
    online_keys = (
        "is_online",
        "online",
        "booking_online",
        "is_booking_online",
        "is_bookable",
        "is_active",
        "active",
    )
    for key in online_keys:
        value = service.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    return True


def _service_visibility_decision(service: dict[str, Any]) -> tuple[bool, str]:
    if not _is_service_online_bookable(service):
        return False, "online_disabled_hide"

    assigned_from_payload = _extract_assigned_staff_ids_from_service(service)
    if assigned_from_payload is None:
        return True, "ambiguous_show"
    if len(assigned_from_payload) == 0:
        return False, "explicit_zero_hide"

    explicit_count: int | None = None
    for key in ("staff_count", "employees_count", "specialists_count", "master_count"):
        value = service.get(key)
        if value is None:
            continue
        try:
            explicit_count = int(value)
            break
        except (TypeError, ValueError):
            continue

    if explicit_count is not None and explicit_count == len(assigned_from_payload):
        return True, "full_show"
    return True, "subset_show"



def _staff_item_from_payload(staff: dict[str, Any], *, fallback_id: str | None = None) -> StaffItem | None:
    staff_id = _extract_staff_id_from_payload(staff) or _normalize_yclients_id(fallback_id)
    if not staff_id:
        return None
    name = _safe_str(
        staff.get("name")
        or staff.get("fullname")
        or staff.get("full_name")
        or staff.get("title")
        or staff.get("display_name")
    ) or f"Мастер #{staff_id}"
    return StaffItem(
        id=staff_id,
        name=name,
        specialization=_extract_staff_specialization(staff),
        rating=_extract_staff_rating(staff),
    )


async def _resolve_assigned_staff(company_id: str, service: dict[str, Any]) -> StaffResolution:
    service_id = _normalize_yclients_id(service.get("id"))
    service_name = _safe_str(service.get("title") or service.get("name")) or "n/a"
    service_assigned_ids = _extract_assigned_staff_ids_from_service(service)
    service_payload_count = None if service_assigned_ids is None else len(service_assigned_ids)
    service_staff_rows = _extract_assigned_staff_rows_from_service(service)
    service_staff_by_id = {
        _extract_staff_id_from_payload(row): row
        for row in service_staff_rows
        if _extract_staff_id_from_payload(row)
    }

    endpoint_rows = await get_staff_for_service(company_id, service_id)
    active_endpoint_rows = [row for row in endpoint_rows if _is_staff_payload_active(row)]
    endpoint_by_id = {
        _extract_staff_id_from_payload(row): row
        for row in active_endpoint_rows
        if _extract_staff_id_from_payload(row)
    }
    endpoint_count = len(active_endpoint_rows)

    resolved_by_id: dict[str, StaffItem] = {}
    source = "staff_endpoint"
    if service_assigned_ids is not None:
        endpoint_matches = 0
        service_payload_matches = 0
        for staff_id in sorted(service_assigned_ids):
            row = endpoint_by_id.get(staff_id)
            if row is not None:
                endpoint_matches += 1
            else:
                row = service_staff_by_id.get(staff_id)
                if row is not None and not _is_staff_payload_active(row):
                    row = None
                if row is not None:
                    service_payload_matches += 1
            item = _staff_item_from_payload(row or {"id": staff_id}, fallback_id=staff_id)
            if item is not None:
                resolved_by_id[item.id] = item
        if endpoint_matches and service_payload_matches:
            source = "intersection"
        else:
            source = "service_payload" if service_payload_matches or service_assigned_ids else "service_payload"
    else:
        for row in active_endpoint_rows:
            service_ids = _extract_staff_service_ids(row)
            if service_ids and service_id not in service_ids:
                continue
            item = _staff_item_from_payload(row)
            if item is not None:
                resolved_by_id[item.id] = item
        source = "staff_endpoint"

    resolved_staff = sorted(resolved_by_id.values(), key=lambda row: row.name.lower())
    supports_any_master = bool(resolved_staff) and (_supports_any_master(endpoint_rows) or _supports_any_master(service))
    return StaffResolution(
        staff_list=resolved_staff,
        supports_any_master=supports_any_master,
        source=source,
        service_payload_count=service_payload_count,
        endpoint_count=endpoint_count,
        service_payload_snippet=_payload_snippet(service),
        endpoint_payload_snippet=_payload_snippet(endpoint_rows),
        trace_id=f"staff-resolve-{time.time_ns()}",
    )


async def _notify_staff_resolution_diagnostics(
    callback: CallbackQuery,
    *,
    category_id: str,
    service_id: str,
    service_name: str,
    resolution: StaffResolution,
    reason: str,
) -> None:
    try:
        await callback.bot.send_message(
            DEV_DIAGNOSTICS_TG_ID,
            (
                "🚨 Booking flow: диагностика выбора мастеров по услуге\n"
                f"🧩 trace_id: {resolution.trace_id}\n"
                f"🪤 reason: {reason}\n"
                f"🗂 category_id: {category_id or 'n/a'}\n"
                f"🆔 service_id: {service_id or 'n/a'}\n"
                f"✂️ service_name: {service_name or 'n/a'}\n"
                f"📦 source: {resolution.source}\n"
                f"👥 assigned_count: {len(resolution.staff_list)}\n"
                f"📊 service_payload_count: {resolution.service_payload_count if resolution.service_payload_count is not None else 'n/a'}\n"
                f"📊 staff_endpoint_count: {resolution.endpoint_count}\n"
                f"📄 service_payload: {resolution.service_payload_snippet}\n"
                f"📄 staff_endpoint_payload: {resolution.endpoint_payload_snippet}"
            ),
        )
    except Exception:
        logger.exception("Failed to send staff resolution diagnostics")



async def _notify_staff_for_service_failure(
    callback: CallbackQuery,
    *,
    service_id: str,
    company_id: str,
    exc: Exception,
) -> None:
    trace_id = getattr(exc, "trace_id", None) or "n/a"
    endpoint = getattr(exc, "endpoint", None) or "/api/v1/company/{company_id}/staff?service_ids[]=..."
    status = getattr(exc, "status_code", None)
    snippet = (getattr(exc, "response_snippet", None) or str(exc) or "—")[:1000]
    text = (
        "🚨 YClients: ошибка получения мастеров для услуги\n"
        f"🧩 trace_id: {trace_id}\n"
        f"➡️ endpoint: {endpoint}\n"
        f"📡 status: {status or 'n/a'}\n"
        f"🆔 service_id: {service_id or 'n/a'}\n"
        f"🏢 company_id: {company_id or 'n/a'}\n"
        f"📄 response: {snippet}"
    )
    try:
        await callback.bot.send_message(DEV_DIAGNOSTICS_TG_ID, text)
    except Exception:
        logger.exception("Failed to send staff-for-service diagnostics")


def _log_service_visibility_decision(
    *,
    category_id: str,
    service_id: str,
    service_name: str,
    reason: str,
) -> None:
    logger.info(
        "booking_service_visibility category_id=%s service_id=%s service_name=%s reason=%s",
        category_id or "n/a",
        service_id or "n/a",
        service_name or "n/a",
        reason,
    )


def _decode_unicode(value: str) -> str:
    try:
        return value.encode("utf-8").decode("unicode_escape")
    except Exception:
        return value


def _book_times_endpoint(company_id: str, staff_id: str | None, iso_date: str, service_id: str | None = None) -> str:
    normalized_staff_id = staff_id or "0"
    suffix = f"?service_ids[]={service_id}" if service_id else ""
    return f"/api/v1/book_times/{company_id}/{normalized_staff_id}/{iso_date}{suffix}"


async def _notify_book_times_failure(
    callback: CallbackQuery,
    *,
    service_id: str,
    staff_id: str | None,
    iso_date: str,
    company_id: str,
    exc: Exception | None = None,
    title: str = "🚨 YClients: ошибка получения слотов/времени",
    trace_id: str | None = None,
    endpoint: str | None = None,
    status: int | None = None,
    response_snippet: str | None = None,
) -> None:
    resolved_trace_id = trace_id or getattr(exc, "trace_id", None) or "n/a"
    resolved_endpoint = endpoint or getattr(exc, "endpoint", None) or _book_times_endpoint(company_id, staff_id, iso_date, service_id)
    resolved_status = status if status is not None else getattr(exc, "status_code", None)
    snippet_source = response_snippet
    if not snippet_source and exc is not None:
        snippet_source = getattr(exc, "response_snippet", None) or str(exc)
    snippet = _decode_unicode((snippet_source or "—")[:1000])
    text = (
        f"{title}\n"
        f"🧩 trace_id: {resolved_trace_id}\n"
        f"➡️ endpoint: {resolved_endpoint}\n"
        f"📡 status: {resolved_status or 'n/a'}\n"
        f"🆔 service_id: {service_id or 'n/a'}\n"
        f"💈 staff_id: {staff_id or 'any'}\n"
        f"📅 date: {iso_date}\n"
        f"🏢 company_id: {company_id or 'n/a'}\n"
        f"📄 response: {snippet}"
    )
    try:
        await callback.bot.send_message(DEV_DIAGNOSTICS_TG_ID, text)
    except Exception:
        logger.exception("Failed to send book_times diagnostics")


async def _reply_booking_step_error(target: Message | CallbackQuery) -> None:
    text = "⚠️ Ошибка шага записи. Начните заново 🙂"
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=HOME, callback_data=CB_HOME)]])
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=reply_markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=reply_markup)

async def _ensure_filtered_staff_source(callback: CallbackQuery, state: FSMContext, staff_list: list[StaffItem]) -> bool:
    data = await state.get_data()
    source = _safe_str(data.get("book_staff_source"))
    if not staff_list or source in ALLOWED_STAFF_SOURCES or source.startswith("cache:"):
        return True

    trace_id = f"staff-source-{time.time_ns()}"
    selected_service_id = _safe_str(data.get("selected_service_id")) or "n/a"
    selected_category_id = _safe_str(data.get("selected_category_id")) or "n/a"
    source_bucket = source if source in FORBIDDEN_STAFF_FALLBACK_SOURCES else "other"
    logger.error("booking_staff_source_violation source=%s size=%s", source or "n/a", len(staff_list))
    try:
        await callback.bot.send_message(
            DEV_DIAGNOSTICS_TG_ID,
            (
                "🚨 Booking flow: попытка показать неотфильтрованный список мастеров\n"
                f"🧩 trace_id: {trace_id}\n"
                f"📦 source: {source or 'n/a'}\n"
                f"🪤 source_bucket: {source_bucket}\n"
                f"👥 staff_count: {len(staff_list)}\n"
                f"🆔 service_id: {selected_service_id}\n"
                f"🗂 category_id: {selected_category_id}"
            ),
        )
    except Exception:
        logger.exception("Failed to send staff source violation diagnostics")

    if callback.message:
        await callback.message.edit_text(
            "⚠️ Техническая ошибка. Попробуйте ещё раз.",
            reply_markup=_build_common_only_keyboard(),
        )
    await callback.answer()
    return False


def _normalize_services(raw_services: list[dict[str, Any]]) -> list[ServiceItem]:
    services: list[ServiceItem] = []
    for item in raw_services:
        service_id = _normalize_yclients_id(item.get("id"))
        service_name = _safe_str(item.get("title") or item.get("name"))
        if not service_id or not service_name:
            continue
        category_id = _safe_str(item.get("category_id") or item.get("category") or "other") or "other"
        category_name = _safe_str(item.get("category_title") or item.get("category_name") or "Другое") or "Другое"
        services.append(
            ServiceItem(
                id=service_id,
                name=service_name,
                category_id=category_id,
                category_name=category_name,
                price=_extract_price(item),
                duration=_extract_duration(item),
            )
        )
    return services


def _normalize_staff(raw_staff: list[dict[str, Any]], selected_service_id: str) -> list[StaffItem]:
    selected_service_id = _normalize_yclients_id(selected_service_id)
    staff: list[StaffItem] = []
    for item in raw_staff:
        staff_id = _extract_staff_id_from_payload(item)
        name = _safe_str(item.get("name") or item.get("fullname") or item.get("title"))
        if not staff_id or not name:
            continue

        service_ids = _extract_staff_service_ids(item)
        if service_ids and selected_service_id not in service_ids:
            continue

        staff.append(
            StaffItem(
                id=staff_id,
                name=name,
                specialization=_extract_staff_specialization(item),
                rating=_extract_staff_rating(item),
            )
        )

    return sorted(staff, key=lambda row: row.name)


async def _load_services(company_id: str) -> list[ServiceItem]:
    key = (company_id, "services")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    client, cid = await _build_client()
    try:
        payload = await get_services(client, company_id=cid)
    finally:
        await client.close()

    raw_services = _extract_data(payload)
    filtered_raw_services: list[dict[str, Any]] = []
    for service in raw_services:
        should_show, reason = _service_visibility_decision(service)
        service_id = _safe_str(service.get("id"))
        service_name = _safe_str(service.get("title") or service.get("name"))
        category_id = _safe_str(service.get("category_id") or service.get("category"))
        _log_service_visibility_decision(
            category_id=category_id,
            service_id=service_id,
            service_name=service_name,
            reason=reason,
        )
        if should_show:
            filtered_raw_services.append(service)

    services = _normalize_services(filtered_raw_services)
    for raw_service in filtered_raw_services:
        raw_id = _normalize_yclients_id(raw_service.get("id"))
        if raw_id:
            _SERVICE_RAW_CACHE[(company_id, raw_id)] = raw_service
    _cache_set(key, services)
    return services


async def _load_staff_service_map(company_id: str) -> dict[str, set[str]]:
    key = (company_id, "staff_service_map")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    client, cid = await _build_client()
    try:
        payload = await get_staff(client, company_id=cid)
    finally:
        await client.close()

    rows = _extract_data(payload)
    mapping: dict[str, set[str]] = {}
    for row in rows:
        staff_id = _extract_staff_id_from_payload(row)
        if not staff_id:
            continue
        mapping[staff_id] = _extract_staff_service_ids(row)
    _cache_set(key, mapping)
    return mapping


async def _get_valid_services_for_context(
    company_id: str,
    data: dict[str, Any],
    services: list[ServiceItem],
    *,
    filter_staff_availability: bool = False,
) -> list[ServiceItem]:
    selected_staff_id = _normalize_yclients_id(data.get("selected_staff_id"))
    selected_date = _safe_str(data.get("selected_date"))
    selected_time = _safe_str(data.get("selected_time"))
    entry_mode = _safe_str(data.get("entry_mode"))

    valid_by_staff: list[ServiceItem] = services
    if selected_staff_id:
        staff_service_map = await _load_staff_service_map(company_id)
        selected_staff_service_ids = staff_service_map.get(selected_staff_id, set())
        valid_by_staff = []
        for service in services:
            raw_service = _SERVICE_RAW_CACHE.get((company_id, service.id)) or {}
            assigned_staff_ids = _extract_assigned_staff_ids_from_service(raw_service)
            if assigned_staff_ids is not None:
                if selected_staff_id in assigned_staff_ids:
                    valid_by_staff.append(service)
                continue
            if service.id in selected_staff_service_ids:
                valid_by_staff.append(service)

    if entry_mode != "datetime_first" or not selected_date or not selected_time:
        if selected_staff_id and filter_staff_availability:
            return await _filter_services_with_staff_availability(
                company_id,
                staff_id=selected_staff_id,
                services=valid_by_staff,
            )
        return valid_by_staff

    semaphore = asyncio.Semaphore(YCLIENTS_SLOT_CONCURRENCY)

    async def service_has_selected_time(service: ServiceItem) -> tuple[ServiceItem, bool]:
        async with semaphore:
            try:
                slots = await _load_slots(company_id, service_id=service.id, staff_id=selected_staff_id or None, iso_date=selected_date)
            except Exception:
                logger.exception(
                    "booking_slots_load_failed company_id=%s selected_date=%s service_id=%s staff_id=%s error_summary=service_filter_failed",
                    company_id, selected_date, service.id, selected_staff_id or "0",
                )
                raise
            return service, any(slot.time == selected_time for slot in slots)

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(service_has_selected_time(service) for service in valid_by_staff)),
            timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout company_id=%s selected_date=%s service_id=n/a staff_id=%s error_summary=service_filter_timeout",
            company_id, selected_date, selected_staff_id or "0",
        )
        return []
    return [service for service, valid in results if valid]


async def _is_service_compatible_with_staff(company_id: str, service_id: str, staff_id: str) -> bool:
    service_id = _normalize_yclients_id(service_id)
    staff_id = _normalize_yclients_id(staff_id)
    if not service_id or not staff_id:
        return False
    raw_service = _SERVICE_RAW_CACHE.get((company_id, service_id)) or {}
    assigned_staff_ids = _extract_assigned_staff_ids_from_service(raw_service)
    if assigned_staff_ids is not None:
        return staff_id in assigned_staff_ids
    staff_service_map = await _load_staff_service_map(company_id)
    return service_id in staff_service_map.get(staff_id, set())


async def _load_categories(company_id: str, services: list[ServiceItem]) -> list[dict[str, str]]:
    category_scope = ",".join(sorted({service.category_id for service in services})) or "empty"
    key = (company_id, f"categories:{category_scope}")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    categories: list[dict[str, str]] = []
    service_counts: dict[str, int] = {}
    for service in services:
        service_counts[service.category_id] = service_counts.get(service.category_id, 0) + 1
    client, cid = await _build_client()
    try:
        payload = await list_service_categories(client, company_id=cid)
        rows = _extract_data(payload)
        for item in rows:
            category_id = _safe_str(item.get("id"))
            title = _safe_str(item.get("title") or item.get("name"))
            if not category_id or _is_empty_or_nogroup(category_id, title):
                continue
            if service_counts.get(category_id, 0) <= 0:
                continue
            count = item.get("services_count") or item.get("service_count")
            if count is not None:
                try:
                    if int(count) <= 0:
                        continue
                except (TypeError, ValueError):
                    pass
            elif service_counts.get(category_id, 0) <= 0:
                continue
            categories.append({"id": category_id, "name": title})
    except Exception as exc:
        logger.info("Could not fetch categories directly, fallback to services grouping: %s", type(exc).__name__)
    finally:
        await client.close()

    if not categories:
        grouped: dict[str, str] = {}
        for service in services:
            if _is_empty_or_nogroup(service.category_id, service.category_name):
                continue
            grouped[service.category_id] = service.category_name
        categories = [{"id": category_id, "name": name} for category_id, name in grouped.items()]

    categories.sort(key=lambda row: row["name"])
    _cache_set(key, categories)
    return categories


async def _load_staff(company_id: str, service: ServiceItem) -> StaffResolution:
    service_id = service.id
    key = (company_id, f"staff:{service_id}")
    cached = _cache_get(key)
    if cached is not None:
        return StaffResolution(
            staff_list=cached.staff_list,
            supports_any_master=cached.supports_any_master,
            source=f"cache:{cached.source}",
            service_payload_count=cached.service_payload_count,
            endpoint_count=cached.endpoint_count,
            service_payload_snippet=cached.service_payload_snippet,
            endpoint_payload_snippet=cached.endpoint_payload_snippet,
            trace_id=f"staff-resolve-cache-{time.time_ns()}",
        )

    service_payload = _SERVICE_RAW_CACHE.get((company_id, service.id)) or {
        "id": service.id,
        "title": service.name,
        "category_id": service.category_id,
        "category_title": service.category_name,
    }
    resolved = await _resolve_assigned_staff(company_id, service_payload)
    _cache_set(key, resolved)
    return resolved


async def _request_book_times_response(
    company_id: str,
    *,
    service_id: str | None,
    staff_id: str | None,
    iso_date: str,
) -> tuple[YClientsResponse, YClientsClient]:
    client, cid = await _build_client()
    normalized_staff_id = staff_id or "0"
    params = {"service_ids[]": service_id} if service_id else None
    try:
        response = await asyncio.wait_for(
            client.request(
                "GET",
                f"/api/v1/book_times/{cid}/{normalized_staff_id}/{iso_date}",
                params=params,
            ),
            timeout=YCLIENTS_SLOT_CALL_TIMEOUT_S,
        )
    except Exception:
        await client.close()
        raise
    return response, client


async def _request_book_dates_response(
    company_id: str,
    *,
    service_id: str | None = None,
    staff_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[YClientsResponse, YClientsClient]:
    client, cid = await _build_client()
    params: dict[str, Any] = {"staff_id": staff_id or "0"}
    if service_id:
        params["service_ids[]"] = service_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    try:
        response = await asyncio.wait_for(
            client.request("GET", f"/api/v1/book_dates/{cid}", params=params),
            timeout=YCLIENTS_SLOT_CALL_TIMEOUT_S,
        )
    except Exception:
        await client.close()
        raise
    return response, client


def _extract_available_dates(payload: dict[str, Any] | list[Any], *, today: date) -> list[str]:
    data: Any = payload.get("data") if isinstance(payload, dict) else payload
    seen: set[str] = set()

    def add_iso(raw: Any) -> None:
        value = _safe_str(raw)
        if not value:
            return
        try:
            parsed = datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return
        if parsed >= today:
            seen.add(parsed.isoformat())

    def walk(value: Any) -> None:
        if isinstance(value, str):
            add_iso(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, dict):
            for key in ("booking_dates", "working_dates"):
                nested = value.get(key)
                if isinstance(nested, list):
                    walk(nested)
            for key in ("booking_days", "working_days"):
                month_days = value.get(key)
                if not isinstance(month_days, dict):
                    continue
                year = today.year
                for month_raw, days in month_days.items():
                    try:
                        month = int(month_raw)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(days, list):
                        continue
                    for day_raw in days:
                        try:
                            candidate = date(year, month, int(day_raw))
                            if candidate < today and month < today.month:
                                candidate = date(year + 1, month, int(day_raw))
                        except (TypeError, ValueError):
                            continue
                        if candidate >= today:
                            seen.add(candidate.isoformat())

    walk(data)
    return sorted(seen)


async def _load_slots(company_id: str, *, service_id: str | None, staff_id: str | None, iso_date: str) -> list[SlotItem]:
    cache_service = service_id or "any_service"
    cache_staff = staff_id or "any"
    key = (company_id, f"slots:{cache_service}:{cache_staff}:{iso_date}")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    started_at = time.perf_counter()
    logger.info(
        "booking_slots_load_started company_id=%s selected_date=%s service_id=%s staff_id=%s endpoint=%s timeout_s=%.1f",
        company_id,
        iso_date,
        service_id or "n/a",
        staff_id or "0",
        _book_times_endpoint(company_id, staff_id, iso_date, service_id),
        YCLIENTS_SLOT_CALL_TIMEOUT_S,
    )
    client: YClientsClient | None = None
    try:
        response, client = await _request_book_times_response(company_id, service_id=service_id, staff_id=staff_id, iso_date=iso_date)
        if _is_empty_availability_status(response.status):
            logger.info(
                "booking_slots_empty company_id=%s selected_date=%s service_id=%s staff_id=%s status=%s elapsed_ms=%s",
                company_id, iso_date, service_id or "n/a", staff_id or "0", response.status, int((time.perf_counter() - started_at) * 1000),
            )
            _cache_set(key, [], ttl_s=DATE_SLOTS_CACHE_TTL_S)
            return []
        client.raise_for_status(response)
        if isinstance(response.body, str):
            slots: list[SlotItem] = []
        else:
            slots = _extract_slots(response.body)
        _cache_set(key, slots, ttl_s=DATE_SLOTS_CACHE_TTL_S)
        logger.info(
            "booking_slots_load_finished company_id=%s selected_date=%s service_id=%s staff_id=%s endpoint=%s status=%s elapsed_ms=%s slots=%s",
            company_id, iso_date, service_id or "n/a", staff_id or "0", response.path_with_query, response.status, int((time.perf_counter() - started_at) * 1000), len(slots),
        )
        return slots
    except Exception:
        logger.exception(
            "booking_slots_load_failed company_id=%s selected_date=%s service_id=%s staff_id=%s elapsed_ms=%s",
            company_id, iso_date, service_id or "n/a", staff_id or "0", int((time.perf_counter() - started_at) * 1000),
        )
        raise
    finally:
        if client is not None:
            await client.close()


def _build_common_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text=BACK, callback_data=CB_BACK)],
        [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
    ]


def _build_common_only_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_build_common_rows())


def _is_empty_availability_status(status: int) -> bool:
    # YClients availability endpoints may answer with validation/not-found statuses
    # when a staff schedule is absent for the requested period. In the booking UI
    # this means "no free windows", not a user-facing crash.
    return status in {400, 404, 422}


def _build_booking_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👨‍🔧 Выбрать специалиста", callback_data=CB_HUB_STAFF)],
            [InlineKeyboardButton(text="📅 Выбрать дату и время", callback_data=CB_HUB_DATETIME)],
            [InlineKeyboardButton(text="🧾 Выбрать услуги", callback_data=CB_HUB_SERVICE)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
        ]
    )


def _build_pref_dates_kb(available_dates: list[str], page: int = 0) -> InlineKeyboardMarkup:
    return _build_dates_kb(available_dates, page=page)


def _build_pref_times_kb(slots: list[SlotItem], page: int = 0) -> InlineKeyboardMarkup:
    return _build_slots_kb(slots, page=page)


async def _load_datetime_first_dates(company_id: str) -> list[str]:
    key = (company_id, "datetime_first_dates")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    now = await _company_now(company_id)
    date_from = now.date().isoformat()
    date_to = (now.date() + timedelta(days=DATE_LOOKAHEAD_DAYS - 1)).isoformat()
    started_at = time.perf_counter()
    client: YClientsClient | None = None
    try:
        response, client = await _request_book_dates_response(company_id, date_from=date_from, date_to=date_to)
        if _is_empty_availability_status(response.status):
            dates: list[str] = []
        else:
            client.raise_for_status(response)
            dates = _extract_available_dates(response.body if not isinstance(response.body, str) else {}, today=now.date())
            services = await _load_services(company_id)
            dates = await _filter_dates_with_available_slots(company_id, dates, service_id=None, staff_id=None, services=services)
        _cache_set(key, dates, ttl_s=DATE_SLOTS_CACHE_TTL_S)
        logger.info(
            "booking_slots_load_finished company_id=%s selected_date=n/a endpoint=%s status=%s api_call_count=1 elapsed_ms=%s slots=%s",
            company_id,
            response.path_with_query,
            response.status,
            int((time.perf_counter() - started_at) * 1000),
            len(dates),
        )
        return dates
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout company_id=%s selected_date=n/a endpoint=/api/v1/book_dates/%s elapsed_ms=%s",
            company_id,
            company_id,
            int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except Exception:
        logger.exception(
            "booking_slots_load_failed company_id=%s selected_date=n/a endpoint=/api/v1/book_dates/%s elapsed_ms=%s",
            company_id,
            company_id,
            int((time.perf_counter() - started_at) * 1000),
        )
        raise
    finally:
        if client is not None:
            await client.close()


NO_AVAILABLE_DATES_TEXT = "😔 У этого мастера пока нет свободных окон для записи.\nПопробуйте выбрать другого мастера или услугу."
NO_AVAILABLE_MASTERS_TEXT = "😔 Сейчас нет мастеров со свободными окнами для записи.\nПопробуйте заглянуть позже."
NO_SERVICE_AVAILABLE_MASTERS_TEXT = "😔 Для этой услуги пока нет мастеров со свободными окнами.\nПопробуйте выбрать другую услугу или загляните позже."
STAFF_AVAILABILITY_LOAD_ERROR_TEXT = "⏳ Не удалось загрузить свободные окна мастеров. Попробуйте чуть позже."


async def _filter_dates_with_available_slots(
    company_id: str,
    dates: list[str],
    *,
    service_id: str | None,
    staff_id: str | None,
    services: list[ServiceItem] | None = None,
    user_tg_id: int | None = None,
) -> list[str]:
    if not dates:
        return []
    now = await _company_now(company_id)
    branch_timezone = str(now.tzinfo)
    logger.info(
        "booking_dates_filter_started user_tg_id=%s service_id=%s staff_id=%s branch_timezone=%s dates_count=%s",
        user_tg_id or "n/a",
        service_id or "n/a",
        staff_id or "0",
        branch_timezone,
        len(dates),
    )
    filtered: list[str] = []
    for iso_date in dates:
        try:
            if service_id:
                slots = await _load_slots(company_id, service_id=service_id, staff_id=staff_id, iso_date=iso_date)
            elif services is not None:
                slots = await _load_datetime_first_slots_for_date(company_id, iso_date=iso_date, services=services)
            else:
                slots = await _load_slots(company_id, service_id=None, staff_id=staff_id, iso_date=iso_date)
            future_slots = [slot for slot in slots if _slot_is_future_for_company_day(slot, iso_date=iso_date, now=now)]
            logger.info(
                "booking_date_slots_checked user_tg_id=%s service_id=%s staff_id=%s date=%s branch_timezone=%s slots_count=%s available_future_slots_count=%s",
                user_tg_id or "n/a",
                service_id or "n/a",
                staff_id or "0",
                iso_date,
                branch_timezone,
                len(slots),
                len(future_slots),
            )
            if future_slots:
                filtered.append(iso_date)
            else:
                logger.info(
                    "booking_date_hidden_no_slots user_tg_id=%s service_id=%s staff_id=%s date=%s branch_timezone=%s slots_count=%s available_future_slots_count=%s",
                    user_tg_id or "n/a",
                    service_id or "n/a",
                    staff_id or "0",
                    iso_date,
                    branch_timezone,
                    len(slots),
                    0,
                )
        except Exception as exc:
            logger.warning(
                "booking_date_hidden_no_slots user_tg_id=%s service_id=%s staff_id=%s date=%s branch_timezone=%s slots_count=%s available_future_slots_count=%s exception_type=%s exception_message=%s",
                user_tg_id or "n/a",
                service_id or "n/a",
                staff_id or "0",
                iso_date,
                branch_timezone,
                0,
                0,
                type(exc).__name__,
                str(exc)[:180],
                exc_info=True,
            )
    logger.info(
        "booking_dates_filter_finished user_tg_id=%s service_id=%s staff_id=%s branch_timezone=%s dates_count=%s available_dates_count=%s",
        user_tg_id or "n/a",
        service_id or "n/a",
        staff_id or "0",
        branch_timezone,
        len(dates),
        len(filtered),
    )
    return filtered


async def _load_datetime_first_slots_for_date(company_id: str, *, iso_date: str, services: list[ServiceItem]) -> list[SlotItem]:
    key = (company_id, f"datetime_first_slots:{iso_date}")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    now = await _company_now(company_id)
    started_at = time.perf_counter()
    api_call_count = 0
    logger.info(
        "booking_slots_load_started user_id=n/a selected_date=%s company_id=%s branch_timezone=%s service_id=n/a staff_id=0 endpoint=%s total_timeout_s=%.1f",
        iso_date,
        company_id,
        now.tzinfo,
        _book_times_endpoint(company_id, None, iso_date),
        YCLIENTS_SLOT_TOTAL_TIMEOUT_S,
    )

    async def direct_load() -> list[SlotItem]:
        nonlocal api_call_count
        api_call_count += 1
        return await _load_slots(company_id, service_id=None, staff_id=None, iso_date=iso_date)

    async def service_load(service: ServiceItem, semaphore: asyncio.Semaphore) -> list[SlotItem]:
        nonlocal api_call_count
        async with semaphore:
            api_call_count += 1
            try:
                return await _load_slots(company_id, service_id=service.id, staff_id=None, iso_date=iso_date)
            except Exception:
                logger.exception(
                    "booking_slots_load_failed company_id=%s selected_date=%s service_id=%s staff_id=0 error_summary=service_fallback_failed",
                    company_id, iso_date, service.id,
                )
                return []

    try:
        async def load_all() -> list[SlotItem]:
            direct_slots = await direct_load()
            if direct_slots:
                return direct_slots
            semaphore = asyncio.Semaphore(YCLIENTS_SLOT_CONCURRENCY)
            batches = [service_load(service, semaphore) for service in services]
            results = await asyncio.gather(*batches) if batches else []
            by_time: dict[str, SlotItem] = {}
            for slots in results:
                for slot in slots:
                    by_time.setdefault(slot.time, slot)
            return sorted(by_time.values(), key=lambda item: item.time)

        slots = await asyncio.wait_for(load_all(), timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S)
        future_slots = [slot for slot in slots if _slot_is_future_for_company_day(slot, iso_date=iso_date, now=now)]
        _cache_set(key, future_slots, ttl_s=DATE_SLOTS_CACHE_TTL_S)
        if not future_slots:
            logger.info(
                "booking_slots_empty selected_date=%s company_id=%s branch_timezone=%s api_call_count=%s elapsed_ms=%s",
                iso_date, company_id, now.tzinfo, api_call_count, int((time.perf_counter() - started_at) * 1000),
            )
        logger.info(
            "booking_slots_load_finished selected_date=%s company_id=%s branch_timezone=%s api_call_count=%s elapsed_ms=%s slots=%s",
            iso_date, company_id, now.tzinfo, api_call_count, int((time.perf_counter() - started_at) * 1000), len(future_slots),
        )
        return future_slots
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout selected_date=%s company_id=%s branch_timezone=%s api_call_count=%s elapsed_ms=%s",
            iso_date, company_id, now.tzinfo, api_call_count, int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except Exception:
        logger.exception(
            "booking_slots_load_failed selected_date=%s company_id=%s branch_timezone=%s api_call_count=%s elapsed_ms=%s",
            iso_date, company_id, now.tzinfo, api_call_count, int((time.perf_counter() - started_at) * 1000),
        )
        raise


def _build_success_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Мои записи", callback_data=CB_MY_BOOKINGS)],
            [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
        ]
    )


def _build_phone_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK, callback_data=CB_PHONE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
        ]
    )


def _build_phone_reply_kb(*, include_registered_phone: bool) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if include_registered_phone:
        rows.append([KeyboardButton(text=BOOKING_USE_REGISTERED_PHONE)])
    rows.append([KeyboardButton(text="📞 Поделиться контактом", request_contact=True)])
    rows.append([KeyboardButton(text=BACK), KeyboardButton(text=HOME)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _format_date_russian(iso_date: str) -> str:
    try:
        return datetime.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def _normalize_phone(raw_phone: str) -> str | None:
    cleaned = re.sub(r"[^\d+]", "", raw_phone)
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if cleaned.startswith("8") and len(cleaned) == 11:
        cleaned = f"+7{cleaned[1:]}"
    if cleaned.startswith("7") and len(cleaned) == 11:
        cleaned = f"+{cleaned}"
    if not cleaned.startswith("+"):
        if cleaned.isdigit() and 10 <= len(cleaned) <= 15:
            cleaned = f"+{cleaned}"
        else:
            return None
    digits = cleaned[1:]
    if not digits.isdigit() or not (10 <= len(digits) <= 15):
        return None
    return cleaned


def _build_phone_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить запись", callback_data=CB_CONFIRM_FINAL)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_CONFIRM_CANCEL)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PHONE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
        ]
    )


async def _resolve_client_profile(callback: CallbackQuery, state: FSMContext) -> tuple[str, str | None]:
    user = await get_user(callback.from_user.id)
    tg_name = " ".join(filter(None, [callback.from_user.first_name, callback.from_user.last_name])).strip()
    full_name = (user or {}).get("name") or tg_name or "Гость"
    phone = _normalize_phone(str((user or {}).get("phone") or ""))
    await state.update_data(client_fullname=full_name)
    return full_name, phone


async def _build_summary_text(
    data: dict[str, Any],
    *,
    include_header: bool = True,
    phone: str | None = None,
    company_id: str | None = None,
) -> str:
    service_name = _safe_str(data.get("selected_service_name")) or "—"
    staff_name = _safe_str(data.get("selected_staff_name")) or "Любой мастер"
    selected_date = _format_date_russian(_safe_str(data.get("selected_date")))
    selected_time = _safe_str(data.get("selected_time")) or "—"
    selected_dt = parse_yclients_datetime(data.get("selected_datetime"))
    price = _safe_str(data.get("selected_service_price"))
    duration = _safe_str(data.get("selected_service_duration"))

    resolved_company_id = company_id or _safe_str(data.get("yclients_company_id"))
    if not resolved_company_id:
        credentials, _ = await get_yclients_credentials()
        resolved_company_id = credentials.company_id

    tz_context = await resolve_company_timezone(resolved_company_id)
    contacts = await resolve_contacts_for_company(resolved_company_id)
    if selected_dt is not None:
        local_dt = selected_dt.astimezone(ZoneInfo(tz_context.timezone_name))
        selected_date = local_dt.strftime("%d.%m.%Y")
        selected_time = local_dt.strftime("%H:%M")

    lines = ["Подтвердите запись, пожалуйста 🙂\n"] if include_header else []
    lines.extend(
        [
            f"✂️ Услуга: {service_name}",
            f"👤 Мастер: {staff_name}",
            f"📅 Дата: {selected_date}",
            f"🕒 Время: {selected_time}",
        ]
    )
    if price:
        lines.append(f"💰 Цена: {price}")
    if duration:
        lines.append(f"⏳ Длительность: {duration}")
    lines.extend(
        [
            f"📍 Адрес: {contacts.resolved.address}",
            f"📞 Контакты: {contacts.resolved.phone}",
        ]
    )
    if phone:
        lines.append(f"📱 Телефон: {phone}")
    return "\n".join(lines)


def _build_categories_kb(categories: list[dict[str, str]], page: int) -> InlineKeyboardMarkup:
    total_pages = max((len(categories) - 1) // PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = categories[start : start + PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    for item in chunk:
        rows.append([InlineKeyboardButton(text=item["name"], callback_data=f"{CB_CATEGORY}:{item['id']}")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PAGE_CATEGORY}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_PAGE_CATEGORY}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.extend(_build_common_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _service_button_text(service: ServiceItem) -> str:
    details: list[str] = []
    if service.price:
        details.append(service.price)
    if service.duration:
        details.append(service.duration)
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{service.name}{suffix}"


def _build_services_kb(services: list[ServiceItem], page: int) -> InlineKeyboardMarkup:
    total_pages = max((len(services) - 1) // PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = services[start : start + PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    for item in chunk:
        rows.append([InlineKeyboardButton(text=_service_button_text(item), callback_data=f"{CB_SERVICE}:{item.id}")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PAGE_SERVICE}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_PAGE_SERVICE}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.extend(_build_common_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _staff_button_text(staff: StaffItem) -> str:
    details: list[str] = []
    if staff.specialization:
        details.append(staff.specialization)
    if staff.rating:
        details.append(f"⭐️ {staff.rating}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"💈 {staff.name}{suffix}"


def _build_staff_kb(staff_list: list[StaffItem], page: int, *, supports_any_master: bool) -> InlineKeyboardMarkup:
    total_pages = max((len(staff_list) - 1) // PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = staff_list[start : start + PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    if supports_any_master:
        rows.append([InlineKeyboardButton(text="👤 Любой специалист", callback_data=CB_STAFF_ANY)])

    for item in chunk:
        rows.append([InlineKeyboardButton(text=_staff_button_text(item), callback_data=f"{CB_STAFF}:{item.id}")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PAGE_STAFF}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_PAGE_STAFF}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.extend(_build_common_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)




async def _render_staff_selection(callback: CallbackQuery, *, staff_list: list[StaffItem], page: int, supports_any_master: bool) -> None:
    keyboard = _build_staff_kb(staff_list, page, supports_any_master=supports_any_master)
    await _safe_edit_text(callback, text="Выберите мастера 💈", reply_markup=keyboard)


def _build_dates_kb(available_dates: list[str], *, page: int) -> InlineKeyboardMarkup:
    total_pages = max((len(available_dates) - 1) // DATE_PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * DATE_PAGE_SIZE
    chunk = available_dates[start : start + DATE_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    date_buttons: list[InlineKeyboardButton] = []
    for iso_date in chunk:
        current = datetime.fromisoformat(iso_date).date()
        date_buttons.append(
            InlineKeyboardButton(
                text=f"📅 {_date_label(current)}",
                callback_data=f"{CB_DATE}:{iso_date}",
            )
        )

    for index in range(0, len(date_buttons), 2):
        rows.append(date_buttons[index : index + 2])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_WEEK}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_WEEK}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    rows.extend(_build_common_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_slots_kb(slots: list[SlotItem], page: int) -> InlineKeyboardMarkup:
    total_pages = max((len(slots) - 1) // TIME_PAGE_SIZE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * TIME_PAGE_SIZE
    chunk = slots[start : start + TIME_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    slot_buttons = [InlineKeyboardButton(text=f"🕒 {item.time}", callback_data=f"{CB_TIME}:{item.time}") for item in chunk]
    for index in range(0, len(slot_buttons), 3):
        rows.append(slot_buttons[index : index + 3])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PAGE_TIME}:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_PAGE_TIME}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.extend(_build_common_rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _notify_service_selection_failure(callback: CallbackQuery, exc: Exception) -> None:
    trace_id = getattr(exc, "trace_id", None) or "n/a"
    method = getattr(exc, "method", None) or "GET"
    endpoint = getattr(exc, "endpoint", None) or "/unknown"
    status = getattr(exc, "status_code", None)
    snippet = getattr(exc, "response_snippet", None) or str(exc)
    partner_present = getattr(exc, "partner_token_present", None)
    user_present = getattr(exc, "user_token_present", None)

    if partner_present is None or user_present is None:
        try:
            credentials, _ = await get_yclients_credentials()
            partner_present = bool(credentials.partner_token)
            user_present = bool(credentials.user_token)
        except Exception:
            partner_present = bool(partner_present) if partner_present is not None else False
            user_present = bool(user_present) if user_present is not None else False

    if status is None:
        status_text = f"exception={type(exc).__name__}: {str(exc)[:300]}"
    else:
        status_text = f"status={status}"

    text = (
        "🚨 YClients: ошибка после выбора услуги\n"
        f"🧩 trace_id: {trace_id}\n"
        f"➡️ endpoint: {endpoint}\n"
        f"🛠 method: {method}\n"
        f"📡 {status_text}\n"
        f"📄 response: {(snippet or '—')[:1000]}\n"
        f"🔐 partner_token={'yes' if partner_present else 'no'}\n"
        f"🔐 user_token={'yes' if user_present else 'no'}"
    )

    try:
        await callback.bot.send_message(DEV_DIAGNOSTICS_TG_ID, text)
    except Exception:
        logger.exception("Failed to send YClients service-selection diagnostics")


async def _send_yclients_error(target: Message | CallbackQuery, exc: Exception) -> None:
    logger.exception("Booking flow YClients error: %s", exc)
    setup_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Настроить / Изменить", callback_data="yclients:setup")],
            [InlineKeyboardButton(text=BACK, callback_data=CB_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
        ]
    )
    if isinstance(exc, YClientsCredentialsError):
        text = "❌ Интеграция YClients не настроена. Откройте главное меню и проверьте настройки ⚙️"
        reply_markup = setup_kb
    elif isinstance(exc, YClientsAuthError):
        text = "❌ Нет доступа к YClients. Проверьте токены ⚙️"
        reply_markup = setup_kb
    elif isinstance(exc, YClientsRateLimitError):
        text = "⏳ Слишком много запросов. Попробуйте через минуту 🙂"
        reply_markup = None
    else:
        text = "⚠️ Техническая ошибка. Попробуйте ещё раз через минуту."
        reply_markup = _build_common_only_keyboard()
        bot = target.bot if isinstance(target, CallbackQuery) else target.bot
        await notify_yclients_exception(bot, exc=exc, action="booking_flow")

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text, reply_markup=reply_markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=reply_markup)


async def _answer_with_optional_photo(
    target: Message | CallbackQuery,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    photo: Any | None = None,
) -> None:
    can_send_photo = bool(photo)
    if isinstance(target, CallbackQuery):
        if target.message:
            if can_send_photo:
                await target.message.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
            else:
                await _safe_edit_text(target, text=text, reply_markup=reply_markup)
                return
        await target.answer()
    else:
        if can_send_photo:
            await target.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
        else:
            await target.answer(text, reply_markup=reply_markup)


async def _safe_edit_text(callback: CallbackQuery, *, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    if not callback.message:
        await callback.answer()
        return

    message = callback.message
    try:
        if message.text:
            await message.edit_text(text, reply_markup=reply_markup)
        elif message.caption is not None:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup)
    await callback.answer()


async def _revalidate_before_final_step(target: Message | CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    user_id = target.from_user.id if isinstance(target, CallbackQuery) else target.from_user.id
    callback_data = target.data if isinstance(target, CallbackQuery) else None
    entry_mode = _safe_str(data.get("entry_mode"))
    service_id = _safe_str(data.get("selected_service_id"))
    staff_id = _safe_str(data.get("selected_staff_id"))
    selected_date = _safe_str(data.get("selected_date"))
    selected_time = _safe_str(data.get("selected_time"))
    selected_datetime = _safe_str(data.get("selected_datetime"))

    if not service_id or not selected_date or not selected_time:
        _log_booking_issue(
            "booking_state_invalid",
            user_id=user_id,
            entry_mode=entry_mode,
            staff_id=staff_id or None,
            service_id=service_id or None,
            selected_datetime=selected_datetime or None,
            callback_data=callback_data,
            summary="required_booking_context_missing",
        )
        await _reply_booking_step_error(target)
        return False

    company_id = "n/a"
    try:
        company_id, _ = await _get_company_context()
        services = await _load_services(company_id)
    except Exception as exc:
        _log_booking_issue(
            "booking_service_select_failed",
            user_id=user_id,
            entry_mode=entry_mode,
            staff_id=staff_id or None,
            service_id=service_id,
            selected_datetime=selected_datetime or None,
            callback_data=callback_data,
            endpoint=getattr(exc, "endpoint", None),
            method=getattr(exc, "method", None),
            summary=type(exc).__name__,
        )
        await _send_yclients_error(target, exc)
        return False

    known_service_ids = {item.id for item in services}
    if service_id not in known_service_ids:
        preserve_datetime = entry_mode == "datetime_first"
        await state.update_data(
            selected_service_id=None,
            selected_service_name=None,
            selected_service_price=None,
            selected_service_duration=None,
            selected_date=(selected_date if preserve_datetime else None),
            selected_time=(selected_time if preserve_datetime else None),
            selected_datetime=(selected_datetime if preserve_datetime else None),
        )
        await target.answer("😔 Эта услуга недоступна в выбранное время. Выберите другую услугу или время.")
        await _show_categories(target, state)
        return False

    if staff_id:
        try:
            if not await _is_service_compatible_with_staff(company_id, service_id, staff_id):
                preserve_datetime = entry_mode == "datetime_first"
                await state.update_data(
                    selected_staff_id=None,
                    selected_staff_name=None,
                    selected_staff_photo_file_id=None,
                    selected_date=(selected_date if preserve_datetime else None),
                    selected_time=(selected_time if preserve_datetime else None),
                    selected_datetime=(selected_datetime if preserve_datetime else None),
                )
                if isinstance(target, CallbackQuery):
                    await target.answer("😔 Этот мастер недоступен в выбранное время. Выберите другого мастера.", show_alert=True)
                    await _show_staff(target, state, page=int(data.get("book_page_staff", 0) or 0))
                else:
                    await target.answer("😔 Этот мастер недоступен в выбранное время. Выберите другого мастера.")
                    await _show_booking_hub(target, state)
                return False
        except Exception as exc:
            _log_booking_issue(
                "booking_staff_filter_failed",
                user_id=user_id,
                entry_mode=entry_mode,
                staff_id=staff_id,
                service_id=service_id,
                selected_datetime=selected_datetime or None,
                callback_data=callback_data,
                endpoint=getattr(exc, "endpoint", None),
                method=getattr(exc, "method", None),
                summary=type(exc).__name__,
            )
            await _send_yclients_error(target, exc)
            return False

    try:
        slots = await _load_slots(company_id, service_id=service_id, staff_id=staff_id or None, iso_date=selected_date)
    except Exception as exc:
        _log_booking_issue(
            "booking_slots_fetch_failed",
            user_id=user_id,
            entry_mode=entry_mode,
            staff_id=staff_id or None,
            service_id=service_id,
            selected_datetime=selected_datetime or None,
            callback_data=callback_data,
            endpoint=getattr(exc, "endpoint", None),
            method=getattr(exc, "method", None),
            summary=type(exc).__name__,
        )
        await _send_yclients_error(target, exc)
        return False

    if not any(slot.time == selected_time for slot in slots):
        await state.update_data(selected_time=None, selected_datetime=None, reschedule_new_datetime=None)
        await target.answer("😔 На выбранное время уже нет свободной записи. Выберите другое время.")
        await _show_slots(target, state, iso_date=selected_date, page=0)
        return False

    return True


def _booking_step_text(data: dict[str, Any], *, tail: str, include_selected_date: bool = True) -> str:
    service_name = _safe_str(data.get("selected_service_name")) or "—"
    staff_name = _safe_str(data.get("selected_staff_name")) or "Любой мастер"

    lines = [
        f"✂️ Услуга: {service_name}",
        f"💈 Мастер: {staff_name}",
    ]
    if include_selected_date:
        selected_date = _format_date_russian(_safe_str(data.get("selected_date"))) if data.get("selected_date") else "—"
        lines.append(f"📅 Дата: {selected_date}")
    lines.append(tail)
    return "\n".join(lines)


async def _get_selected_master_photo_file_id(state: FSMContext) -> str | None:
    data = await state.get_data()
    cached_file_id = _safe_str(data.get("selected_staff_photo_file_id"))
    if cached_file_id:
        return cached_file_id

    staff_id = _safe_str(data.get("selected_staff_id"))
    if not staff_id:
        await state.update_data(selected_staff_photo_file_id=None)
        return None

    company_id, _ = await _get_company_context()
    photo_row = await get_master_photo(company_id, staff_id)
    file_id = _safe_str((photo_row or {}).get("telegram_file_id")) or None
    await state.update_data(selected_staff_photo_file_id=file_id)
    return file_id


async def _send_confirmation_with_optional_photo(target: Message | CallbackQuery, state: FSMContext, text: str) -> None:
    photo_file_id = await _get_selected_master_photo_file_id(state)
    message = target.message if isinstance(target, CallbackQuery) else target
    if not message:
        return
    if photo_file_id:
        await message.answer_photo(
            photo=photo_file_id,
            caption=text,
            reply_markup=_build_phone_confirm_kb(),
        )
        return
    await message.answer(text, reply_markup=_build_phone_confirm_kb())



async def _load_available_dates(company_id: str, *, service_id: str, staff_id: str | None) -> list[str]:
    cache_staff = staff_id or "any"
    key = (company_id, f"available_dates:{service_id}:{cache_staff}")
    cached = _cache_get(key)
    if cached is not None:
        return cached

    now = await _company_now(company_id)
    date_from = now.date().isoformat()
    date_to = (now.date() + timedelta(days=DATE_LOOKAHEAD_DAYS - 1)).isoformat()
    client: YClientsClient | None = None
    try:
        response, client = await _request_book_dates_response(
            company_id, service_id=service_id, staff_id=staff_id, date_from=date_from, date_to=date_to
        )
        if _is_empty_availability_status(response.status):
            available: list[str] = []
        else:
            client.raise_for_status(response)
            available = _extract_available_dates(response.body if not isinstance(response.body, str) else {}, today=now.date())
    except Exception as exc:
        if not getattr(exc, "requested_date", None):
            setattr(exc, "requested_date", date_from)
        raise
    finally:
        if client is not None:
            await client.close()

    available = await _filter_dates_with_available_slots(company_id, available, service_id=service_id, staff_id=staff_id)
    _cache_set(key, available, ttl_s=DATE_SLOTS_CACHE_TTL_S)
    return available



async def _notify_staff_availability_failure(
    target: Message | CallbackQuery,
    *,
    action: str,
    flow_step: str,
    company_id: str,
    branch_timezone: str,
    service_id: str | None = None,
    staff_id: str | None = None,
    endpoint_function: str,
    exc: Exception,
) -> None:
    text = (
        "🚨 YClients: ошибка проверки доступности мастера\n"
        f"action: {action}\n"
        f"flow_step: {flow_step}\n"
        f"company_id: {company_id or 'n/a'}\n"
        f"service_id: {service_id or 'n/a'}\n"
        f"staff_id: {staff_id or 'n/a'}\n"
        f"endpoint/function: {endpoint_function}\n"
        f"exception: {type(exc).__name__}: {str(exc)[:300]}\n"
        f"branch_timezone: {branch_timezone or 'n/a'}"
    )
    try:
        await target.bot.send_message(DEV_DIAGNOSTICS_TG_ID, text)
    except Exception:
        logger.exception("Failed to send staff availability diagnostics")


async def _show_staff_availability_load_error(target: Message | CallbackQuery) -> None:
    await _answer_with_optional_photo(
        target,
        text=STAFF_AVAILABILITY_LOAD_ERROR_TEXT,
        reply_markup=_build_common_only_keyboard(),
    )


async def _staff_has_future_slot_for_service(
    company_id: str,
    *,
    staff_id: str,
    service_id: str,
    now: datetime | None = None,
) -> bool:
    staff_id = _normalize_yclients_id(staff_id)
    service_id = _normalize_yclients_id(service_id)
    if not staff_id or not service_id:
        return False

    now = now or await _company_now(company_id)
    date_from = now.date().isoformat()
    date_to = (now.date() + timedelta(days=DATE_LOOKAHEAD_DAYS - 1)).isoformat()
    branch_timezone = str(now.tzinfo)
    key = (
        company_id,
        f"staff_availability:{branch_timezone}:{staff_id}:{service_id}:{date_from}:{date_to}",
    )
    cached = _cache_get(key)
    if cached is not None:
        return bool(cached)

    client: YClientsClient | None = None
    try:
        response, client = await _request_book_dates_response(
            company_id,
            service_id=service_id,
            staff_id=staff_id,
            date_from=date_from,
            date_to=date_to,
        )
        if _is_empty_availability_status(response.status):
            _cache_set(key, False, ttl_s=STAFF_AVAILABILITY_CACHE_TTL_S)
            return False
        client.raise_for_status(response)
        dates = _extract_available_dates(response.body if not isinstance(response.body, str) else {}, today=now.date())
    finally:
        if client is not None:
            await client.close()

    for iso_date in dates:
        slots = await _load_slots(company_id, service_id=service_id, staff_id=staff_id, iso_date=iso_date)
        if any(_slot_is_future_for_company_day(slot, iso_date=iso_date, now=now) for slot in slots):
            _cache_set(key, True, ttl_s=STAFF_AVAILABILITY_CACHE_TTL_S)
            return True

    _cache_set(key, False, ttl_s=STAFF_AVAILABILITY_CACHE_TTL_S)
    return False


async def _filter_staff_with_future_availability(
    target: Message | CallbackQuery,
    *,
    company_id: str,
    staff_list: list[StaffItem],
    services_by_staff: dict[str, list[ServiceItem]],
    flow_step: str,
) -> list[StaffItem]:
    if not staff_list:
        return []

    now = await _company_now(company_id)
    branch_timezone = str(now.tzinfo)
    semaphore = asyncio.Semaphore(YCLIENTS_SLOT_CONCURRENCY)

    async def staff_is_bookable(staff_item: StaffItem) -> tuple[StaffItem, bool]:
        staff_services = services_by_staff.get(staff_item.id, [])
        if not staff_services:
            return staff_item, False
        async with semaphore:
            for service in staff_services:
                try:
                    if await _staff_has_future_slot_for_service(
                        company_id,
                        staff_id=staff_item.id,
                        service_id=service.id,
                        now=now,
                    ):
                        return staff_item, True
                except Exception as exc:
                    await _notify_staff_availability_failure(
                        target,
                        action="filter_staff_with_future_availability",
                        flow_step=flow_step,
                        company_id=company_id,
                        branch_timezone=branch_timezone,
                        service_id=service.id,
                        staff_id=staff_item.id,
                        endpoint_function="_request_book_dates_response/_load_slots",
                        exc=exc,
                    )
                    raise
        return staff_item, False

    results = await asyncio.wait_for(
        asyncio.gather(*(staff_is_bookable(staff_item) for staff_item in staff_list)),
        timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S,
    )
    filtered = [staff_item for staff_item, bookable in results if bookable]
    logger.info(
        "booking_staff_availability_filter_finished flow_step=%s company_id=%s branch_timezone=%s staff_before=%s staff_after=%s",
        flow_step,
        company_id,
        branch_timezone,
        len(staff_list),
        len(filtered),
    )
    return filtered


async def _filter_services_with_staff_availability(
    company_id: str,
    *,
    staff_id: str,
    services: list[ServiceItem],
) -> list[ServiceItem]:
    if not staff_id or not services:
        return services
    now = await _company_now(company_id)
    filtered: list[ServiceItem] = []
    for service in services:
        if await _staff_has_future_slot_for_service(company_id, staff_id=staff_id, service_id=service.id, now=now):
            filtered.append(service)
    return filtered


async def _resolve_branch_title(company_id: str) -> str:
    try:
        client, _ = await _build_client()
        try:
            payload = await get_company(client, company_id=company_id)
        finally:
            await client.close()
    except Exception:
        return f"Филиал #{company_id}"
    raw = payload.get("data") if isinstance(payload, dict) else None
    company = raw if isinstance(raw, dict) else (payload if isinstance(payload, dict) else {})
    return _safe_str(company.get("title") or company.get("name") or company.get("short_title")) or f"Филиал #{company_id}"


async def _show_booking_hub(target: Message | CallbackQuery, state: FSMContext) -> None:
    company_id, _ = await _get_company_context()
    contacts = await resolve_contacts_for_company(company_id)
    branch_title = await _resolve_branch_title(company_id)
    lines = [f"✂️ Запись в {branch_title}"]
    if contacts.resolved.address and contacts.resolved.address != "—":
        lines.append(f"📍 {contacts.resolved.address}")
    lines.append("\nВыберите, с чего начать:")
    await state.set_state(BookingFlowStates.BOOKING_HUB)
    await state.update_data(entry_mode=None, staff_step_origin=None, selected_date=None, selected_time=None, selected_datetime=None)
    text = "\n".join(lines)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=_build_booking_hub_kb())
        await target.answer()
        return
    await target.answer(text, reply_markup=_build_booking_hub_kb())




async def _open_datetime_first_from_notification(callback: CallbackQuery, state: FSMContext) -> bool:
    started_at = time.perf_counter()
    company_id = "n/a"
    try:
        company_id, _ = await _get_company_context()
        tz_context = await resolve_company_timezone(company_id)
        available_dates = await asyncio.wait_for(_load_datetime_first_dates(company_id), timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S)
        await state.set_state(BookingFlowStates.CHOOSE_PREF_DATE)
        await state.update_data(
            entry_mode="datetime_first",
            selected_date=None,
            selected_time=None,
            selected_datetime=None,
            selected_staff_id=None,
            selected_staff_name=None,
            selected_staff_photo_file_id=None,
            branch_timezone=tz_context.timezone_name,
            yclients_company_id=company_id,
            pref_available_dates=available_dates,
            pref_slots_by_date={},
            pref_selected_slots=[],
        )
        if not available_dates:
            logger.warning(
                "cancellation_recovery_datetime_first_fallback_to_booking_hub reason=no_available_dates user_id=%s company_id=%s",
                callback.from_user.id,
                company_id,
            )
            return False
        await _answer_with_optional_photo(
            callback,
            text="📅 Выберите дату с доступными окнами:",
            reply_markup=_build_pref_dates_kb(available_dates, 0),
        )
        logger.info(
            "notification_datetime_first_opened user_id=%s company_id=%s elapsed_ms=%s",
            callback.from_user.id,
            company_id,
            int((time.perf_counter() - started_at) * 1000),
        )
        return True
    except Exception as exc:
        logger.warning(
            "cancellation_recovery_datetime_first_fallback_to_booking_hub reason=%s user_id=%s company_id=%s elapsed_ms=%s",
            type(exc).__name__,
            callback.from_user.id,
            company_id,
            int((time.perf_counter() - started_at) * 1000),
            exc_info=True,
        )
        return False


async def open_booking_from_notification(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    funnel_type: str,
    notification_event_id: int | None = None,
    yclients_client_id: str | None = None,
    is_test: bool = False,
    source: str | None = None,
    preserve_data: dict[str, Any] | None = None,
    entry: str = "hub",
) -> None:
    if callback.from_user is None:
        return
    if is_test and callback.from_user.id != DEV_DIAGNOSTICS_TG_ID:
        await callback.answer("⛔ Тестовая кнопка доступна только разработчику.", show_alert=True)
        return
    click_source = source or ("dev_test" if is_test else None)
    try:
        await log_click(
            funnel_type=funnel_type,
            client_tg_id=callback.from_user.id,
            yclients_client_id=yclients_client_id,
            notification_event_id=notification_event_id,
            is_test=is_test,
            source=click_source,
        )
    except Exception:
        logger.exception(
            "notification_booking_click_log_failed funnel_type=%s event_id=%s user_id=%s is_test=%s source=%s",
            funnel_type,
            notification_event_id,
            callback.from_user.id,
            is_test,
            click_source or "n/a",
        )
    try:
        await clear_state_preserving_navigation(state)
        await state.update_data(
            notification_funnel_type=funnel_type,
            notification_event_id=notification_event_id,
            notification_clicked_at_utc=datetime.now(timezone.utc).isoformat(),
            notification_is_test=is_test,
            notification_source=click_source,
            **(preserve_data or {}),
        )
        if entry == "datetime_first":
            opened = await _open_datetime_first_from_notification(callback, state)
            if opened:
                return
            logger.warning(
                "cancellation_recovery_datetime_first_fallback_to_booking_hub user_id=%s event_id=%s",
                callback.from_user.id,
                notification_event_id,
            )
        await _show_booking_hub(callback, state)
    except Exception:
        logger.exception(
            "notification_booking_open_failed funnel_type=%s event_id=%s user_id=%s entry=%s",
            funnel_type,
            notification_event_id,
            callback.from_user.id,
            entry,
        )
        if callback.message:
            await callback.message.answer("⚠️ Не удалось открыть запись. Попробуйте через главное меню.")
        await callback.answer()


async def _show_categories(target: Message | CallbackQuery, state: FSMContext, page: int = 0) -> None:
    try:
        company_id, _ = await _get_company_context()
        services = await _load_services(company_id)
        data = await state.get_data()
        valid_services = await _get_valid_services_for_context(company_id, data, services, filter_staff_availability=True)
        categories = await _load_categories(company_id, valid_services)
    except Exception as exc:
        await _send_yclients_error(target, exc)
        return

    selected_staff_id = _safe_str(data.get("selected_staff_id"))
    if not categories:
        if selected_staff_id:
            text = "😔 У этого мастера сейчас нет доступных услуг. Пожалуйста, выберите другого специалиста."
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=BACK, callback_data=CB_BACK)],
                    [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
                ]
            )
            await state.update_data(book_services=[], book_categories=[])
            if isinstance(target, CallbackQuery):
                if target.message:
                    await target.message.edit_text(text, reply_markup=reply_markup)
                await target.answer()
            else:
                await target.answer(text, reply_markup=reply_markup)
            return
        if isinstance(target, CallbackQuery):
            if target.message:
                await target.message.edit_text("😔 Пока нет доступных категорий услуг.", reply_markup=_build_common_only_keyboard())
            await target.answer()
        else:
            await target.answer("😔 Пока нет доступных категорий услуг.", reply_markup=_build_common_only_keyboard())
        return

    await state.set_state(BookingFlowStates.CHOOSE_CATEGORY)
    await state.update_data(book_page_category=page, book_services=[s.__dict__ for s in valid_services], book_categories=categories)
    keyboard = _build_categories_kb(categories, page)
    entry_mode = _safe_str(data.get("entry_mode"))
    base_text = "✂️ Выберите категорию услуг 😊"
    if entry_mode == "staff_first":
        base_text = "💈 Отлично! Теперь выберите категорию услуг ✂️"
    elif entry_mode == "datetime_first":
        base_text = "🧾 Теперь выберите категорию услуг для выбранной даты/времени"
    await _answer_with_optional_photo(target, text=base_text, reply_markup=keyboard, photo=await _get_selected_master_photo_file_id(state))


def _deserialize_services(data: list[dict[str, Any]]) -> list[ServiceItem]:
    result: list[ServiceItem] = []
    for item in data:
        try:
            result.append(ServiceItem(**item))
        except TypeError:
            continue
    return result


def _deserialize_staff(data: list[dict[str, Any]]) -> list[StaffItem]:
    result: list[StaffItem] = []
    for item in data:
        try:
            result.append(StaffItem(**item))
        except TypeError:
            continue
    return result


async def _show_services(target: CallbackQuery, state: FSMContext, category_id: str, page: int = 0) -> None:
    data = await state.get_data()
    categories = data.get("book_categories") or []
    raw_services = data.get("book_services") or []
    services = _deserialize_services(raw_services)

    selected_category = next((item for item in categories if item.get("id") == category_id), None)
    if not selected_category:
        await _show_categories(target, state)
        return

    filtered = [service for service in services if service.category_id == category_id]
    await state.set_state(BookingFlowStates.CHOOSE_SERVICE)
    await state.update_data(
        selected_category_id=selected_category["id"],
        selected_category_name=selected_category["name"],
        book_page_service=page,
    )

    if not filtered:
        if target.message:
            await target.message.edit_text(
                "😕 В этой категории пока нет доступных услуг.",
                reply_markup=_build_common_only_keyboard(),
            )
        await target.answer()
        return

    keyboard = _build_services_kb(filtered, page)
    entry_mode = _safe_str(data.get("entry_mode"))
    message_text = "Выберите услугу ✂️"
    if entry_mode == "datetime_first":
        message_text = "Выберите услугу для выбранной даты/времени ✂️"
    notice_text = _safe_str(data.get("booking_notice_text"))
    if notice_text:
        message_text = f"{notice_text}\n\n{message_text}"
        await state.update_data(booking_notice_text=None)

    await _answer_with_optional_photo(
        target,
        text=message_text,
        reply_markup=keyboard,
        photo=await _get_selected_master_photo_file_id(state),
    )


async def _show_staff(target: CallbackQuery, state: FSMContext, page: int = 0) -> None:
    data = await state.get_data()
    service_id = _safe_str(data.get("selected_service_id"))
    if not service_id:
        await _safe_edit_text(
            target,
            text="⚠️ Ошибка выбора услуги. Начните запись заново 🙂",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=HOME, callback_data=CB_HOME)]]),
        )
        return

    services = _deserialize_services(data.get("book_services") or [])
    selected_service = next((item for item in services if item.id == service_id), None)
    if selected_service is None:
        await _reply_booking_step_error(target)
        return

    company_id = "n/a"
    try:
        company_id, has_user_token = await _get_company_context()
        if not has_user_token:
            raise YClientsCredentialsError("Для шага выбора мастера требуется user_token")
        resolution = await _load_staff(company_id, selected_service)
        staff_list = resolution.staff_list
        supports_any_master = resolution.supports_any_master
        logger.info(
            "booking_staff_resolution category_id=%s category_name=%s service_id=%s service_name=%s source=%s assigned=%s service_payload_count=%s endpoint_count=%s trace_id=%s staff_request=%s",
            selected_service.category_id,
            _safe_str(data.get("selected_category_name")) or "n/a",
            selected_service.id,
            selected_service.name,
            resolution.source,
            len(staff_list),
            resolution.service_payload_count,
            resolution.endpoint_count,
            resolution.trace_id,
            f"/api/v1/company/{company_id}/staff?service_ids[]={selected_service.id}",
        )
        if _safe_str(data.get("entry_mode")) != "datetime_first":
            services_by_staff = {staff_item.id: [selected_service] for staff_item in staff_list}
            staff_list = await _filter_staff_with_future_availability(
                target,
                company_id=company_id,
                staff_list=staff_list,
                services_by_staff=services_by_staff,
                flow_step="service_first_staff_picker",
            )
            supports_any_master = False
        selected_date = _safe_str(data.get("selected_date"))
        selected_time = _safe_str(data.get("selected_time"))
        if _safe_str(data.get("entry_mode")) == "datetime_first" and selected_date and selected_time:
            semaphore = asyncio.Semaphore(YCLIENTS_SLOT_CONCURRENCY)

            async def staff_has_selected_time(staff_item: StaffItem) -> tuple[StaffItem, bool]:
                async with semaphore:
                    try:
                        slots = await _load_slots(company_id, service_id=selected_service.id, staff_id=staff_item.id, iso_date=selected_date)
                    except Exception as exc:
                        logger.exception(
                            "booking_slots_load_failed company_id=%s selected_date=%s service_id=%s staff_id=%s error_summary=staff_filter_failed",
                            company_id, selected_date, selected_service.id, staff_item.id,
                        )
                        await _notify_staff_availability_failure(
                            target,
                            action="filter_staff_for_selected_time",
                            flow_step="datetime_first_staff_picker",
                            company_id=company_id,
                            branch_timezone=str((await _company_now(company_id)).tzinfo),
                            service_id=selected_service.id,
                            staff_id=staff_item.id,
                            endpoint_function="_load_slots",
                            exc=exc,
                        )
                        raise
                    now_for_selected_time = await _company_now(company_id)
                    return staff_item, any(
                        slot.time == selected_time and _slot_is_future_for_company_day(slot, iso_date=selected_date, now=now_for_selected_time)
                        for slot in slots
                    )

            results = await asyncio.wait_for(
                asyncio.gather(*(staff_has_selected_time(staff_item) for staff_item in staff_list)),
                timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S,
            )
            staff_list = [staff_item for staff_item, valid in results if valid]
            supports_any_master = False
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout user_id=%s selected_date=%s company_id=%s service_id=%s staff_id=n/a error_summary=staff_filter_timeout",
            target.from_user.id, _safe_str(data.get("selected_date")) or "n/a", company_id, service_id,
        )
        await _show_staff_availability_load_error(target)
        return
    except Exception as exc:
        await _notify_staff_for_service_failure(target, service_id=service_id, company_id=company_id, exc=exc)
        await _notify_service_selection_failure(target, exc)
        await _send_yclients_error(target, exc)
        return

    await state.set_state(BookingFlowStates.CHOOSE_STAFF)
    await state.update_data(
        book_page_staff=page,
        book_staff=[item.__dict__ for item in staff_list],
        book_staff_any=supports_any_master,
        book_staff_source="availability_filtered",
        staff_step_origin=_safe_str(data.get("staff_step_origin")) or "service",
    )

    if resolution.source in {"intersection", "cache:intersection"}:
        logger.info(
            "booking_staff_resolution_disagreement_non_blocking category_id=%s service_id=%s service_name=%s assigned=%s service_payload_count=%s endpoint_count=%s source=%s trace_id=%s",
            selected_service.category_id,
            selected_service.id,
            selected_service.name,
            len(staff_list),
            resolution.service_payload_count,
            resolution.endpoint_count,
            resolution.source,
            resolution.trace_id,
        )

    if not staff_list and not supports_any_master:
        await _answer_with_optional_photo(
            target,
            text=NO_SERVICE_AVAILABLE_MASTERS_TEXT,
            reply_markup=_build_common_only_keyboard(),
        )
        return

    await _render_staff_selection(target, staff_list=staff_list, page=page, supports_any_master=supports_any_master)


async def _show_date_picker(target: CallbackQuery, state: FSMContext, *, week_offset: int | None = None) -> None:
    data = await state.get_data()
    if _safe_str(data.get("entry_mode")) == "datetime_first" and _safe_str(data.get("selected_datetime")):
        logger.warning(
            "booking_datetime_first_unexpected_date_reroute tg_user_id=%s selected_datetime=%s selected_service_id=%s selected_staff_id=%s callback_data=%s",
            target.from_user.id,
            _safe_str(data.get("selected_datetime")) or "n/a",
            _safe_str(data.get("selected_service_id")) or "n/a",
            _safe_str(data.get("selected_staff_id")) or "n/a",
            target.data,
        )
        await _route_to_confirmation_after_datetime_first_selection(target, state)
        return
    offset = int(data.get("book_week_offset", 0) or 0) if week_offset is None else max(0, week_offset)
    service_id = _safe_str(data.get("selected_service_id"))
    staff_id = _safe_str(data.get("selected_staff_id")) or None
    if not service_id:
        if target.message:
            await target.message.edit_text(
                "⚠️ Ошибка выбора услуги. Начните запись заново 🙂",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=HOME, callback_data=CB_HOME)]]),
            )
        await target.answer()
        return

    services = _deserialize_services(data.get("book_services") or [])
    selected_service = next((item for item in services if item.id == service_id), None)
    if selected_service is None:
        await _reply_booking_step_error(target)
        return

    company_id = "n/a"
    try:
        company_id, _ = await _get_company_context()
        resolution = await _load_staff(company_id, selected_service)
        assigned_staff = resolution.staff_list
    except Exception as exc:
        await _notify_staff_for_service_failure(target, service_id=service_id, company_id=company_id, exc=exc)
        await _send_yclients_error(target, exc)
        return

    if not assigned_staff:
        if resolution.source in {"intersection", "service_payload", "cache:intersection", "cache:service_payload"}:
            await _notify_staff_resolution_diagnostics(
                target,
                category_id=selected_service.category_id,
                service_id=selected_service.id,
                service_name=selected_service.name,
                resolution=resolution,
                reason="date_step_no_assigned_staff",
            )
        selected_category_id = _safe_str(data.get("selected_category_id"))
        if not selected_category_id:
            await _answer_with_optional_photo(
                target,
                text=NO_ASSIGNED_STAFF_TEXT,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=BACK, callback_data=CB_BACK)], [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)]]),
            )
            return
        await state.update_data(booking_notice_text=NO_ASSIGNED_STAFF_TEXT)
        await _show_services(target, state, selected_category_id, page=0)
        return

    if staff_id:
        assigned_staff_ids = {item.id for item in assigned_staff}
        if staff_id not in assigned_staff_ids:
            logger.info(
                "booking_selected_staff_not_assigned service_id=%s selected_staff_id=%s source=%s assigned_count=%s trace_id=%s",
                selected_service.id,
                staff_id,
                resolution.source,
                len(assigned_staff),
                resolution.trace_id,
            )
            page = int(data.get("book_page_staff", 0) or 0)
            await state.set_state(BookingFlowStates.CHOOSE_STAFF)
            await state.update_data(selected_staff_id=None, selected_staff_name=None, book_page_staff=page)
            keyboard = _build_staff_kb(assigned_staff, page, supports_any_master=resolution.supports_any_master)
            await _safe_edit_text(
                target,
                text="😕 Этот мастер недоступен для выбранной услуги. Пожалуйста, выберите другого 🙂",
                reply_markup=keyboard,
            )
            return

    try:
        available_dates = await _load_available_dates(company_id, service_id=service_id, staff_id=staff_id)
    except Exception as exc:
        selected_date = _safe_str(getattr(exc, "requested_date", None)) or _safe_str(data.get("selected_date")) or _today().isoformat()
        await _notify_book_times_failure(
            target,
            service_id=service_id,
            staff_id=staff_id,
            iso_date=selected_date,
            company_id=company_id,
            exc=exc,
        )
        await _send_yclients_error(target, exc)
        return

    if not available_dates:
        await _answer_with_optional_photo(
            target,
            text=NO_AVAILABLE_DATES_TEXT,
            reply_markup=_build_common_only_keyboard(),
        )
        return

    await state.set_state(BookingFlowStates.CHOOSE_DATE)
    await state.update_data(book_week_offset=offset, available_dates=available_dates)
    photo_file_id = await _get_selected_master_photo_file_id(state)
    await _answer_with_optional_photo(
        target,
        text=_booking_step_text(data, tail="📅 Выберите дату:", include_selected_date=False),
        reply_markup=_build_dates_kb(available_dates, page=offset),
        photo=photo_file_id,
    )


async def _show_slots(target: Message | CallbackQuery, state: FSMContext, *, iso_date: str, page: int = 0) -> None:
    try:
        selected = datetime.fromisoformat(iso_date).date()
    except ValueError:
        if isinstance(target, CallbackQuery):
            await target.answer("😔 Не удалось прочитать дату. Выберите день снова 🙂", show_alert=True)
        else:
            await target.answer("😔 Не удалось прочитать дату. Выберите день снова 🙂")
        return

    company_today = _today()
    try:
        context_company_id, _ = await _get_company_context()
        company_today = await _today_for_company(context_company_id)
    except Exception:
        logger.exception("Could not resolve company timezone for selected date validation")
    if selected < company_today:
        if isinstance(target, CallbackQuery):
            await target.answer("😔 Нельзя выбрать прошедшую дату. Выберите другой день 🙂", show_alert=True)
        else:
            await target.answer("😔 Нельзя выбрать прошедшую дату. Выберите другой день 🙂")
        return

    data = await state.get_data()
    service_id = _safe_str(data.get("selected_service_id"))
    if not service_id or "selected_staff_id" not in data:
        await _reply_booking_step_error(target)
        return

    available_dates = [item for item in data.get("available_dates") or [] if isinstance(item, str)]
    if available_dates and iso_date not in available_dates:
        if isinstance(target, CallbackQuery):
            await _answer_with_optional_photo(
                target,
                text="😕 На эту дату у мастера нет доступных окон.\nВыберите другую дату 🙂",
                reply_markup=_build_dates_kb(available_dates, page=int(data.get("book_week_offset", 0) or 0)),
            )
        else:
            await target.answer(
                "😕 На эту дату у мастера нет доступных окон.\nВыберите другую дату 🙂",
                reply_markup=_build_dates_kb(available_dates, page=int(data.get("book_week_offset", 0) or 0)),
            )
        return

    staff_id = _safe_str(data.get("selected_staff_id")) or None
    company_id = "n/a"
    try:
        company_id, _ = await _get_company_context()
        response, client = await _request_book_times_response(company_id, service_id=service_id, staff_id=staff_id, iso_date=iso_date)
    except Exception as exc:
        if isinstance(target, CallbackQuery):
            await _notify_book_times_failure(
                target,
                service_id=service_id,
                staff_id=staff_id,
                iso_date=iso_date,
                company_id=company_id,
                exc=exc,
            )
        await _send_yclients_error(target, exc)
        return

    try:
        decoded_message = ""
        if isinstance(response.body, dict):
            decoded_message = _decode_unicode(_safe_str((response.body.get("meta") or {}).get("message")))

        if _is_empty_availability_status(response.status):
            logger.info(
                "booking_slots_empty company_id=%s selected_date=%s service_id=%s staff_id=%s status=%s",
                company_id, iso_date, service_id or "n/a", staff_id or "0", response.status,
            )
            slots = []
        elif response.status in {401, 403}:
            if isinstance(target, CallbackQuery):
                await _notify_book_times_failure(
                    target,
                    service_id=service_id,
                    staff_id=staff_id,
                    iso_date=iso_date,
                    company_id=company_id,
                    title="🚨 YClients: ошибка получения слотов/времени (auth)",
                    trace_id=response.trace_id,
                    endpoint=response.path_with_query,
                    status=response.status,
                    response_snippet=decoded_message or response.response_snippet,
                )
                if target.message:
                    await target.message.answer("❌ Нет доступа к YClients. Проверьте токены ⚙️")
                await target.answer()
            else:
                await target.answer("❌ Нет доступа к YClients. Проверьте токены ⚙️")
            return

        elif not 200 <= response.status < 300:
            if isinstance(target, CallbackQuery):
                await _notify_book_times_failure(
                    target,
                    service_id=service_id,
                    staff_id=staff_id,
                    iso_date=iso_date,
                    company_id=company_id,
                    trace_id=response.trace_id,
                    endpoint=response.path_with_query,
                    status=response.status,
                    response_snippet=decoded_message or response.response_snippet,
                )
                if target.message:
                    await target.message.answer("⚠️ Техническая ошибка. Попробуйте ещё раз через минуту.")
                await target.answer()
            else:
                await target.answer("⚠️ Техническая ошибка. Попробуйте ещё раз через минуту.")
            return

        if isinstance(response.body, str):
            slots = []
        else:
            slots = _extract_slots(response.body)
            cache_staff = staff_id or "any"
            _cache_set((company_id, f"slots:{service_id}:{cache_staff}:{iso_date}"), slots)
        company_now = await _company_now(company_id)
        raw_slots_count = len(slots)
        slots = [slot for slot in slots if _slot_is_future_for_company_day(slot, iso_date=iso_date, now=company_now)]
        logger.info(
            "booking_date_slots_checked user_tg_id=%s service_id=%s staff_id=%s date=%s branch_timezone=%s slots_count=%s available_future_slots_count=%s",
            target.from_user.id if isinstance(target, CallbackQuery) else "n/a",
            service_id or "n/a",
            staff_id or "0",
            iso_date,
            company_now.tzinfo,
            raw_slots_count,
            len(slots),
        )
    finally:
        await client.close()

    current_data = await state.get_data()
    preferred_time = _safe_str(current_data.get("selected_time"))
    await state.set_state(BookingFlowStates.CHOOSE_TIME)
    await state.update_data(selected_date=iso_date, selected_time=preferred_time or None, selected_datetime=None, reschedule_new_datetime=None, book_slot_page=page)

    if not slots:
        logger.info(
            "booking_date_click_no_slots user_tg_id=%s service_id=%s staff_id=%s date=%s branch_timezone=%s slots_count=%s available_future_slots_count=%s",
            target.from_user.id if isinstance(target, CallbackQuery) else "n/a",
            service_id or "n/a",
            staff_id or "0",
            iso_date,
            (await _company_now(company_id)).tzinfo if company_id != "n/a" else "n/a",
            0,
            0,
        )
        if isinstance(target, CallbackQuery):
            if target.message:
                await target.message.edit_text(
                    "😔 На эту дату свободных окон нет. Выберите другой день 🙂",
                    reply_markup=_build_common_only_keyboard(),
                )
            await target.answer()
        else:
            await target.answer(
                "😔 На эту дату свободных окон нет. Выберите другой день 🙂",
                reply_markup=_build_common_only_keyboard(),
            )
        return

    await state.update_data(book_slots=[item.__dict__ for item in slots])
    refreshed_data = await state.get_data()
    if _safe_str(refreshed_data.get("entry_mode")) == "datetime_first" and preferred_time:
        chosen_pref = next((slot for slot in slots if slot.time == preferred_time), None)
        if chosen_pref:
            selected_datetime = chosen_pref.datetime_iso or f"{iso_date}T{preferred_time}:00"
            await state.update_data(selected_time=preferred_time, selected_datetime=selected_datetime, reschedule_new_datetime=selected_datetime)
            if isinstance(target, CallbackQuery) and target.message:
                await target.message.answer("✅ Отлично, выбранное время доступно.")
                await _prompt_for_phone(target, state)
                return
    updated_data = await state.get_data()
    photo_file_id = await _get_selected_master_photo_file_id(state)
    await _answer_with_optional_photo(
        target,
        text=_booking_step_text(updated_data, tail="🕐 Выберите удобное время:"),
        reply_markup=_build_slots_kb(slots, page),
        photo=photo_file_id,
    )


async def _route_to_confirmation_after_datetime_first_selection(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _revalidate_before_final_step(callback, state):
        logger.info(
            "booking_datetime_first_invalid_slot tg_user_id=%s callback_data=%s",
            callback.from_user.id,
            callback.data,
        )
        return
    data = await state.get_data()
    user = await get_user(callback.from_user.id)
    normalized_phone = _normalize_phone(str((user or {}).get("phone") or "")) or _normalize_phone(_safe_str(data.get("registered_phone")))
    if normalized_phone:
        await state.update_data(client_phone=normalized_phone, registered_phone=normalized_phone)
        updated_data = await state.get_data()
        company_id = _safe_str(updated_data.get("yclients_company_id"))
        if not company_id:
            credentials, _ = await get_yclients_credentials()
            company_id = credentials.company_id
        await state.set_state(BookingFlowStates.CONFIRM_PHONE)
        logger.info(
            "booking_datetime_first_route_to_confirmation tg_user_id=%s selected_datetime=%s selected_service_id=%s selected_staff_id=%s",
            callback.from_user.id,
            _safe_str(updated_data.get("selected_datetime")) or "n/a",
            _safe_str(updated_data.get("selected_service_id")) or "n/a",
            _safe_str(updated_data.get("selected_staff_id")) or "n/a",
        )
        await _send_confirmation_with_optional_photo(
            callback,
            state,
            await _build_summary_text(updated_data, phone=normalized_phone, company_id=company_id),
        )
        return
    await _prompt_for_phone(callback, state)


async def _prompt_for_phone(callback: CallbackQuery, state: FSMContext) -> None:
    _, has_user_token = await _get_company_context()
    if not has_user_token:
        if callback.message:
            await callback.message.answer("❌ Для записи нужен User token. Откройте ⚙️ Настроить / Изменить")
        await callback.answer()
        return

    fullname, _ = await _resolve_client_profile(callback, state)
    await state.set_state(BookingFlowStates.WAIT_PHONE)
    registered_phone = _normalize_phone(str((await get_user(callback.from_user.id) or {}).get("phone") or ""))
    await state.update_data(registered_phone=registered_phone, client_fullname=fullname)
    if callback.message:
        if registered_phone:
            await callback.message.answer(
                "📱 Чтобы записать вас, отправьте номер телефона 😊\n\nМожно использовать номер из регистрации:\n"
                f"{registered_phone}",
                reply_markup=_build_phone_reply_kb(include_registered_phone=True),
            )
        else:
            await callback.message.answer(
                "📱 Чтобы записать вас, отправьте номер телефона 😊",
                reply_markup=_build_phone_reply_kb(include_registered_phone=False),
            )
    await callback.answer()


async def _prompt_for_reschedule_confirmation(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    ctx = data.get("reschedule_context") if isinstance(data.get("reschedule_context"), dict) else {}
    client_phone = _normalize_phone(_safe_str(ctx.get("client_phone") or data.get("registered_phone") or ""))
    client_fullname = _safe_str(ctx.get("client_name") or data.get("client_fullname")) or "Гость"

    await state.update_data(client_phone=client_phone, client_fullname=client_fullname)
    updated_data = await state.get_data()
    company_id = _safe_str(updated_data.get("yclients_company_id") or ctx.get("company_id"))
    if not company_id:
        credentials, _ = await get_yclients_credentials()
        company_id = credentials.company_id
    await state.set_state(BookingFlowStates.CONFIRM_PHONE)
    await _send_confirmation_with_optional_photo(
        callback,
        state,
        await _build_summary_text(updated_data, phone=client_phone or None, company_id=company_id),
    )
    await callback.answer()


@router.message(F.text == BOOK_APPOINTMENT_BTN)
async def booking_flow_start(message: Message, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await _show_booking_hub(message, state)


@router.callback_query(F.data == f"{CB_PREFIX}:start")
async def booking_flow_start_from_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await _show_booking_hub(callback, state)


@router.callback_query(F.data == CB_HOME)
async def booking_flow_home(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await render_main_menu(callback, callback.from_user.id)
    await callback.answer()


@router.callback_query(BookingFlowStates.BOOKING_HUB, F.data == CB_BACK)
async def booking_flow_hub_back(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await render_main_menu(callback, callback.from_user.id)
    await callback.answer()


@router.callback_query(BookingFlowStates.BOOKING_HUB, F.data == CB_HUB_SERVICE)
async def booking_flow_hub_service(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(entry_mode="service_first", staff_step_origin="service", selected_staff_id=None, selected_staff_name=None, selected_staff_photo_file_id=None)
    await _show_categories(callback, state)


@router.callback_query(BookingFlowStates.BOOKING_HUB, F.data == CB_HUB_STAFF)
async def booking_flow_hub_staff(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(entry_mode="staff_first", staff_step_origin="hub_staff")
    try:
        company_id, _ = await _get_company_context()
        all_services = await _load_services(company_id)
        raw_staff: dict[str, StaffItem] = {}
        services_by_staff: dict[str, list[ServiceItem]] = {}
        for service in all_services:
            resolution = await _load_staff(company_id, service)
            for item in resolution.staff_list:
                raw_staff[item.id] = item
                services_by_staff.setdefault(item.id, []).append(service)
        staff_candidates = sorted(raw_staff.values(), key=lambda item: item.name.lower())
        staff_list = await _filter_staff_with_future_availability(
            callback,
            company_id=company_id,
            staff_list=staff_candidates,
            services_by_staff=services_by_staff,
            flow_step="master_first_staff_picker",
        )
    except asyncio.TimeoutError:
        logger.warning(
            "booking_staff_availability_filter_timeout user_id=%s flow_step=master_first_staff_picker",
            callback.from_user.id,
        )
        await _show_staff_availability_load_error(callback)
        return
    except Exception as exc:
        await _send_yclients_error(callback, exc)
        return

    await state.set_state(BookingFlowStates.CHOOSE_STAFF)
    await state.update_data(
        book_page_staff=0,
        book_staff=[item.__dict__ for item in staff_list],
        book_staff_any=False,
        book_staff_source="availability_filtered",
        staff_step_origin="hub_staff",
    )
    if not staff_list:
        await _answer_with_optional_photo(
            callback,
            text=NO_AVAILABLE_MASTERS_TEXT,
            reply_markup=_build_common_only_keyboard(),
        )
        return
    await _render_staff_selection(callback, staff_list=staff_list, page=0, supports_any_master=False)


@router.callback_query(BookingFlowStates.BOOKING_HUB, F.data == CB_HUB_DATETIME)
async def booking_flow_hub_datetime(callback: CallbackQuery, state: FSMContext) -> None:
    started_at = time.perf_counter()
    company_id = "n/a"
    try:
        company_id, _ = await _get_company_context()
        tz_context = await resolve_company_timezone(company_id)
        available_dates = await asyncio.wait_for(_load_datetime_first_dates(company_id), timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout user_id=%s selected_date=n/a company_id=%s callback_data=%s elapsed_ms=%s",
            callback.from_user.id, company_id, callback.data, int((time.perf_counter() - started_at) * 1000),
        )
        await _answer_with_optional_photo(
            callback,
            text="⏳ Не удалось быстро загрузить свободное время. Попробуйте выбрать дату ещё раз.",
            reply_markup=_build_common_only_keyboard(),
        )
        return
    except Exception as exc:
        logger.exception(
            "booking_slots_load_failed user_id=%s selected_date=n/a company_id=%s callback_data=%s elapsed_ms=%s",
            callback.from_user.id, company_id, callback.data, int((time.perf_counter() - started_at) * 1000),
        )
        await _answer_with_optional_photo(
            callback,
            text="⚠️ Не удалось получить свободное время из YClients. Попробуйте позже.",
            reply_markup=_build_common_only_keyboard(),
        )
        await notify_yclients_exception(callback.bot, exc=exc, action="booking_datetime_first_dates")
        return

    await state.set_state(BookingFlowStates.CHOOSE_PREF_DATE)
    await state.update_data(
        entry_mode="datetime_first",
        selected_date=None,
        selected_time=None,
        selected_datetime=None,
        selected_staff_id=None,
        selected_staff_name=None,
        selected_staff_photo_file_id=None,
        branch_timezone=tz_context.timezone_name,
        yclients_company_id=company_id,
        pref_available_dates=available_dates,
        pref_slots_by_date={},
        pref_selected_slots=[],
    )
    logger.info(
        "booking_datetime_first_state_saved user_id=%s selected_date=n/a company_id=%s branch_timezone=%s fsm_state=%s fsm_data_keys=%s elapsed_ms=%s",
        callback.from_user.id, company_id, tz_context.timezone_name, await state.get_state(), sorted((await state.get_data()).keys()), int((time.perf_counter() - started_at) * 1000),
    )
    if not available_dates:
        await _answer_with_optional_photo(
            callback,
            text=NO_AVAILABLE_DATES_TEXT,
            reply_markup=_build_common_only_keyboard(),
        )
        return
    await _answer_with_optional_photo(
        callback,
        text="📅 Выберите дату с доступными окнами:",
        reply_markup=_build_pref_dates_kb(available_dates, 0),
    )


@router.callback_query(BookingFlowStates.CHOOSE_PREF_DATE, F.data == CB_BACK)
async def booking_flow_pref_date_back(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_booking_hub(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_PREF_DATE, F.data.startswith(f"{CB_WEEK}:"))
async def booking_flow_pref_date_page(callback: CallbackQuery, state: FSMContext) -> None:
    raw_page = callback.data.removeprefix(f"{CB_WEEK}:")
    page = int(raw_page) if raw_page.isdigit() else 0
    await state.set_state(BookingFlowStates.CHOOSE_PREF_DATE)
    data = await state.get_data()
    available_dates = [item for item in data.get("pref_available_dates") or [] if isinstance(item, str)]
    await _answer_with_optional_photo(
        callback,
        text="📅 Выберите дату с доступными окнами:",
        reply_markup=_build_pref_dates_kb(available_dates, page),
    )


@router.callback_query(BookingFlowStates.CHOOSE_PREF_DATE, F.data.startswith(f"{CB_DATE}:"))
async def booking_flow_pref_date_pick(callback: CallbackQuery, state: FSMContext) -> None:
    started_at = time.perf_counter()
    iso_date = callback.data.removeprefix(f"{CB_DATE}:")
    data = await state.get_data()
    fsm_state = await state.get_state()
    company_id = _safe_str(data.get("yclients_company_id")) or "n/a"
    branch_timezone = _safe_str(data.get("branch_timezone")) or "n/a"
    logger.info(
        "booking_datetime_first_date_selected handler=booking_flow_pref_date_pick user_id=%s callback_data=%s selected_date=%s fsm_state=%s fsm_data_keys=%s company_id=%s branch_timezone=%s service_id=%s staff_id=%s",
        callback.from_user.id, callback.data, iso_date, fsm_state, sorted(data.keys()), company_id, branch_timezone, _safe_str(data.get("selected_service_id")) or "n/a", _safe_str(data.get("selected_staff_id")) or "n/a",
    )
    if _safe_str(data.get("entry_mode")) != "datetime_first":
        logger.warning(
            "booking_slots_load_failed user_id=%s selected_date=%s company_id=%s error_summary=stale_or_wrong_entry_mode",
            callback.from_user.id, iso_date, company_id,
        )
        await _answer_with_optional_photo(
            callback,
            text='⚠️ Данные записи устарели. Откройте раздел "Записаться" заново.',
            reply_markup=_build_common_only_keyboard(),
        )
        return

    available_dates = [item for item in data.get("pref_available_dates") or [] if isinstance(item, str)]
    if available_dates and iso_date not in available_dates:
        await callback.answer("😔 На эту дату свободного времени нет. Выберите другую дату.", show_alert=True)
        await _answer_with_optional_photo(
            callback,
            text="📅 Выберите дату с доступными окнами:",
            reply_markup=_build_pref_dates_kb(available_dates, 0),
        )
        return

    try:
        company_id, _ = await _get_company_context()
        tz_context = await resolve_company_timezone(company_id)
        services = await _load_services(company_id)
        slots = await asyncio.wait_for(
            _load_datetime_first_slots_for_date(company_id, iso_date=iso_date, services=services),
            timeout=YCLIENTS_SLOT_TOTAL_TIMEOUT_S,
        )
        raw_slots = [item.__dict__ for item in slots]
        await state.update_data(
            entry_mode="datetime_first",
            selected_date=iso_date,
            selected_time=None,
            selected_datetime=None,
            branch_timezone=tz_context.timezone_name,
            pref_selected_slots=raw_slots,
            pref_slots_by_date={iso_date: raw_slots},
        )
        logger.info(
            "booking_datetime_first_state_saved user_id=%s selected_date=%s company_id=%s branch_timezone=%s fsm_state=%s fsm_data_keys=%s api_call_count=%s elapsed_ms=%s",
            callback.from_user.id, iso_date, company_id, tz_context.timezone_name, await state.get_state(), sorted((await state.get_data()).keys()), "see_slot_loader", int((time.perf_counter() - started_at) * 1000),
        )
    except asyncio.TimeoutError:
        logger.warning(
            "booking_slots_load_timeout user_id=%s selected_date=%s company_id=%s branch_timezone=%s callback_data=%s elapsed_ms=%s",
            callback.from_user.id, iso_date, company_id, branch_timezone, callback.data, int((time.perf_counter() - started_at) * 1000),
        )
        await state.update_data(selected_date=iso_date, selected_time=None, selected_datetime=None)
        await _answer_with_optional_photo(
            callback,
            text="⏳ Не удалось быстро загрузить свободное время. Попробуйте выбрать дату ещё раз.",
            reply_markup=_build_common_only_keyboard(),
        )
        return
    except Exception as exc:
        logger.exception(
            "booking_slots_load_failed user_id=%s selected_date=%s company_id=%s branch_timezone=%s callback_data=%s elapsed_ms=%s error_summary=%s",
            callback.from_user.id, iso_date, company_id, branch_timezone, callback.data, int((time.perf_counter() - started_at) * 1000), str(exc)[:200],
        )
        await _answer_with_optional_photo(
            callback,
            text="⚠️ Не удалось получить свободное время из YClients. Попробуйте позже.",
            reply_markup=_build_common_only_keyboard(),
        )
        await notify_yclients_exception(callback.bot, exc=exc, action="booking_datetime_first_slots")
        return

    await state.set_state(BookingFlowStates.CHOOSE_PREF_TIME)
    if not slots:
        logger.info(
            "booking_slots_empty user_id=%s selected_date=%s company_id=%s branch_timezone=%s elapsed_ms=%s",
            callback.from_user.id, iso_date, company_id, branch_timezone, int((time.perf_counter() - started_at) * 1000),
        )
        await _answer_with_optional_photo(
            callback,
            text="😔 На эту дату свободного времени нет. Выберите другую дату.",
            reply_markup=_build_common_only_keyboard(),
        )
        return
    logger.info(
        "booking_datetime_first_route_next user_id=%s selected_date=%s company_id=%s branch_timezone=%s next_state=%s elapsed_ms=%s slots=%s",
        callback.from_user.id, iso_date, company_id, branch_timezone, await state.get_state(), int((time.perf_counter() - started_at) * 1000), len(slots),
    )
    await _answer_with_optional_photo(
        callback,
        text=f"📅 Дата: {_format_date_russian(iso_date)}\n🕒 Выберите доступное время:",
        reply_markup=_build_pref_times_kb(slots, 0),
    )


@router.callback_query(BookingFlowStates.CHOOSE_PREF_TIME, F.data == CB_BACK)
async def booking_flow_pref_time_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingFlowStates.CHOOSE_PREF_DATE)
    data = await state.get_data()
    available_dates = [item for item in data.get("pref_available_dates") or [] if isinstance(item, str)]
    await _answer_with_optional_photo(
        callback,
        text="📅 Выберите дату с доступными окнами:",
        reply_markup=_build_pref_dates_kb(available_dates, 0),
    )


@router.callback_query(BookingFlowStates.CHOOSE_PREF_TIME, F.data.startswith(f"{CB_TIME}:"))
async def booking_flow_pref_time_pick(callback: CallbackQuery, state: FSMContext) -> None:
    selected_time = callback.data.removeprefix(f"{CB_TIME}:")
    data = await state.get_data()
    selected_date = _safe_str(data.get("selected_date"))
    if not selected_date:
        await booking_flow_pref_time_back(callback, state)
        return
    raw_slots = data.get("pref_selected_slots") or ((data.get("pref_slots_by_date") or {}).get(selected_date) or [])
    slots = [SlotItem(**item) for item in raw_slots if isinstance(item, dict) and item.get("time")]
    chosen = next((item for item in slots if item.time == selected_time), None)
    if not chosen:
        await callback.answer("😔 На выбранное время уже нет свободной записи. Выберите другое время.", show_alert=True)
        await _answer_with_optional_photo(
            callback,
            text=f"📅 Дата: {_format_date_russian(selected_date)}\n🕒 Выберите доступное время:",
            reply_markup=_build_pref_times_kb(slots, 0),
        )
        return
    selected_datetime = chosen.datetime_iso or f"{selected_date}T{selected_time}:00"
    logger.info(
        "booking_datetime_first_state_preserved tg_user_id=%s entry_mode=%s selected_date=%s selected_time=%s selected_datetime=%s callback_data=%s",
        callback.from_user.id,
        _safe_str(data.get("entry_mode")) or "n/a",
        selected_date,
        selected_time,
        selected_datetime,
        callback.data,
    )
    await state.update_data(selected_time=selected_time, selected_datetime=selected_datetime, reschedule_new_datetime=selected_datetime)
    await _show_categories(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_CATEGORY, F.data == CB_BACK)
async def booking_flow_category_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    entry_mode = _safe_str(data.get("entry_mode"))
    if entry_mode == "staff_first":
        await state.update_data(staff_step_origin="hub_staff")
        await booking_flow_hub_staff(callback, state)
        return
    if entry_mode == "datetime_first":
        await state.set_state(BookingFlowStates.CHOOSE_PREF_TIME)
        selected_date = _safe_str(data.get("selected_date"))
        raw_slots = ((data.get("pref_slots_by_date") or {}).get(selected_date) or [])
        slots = [SlotItem(**item) for item in raw_slots if isinstance(item, dict) and item.get("time")]
        await _answer_with_optional_photo(
            callback,
            text="🕒 Выберите доступное время:",
            reply_markup=_build_pref_times_kb(slots, 0),
        )
        return
    await _show_booking_hub(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_CATEGORY, F.data.startswith(f"{CB_PAGE_CATEGORY}:"))
async def booking_flow_category_page(callback: CallbackQuery, state: FSMContext) -> None:
    raw_page = callback.data.removeprefix(f"{CB_PAGE_CATEGORY}:")
    page = int(raw_page) if raw_page.isdigit() else 0
    await _show_categories(callback, state, page=page)


@router.callback_query(BookingFlowStates.CHOOSE_CATEGORY, F.data.startswith(f"{CB_CATEGORY}:"))
async def booking_flow_choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = callback.data.removeprefix(f"{CB_CATEGORY}:")
    await _show_services(callback, state, category_id=category_id)


@router.callback_query(BookingFlowStates.CHOOSE_SERVICE, F.data == CB_BACK)
async def booking_flow_service_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = int(data.get("book_page_category", 0) or 0)
    await _show_categories(callback, state, page=page)


@router.callback_query(BookingFlowStates.CHOOSE_SERVICE, F.data.startswith(f"{CB_PAGE_SERVICE}:"))
async def booking_flow_service_page(callback: CallbackQuery, state: FSMContext) -> None:
    raw_page = callback.data.removeprefix(f"{CB_PAGE_SERVICE}:")
    page = int(raw_page) if raw_page.isdigit() else 0
    data = await state.get_data()
    selected_category_id = data.get("selected_category_id")
    raw_services = data.get("book_services") or []
    services = [s for s in _deserialize_services(raw_services) if s.category_id == selected_category_id]
    await state.update_data(book_page_service=page)
    keyboard = _build_services_kb(services, page)
    await _safe_edit_text(callback, text="Выберите услугу ✂️", reply_markup=keyboard)


@router.callback_query(BookingFlowStates.CHOOSE_SERVICE, F.data.startswith(f"{CB_SERVICE}:"))
async def booking_flow_choose_service(callback: CallbackQuery, state: FSMContext) -> None:
    service_id = callback.data.removeprefix(f"{CB_SERVICE}:")
    data = await state.get_data()
    selected_category_id = data.get("selected_category_id")
    raw_services = data.get("book_services") or []
    services = _deserialize_services(raw_services)
    service = next((item for item in services if item.id == service_id and item.category_id == selected_category_id), None)
    if not service:
        await callback.answer("Не удалось выбрать услугу. Попробуйте ещё раз 🙂", show_alert=True)
        return

    selected_staff_id = _safe_str(data.get("selected_staff_id"))
    if selected_staff_id:
        try:
            company_id, _ = await _get_company_context()
            if not await _is_service_compatible_with_staff(company_id, service.id, selected_staff_id):
                await state.update_data(selected_service_id=None, selected_service_name=None, selected_service_price=None, selected_service_duration=None)
                await callback.answer("😔 Эта услуга недоступна у выбранного мастера. Пожалуйста, выберите другую услугу.", show_alert=True)
                await _show_categories(callback, state)
                return
        except Exception as exc:
            await _send_yclients_error(callback, exc)
            return

    entry_mode = _safe_str(data.get("entry_mode"))
    update_payload: dict[str, Any] = {
        "selected_service_id": service.id,
        "selected_service_name": service.name,
        "selected_service_price": service.price,
        "selected_service_duration": service.duration,
    }
    if entry_mode == "datetime_first":
        logger.info(
            "booking_datetime_first_state_preserved tg_user_id=%s entry_mode=%s selected_date=%s selected_time=%s selected_datetime=%s selected_service_id=%s callback_data=%s",
            callback.from_user.id,
            entry_mode,
            _safe_str(data.get("selected_date")) or "n/a",
            _safe_str(data.get("selected_time")) or "n/a",
            _safe_str(data.get("selected_datetime")) or "n/a",
            service.id,
            callback.data,
        )
    elif entry_mode not in {"staff_first", "datetime_first"}:
        update_payload.update(selected_time=None, selected_datetime=None, reschedule_new_datetime=None)
        update_payload.update(
            selected_staff_id=None,
            selected_staff_name=None,
            selected_staff_photo_file_id=None,
            selected_date=None,
        )
    await state.update_data(**update_payload)
    if entry_mode == "staff_first":
        await _show_date_picker(callback, state)
        return
    await state.update_data(staff_step_origin="service")
    await _show_staff(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_STAFF, F.data == CB_BACK)
async def booking_flow_staff_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if _safe_str(data.get("staff_step_origin")) == "hub_staff":
        await _show_booking_hub(callback, state)
        return
    selected_category_id = _safe_str(data.get("selected_category_id"))
    page = int(data.get("book_page_service", 0) or 0)
    await _show_services(callback, state, category_id=selected_category_id, page=page)


@router.callback_query(BookingFlowStates.CHOOSE_STAFF, F.data.startswith(f"{CB_PAGE_STAFF}:"))
async def booking_flow_staff_page(callback: CallbackQuery, state: FSMContext) -> None:
    raw_page = callback.data.removeprefix(f"{CB_PAGE_STAFF}:")
    page = int(raw_page) if raw_page.isdigit() else 0
    data = await state.get_data()
    staff_list = _deserialize_staff(data.get("book_staff") or [])
    supports_any_master = bool(data.get("book_staff_any"))
    await state.update_data(book_page_staff=page)
    if not await _ensure_filtered_staff_source(callback, state, staff_list):
        return

    await _render_staff_selection(callback, staff_list=staff_list, page=page, supports_any_master=supports_any_master)


@router.callback_query(BookingFlowStates.CHOOSE_STAFF, F.data == CB_STAFF_ANY)
async def booking_flow_choose_staff_any(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("book_staff_any"):
        await callback.answer("Эта опция сейчас недоступна 🙂", show_alert=True)
        return

    data = await state.get_data()
    await state.update_data(selected_staff_id=None, selected_staff_name="Любой мастер", selected_staff_photo_file_id=None)
    if _safe_str(data.get("staff_step_origin")) == "hub_staff":
        await _show_categories(callback, state)
        return
    if _safe_str(data.get("entry_mode")) == "datetime_first" and _safe_str(data.get("selected_date")) and _safe_str(data.get("selected_time")):
        await _route_to_confirmation_after_datetime_first_selection(callback, state)
        return
    await _show_date_picker(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_STAFF, F.data.startswith(f"{CB_STAFF}:"))
async def booking_flow_choose_staff(callback: CallbackQuery, state: FSMContext) -> None:
    staff_id = callback.data.removeprefix(f"{CB_STAFF}:")
    data = await state.get_data()
    staff_list = _deserialize_staff(data.get("book_staff") or [])
    selected = next((item for item in staff_list if item.id == staff_id), None)
    if not selected:
        await callback.answer("Не удалось выбрать мастера. Попробуйте ещё раз 🙂", show_alert=True)
        return

    data = await state.get_data()
    await state.update_data(selected_staff_id=selected.id, selected_staff_name=selected.name, selected_staff_photo_file_id=None)
    selected_service_id = _safe_str(data.get("selected_service_id"))
    if selected_service_id:
        try:
            company_id, _ = await _get_company_context()
            if not await _is_service_compatible_with_staff(company_id, selected_service_id, selected.id):
                await state.update_data(selected_staff_id=None, selected_staff_name=None, selected_staff_photo_file_id=None)
                await callback.answer("😔 Эта услуга недоступна у выбранного мастера. Пожалуйста, выберите другого специалиста.", show_alert=True)
                await _show_staff(callback, state, page=int(data.get("book_page_staff", 0) or 0))
                return
        except Exception as exc:
            await _send_yclients_error(callback, exc)
            return
    if _safe_str(data.get("staff_step_origin")) == "hub_staff":
        await _show_categories(callback, state)
        return
    if _safe_str(data.get("entry_mode")) == "datetime_first" and _safe_str(data.get("selected_date")) and _safe_str(data.get("selected_time")):
        await _route_to_confirmation_after_datetime_first_selection(callback, state)
        return
    await _show_date_picker(callback, state)


@router.callback_query(BookingFlowStates.CHOOSE_DATE, F.data == CB_BACK)
async def booking_flow_date_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    service_id = _safe_str(data.get("selected_service_id"))
    if not service_id:
        service_id = _safe_str(data.get("service_id"))
        if service_id:
            await state.update_data(selected_service_id=service_id)
    if not service_id:
        fallback_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BOOK_APPOINTMENT_BTN, callback_data=f"{CB_PREFIX}:start")],
                [InlineKeyboardButton(text=HOME, callback_data=CB_HOME)],
            ]
        )
        if callback.message:
            await callback.message.edit_text("⚠️ Данные записи устарели. Начните запись заново.", reply_markup=fallback_kb)
        await callback.answer()
        return

    page = int(data.get("book_page_staff", 0) or 0)
    staff_list = _deserialize_staff(data.get("book_staff") or [])
    supports_any_master = bool(data.get("book_staff_any"))
    source = _safe_str(data.get("book_staff_source"))
    if not staff_list or (source and source not in ALLOWED_STAFF_SOURCES and not source.startswith("cache:")) or not source:
        await _show_staff(callback, state, page=page)
        return

    await state.set_state(BookingFlowStates.CHOOSE_STAFF)
    if not await _ensure_filtered_staff_source(callback, state, staff_list):
        return

    await _render_staff_selection(callback, staff_list=staff_list, page=page, supports_any_master=supports_any_master)


@router.callback_query(BookingFlowStates.CHOOSE_DATE, F.data.startswith(f"{CB_WEEK}:"))
async def booking_flow_date_week(callback: CallbackQuery, state: FSMContext) -> None:
    raw_offset = callback.data.removeprefix(f"{CB_WEEK}:")
    offset = int(raw_offset) if raw_offset.isdigit() else 0
    await _show_date_picker(callback, state, week_offset=max(0, offset))


@router.callback_query(BookingFlowStates.CHOOSE_DATE, F.data.startswith(f"{CB_DATE}:"))
async def booking_flow_choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    iso_date = callback.data.removeprefix(f"{CB_DATE}:")
    await _show_slots(callback, state, iso_date=iso_date)


@router.callback_query(BookingFlowStates.CHOOSE_TIME, F.data == CB_BACK)
async def booking_flow_time_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    week_offset = int(data.get("book_week_offset", 0) or 0)
    await _show_date_picker(callback, state, week_offset=week_offset)


@router.callback_query(BookingFlowStates.CHOOSE_TIME, F.data.startswith(f"{CB_PAGE_TIME}:"))
async def booking_flow_time_page(callback: CallbackQuery, state: FSMContext) -> None:
    raw_page = callback.data.removeprefix(f"{CB_PAGE_TIME}:")
    page = int(raw_page) if raw_page.isdigit() else 0
    data = await state.get_data()
    selected_date = _safe_str(data.get("selected_date"))
    slots = [SlotItem(**item) for item in data.get("book_slots") or [] if isinstance(item, dict) and item.get("time")]
    await state.update_data(book_slot_page=page)
    if not slots:
        if not selected_date:
            await _reply_booking_step_error(callback)
            return
        await _show_slots(callback, state, iso_date=selected_date, page=0)
        return

    await _answer_with_optional_photo(
        callback,
        text=_booking_step_text(data, tail="🕐 Выберите удобное время:"),
        reply_markup=_build_slots_kb(slots, page),
        photo=await _get_selected_master_photo_file_id(state),
    )


@router.callback_query(BookingFlowStates.CHOOSE_TIME, F.data.startswith(f"{CB_TIME}:"))
async def booking_flow_choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    selected_time = callback.data.removeprefix(f"{CB_TIME}:")
    data = await state.get_data()
    selected_date = _safe_str(data.get("selected_date"))
    slots = [SlotItem(**item) for item in data.get("book_slots") or [] if isinstance(item, dict) and item.get("time")]
    chosen = next((slot for slot in slots if slot.time == selected_time), None)
    if not chosen:
        await callback.answer("😔 Это окно уже неактуально. Обновляю список 🙂", show_alert=True)
        if not selected_date:
            await _reply_booking_step_error(callback)
            return
        await _show_slots(callback, state, iso_date=selected_date, page=0)
        return

    selected_datetime = chosen.datetime_iso or f"{selected_date}T{selected_time}:00"
    await state.update_data(
        selected_time=selected_time,
        selected_datetime=selected_datetime,
        reschedule_new_datetime=selected_datetime,
    )
    fresh_data = await state.get_data()
    if _safe_str(fresh_data.get("my_bookings_mode")) == "reschedule":
        await _prompt_for_reschedule_confirmation(callback, state)
        return
    await _prompt_for_phone(callback, state)


@router.message(BookingFlowStates.WAIT_PHONE)
async def booking_flow_wait_phone(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    if text == BACK:
        selected_date = _safe_str(data.get("selected_date"))
        page = int(data.get("book_slot_page", 0) or 0)
        await message.answer("↩️ Возвращаемся к выбору времени.", reply_markup=ReplyKeyboardRemove())
        if not selected_date:
            await _reply_booking_step_error(message)
            return
        await _show_slots(message, state, iso_date=selected_date, page=page)
        return
    if text == HOME:
        await clear_state_preserving_navigation(state)
        await message.answer("Возвращаю в главное меню 🏠", reply_markup=ReplyKeyboardRemove())
        await render_main_menu(message, message.from_user.id)
        return

    raw_phone = message.contact.phone_number if message.contact else (message.text or "")
    if (message.text or "").strip() == BOOKING_USE_REGISTERED_PHONE:
        raw_phone = str(data.get("registered_phone") or "")
    normalized = _normalize_phone(raw_phone)
    if not normalized:
        await message.answer("😔 Номер выглядит неверно. Отправьте телефон в формате +79991234567 🙂")
        return

    await state.update_data(client_phone=normalized)
    if not await _revalidate_before_final_step(message, state):
        return
    updated_data = await state.get_data()
    company_id = _safe_str(updated_data.get("yclients_company_id"))
    if not company_id:
        credentials, _ = await get_yclients_credentials()
        company_id = credentials.company_id
    await state.set_state(BookingFlowStates.CONFIRM_PHONE)
    await _send_confirmation_with_optional_photo(
        message,
        state,
        await _build_summary_text(updated_data, phone=normalized, company_id=company_id),
    )


@router.callback_query(BookingFlowStates.WAIT_PHONE, F.data == CB_PHONE_BACK)
async def booking_flow_phone_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected_date = _safe_str(data.get("selected_date"))
    page = int(data.get("book_slot_page", 0) or 0)
    if not selected_date:
        await _reply_booking_step_error(callback)
        return
    await _show_slots(callback, state, iso_date=selected_date, page=page)


@router.callback_query(BookingFlowStates.CONFIRM_PHONE, F.data == CB_PHONE_BACK)
async def booking_flow_phone_confirm_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingFlowStates.WAIT_PHONE)
    data = await state.get_data()
    registered_phone = _normalize_phone(str(data.get("registered_phone") or ""))
    if callback.message:
        if registered_phone:
            await callback.message.answer(
                "📱 Чтобы записать вас, отправьте номер телефона 😊\n\nМожно использовать номер из регистрации:\n"
                f"{registered_phone}",
                reply_markup=_build_phone_reply_kb(include_registered_phone=True),
            )
        else:
            await callback.message.answer(
                "📱 Чтобы записать вас, отправьте номер телефона 😊",
                reply_markup=_build_phone_reply_kb(include_registered_phone=False),
            )
    await callback.answer()


@router.callback_query(BookingFlowStates.CONFIRM_PHONE, F.data == CB_CONFIRM_CANCEL)
async def booking_flow_confirm_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    if callback.message:
        await callback.message.answer("❌ Запись отменена.", reply_markup=ReplyKeyboardRemove())
    await render_main_menu(callback, callback.from_user.id)
    await callback.answer()


@router.callback_query(BookingFlowStates.CONFIRM_PHONE, F.data == CB_CONFIRM_FINAL)
async def booking_flow_confirm_final(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    if not acquire_action_lock("booking_create", user_id, ttl_s=CONFIRM_LOCK_SECONDS):
        await callback.answer("⏳ Уже создаём запись, секундочку 🙂", show_alert=True)
        return

    try:
        data = await state.get_data()
        is_reschedule = _safe_str(data.get("my_bookings_mode")) == "reschedule"
        fullname = _safe_str(data.get("client_fullname")) or "Гость"
        phone = _normalize_phone(_safe_str(data.get("client_phone")) or "")
        if not phone and not is_reschedule:
            await state.set_state(BookingFlowStates.WAIT_PHONE)
            if callback.message:
                await callback.message.answer(
                    "😔 Номер выглядит неверно. Отправьте телефон в формате +79991234567 🙂",
                    reply_markup=_build_phone_reply_kb(include_registered_phone=bool(data.get("registered_phone"))),
                )
            await callback.answer()
            return

        if not await _revalidate_before_final_step(callback, state):
            return
        await _create_booking_and_show_success(callback, state, fullname=fullname, phone=phone)
    finally:
        release_action_lock("booking_create", user_id)


@router.callback_query(F.data == CB_MY_BOOKINGS)
async def booking_flow_my_bookings(callback: CallbackQuery, state: FSMContext) -> None:
    from app.handlers.my_bookings import my_bookings_from_flow

    await my_bookings_from_flow(callback, state)


async def _create_booking_and_show_success(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    fullname: str,
    phone: str,
) -> None:
    from app.handlers.my_bookings import invalidate_user_my_bookings_cache
    try:
        result = await _create_booking_core(state, user_id=callback.from_user.id, fullname=fullname, phone=phone, bot=callback.bot)
    except Exception as exc:
        await _handle_booking_create_error(callback, state, exc)
        return

    data = await state.get_data()
    merged_data = {**data, "selected_datetime": result["datetime_iso"]}
    photo_file_id = await _get_selected_master_photo_file_id(state)
    success_text = (
        "✅ Готово! Вы записаны 💈\n\n"
        f"{await _build_summary_text(merged_data, include_header=False, company_id=result.get('company_id'))}"
    )
    await state.clear()
    if callback.message:
        if photo_file_id:
            await callback.message.answer_photo(photo=photo_file_id, caption=success_text)
        else:
            await callback.message.answer(success_text)
    try:
        await record_delivery_decision(
            client_tg_id=callback.from_user.id,
            notification_type="booking_confirmation",
            category="service",
            decision="allowed_white_service_notification",
            source_event_id=f"booking_confirmation:{result['booking_id']}",
        )
    except Exception:
        logger.exception(
            "booking_confirmation_delivery_decision_log_failed context=%s user_tg_id=%s booking_id=%s staff_id=%s service_ids=%s selected_datetime=%s",
            "booking_flow_create_success",
            callback.from_user.id,
            result.get("booking_id"),
            merged_data.get("selected_staff_id"),
            merged_data.get("selected_service_id"),
            result.get("datetime_iso"),
        )
    await render_main_menu(callback, callback.from_user.id)
    if result.get("warning") == "cancel_old_failed" and callback.message:
        await callback.message.answer("⚠️ Новая запись создана, но старая запись пока не была отменена. Мы уже занимаемся этим 🙂")
    logger.info(
        "booking_create_success booking_id=%s service=%s staff=%s datetime=%s",
        result["booking_id"],
        merged_data.get("selected_service_id"),
        merged_data.get("selected_staff_id"),
        result["datetime_iso"],
    )
    invalidate_user_my_bookings_cache(callback.from_user.id)
    await callback.answer()


def _is_time_unavailable_error(exc: Exception) -> bool:
    snippet = _safe_str(getattr(exc, "response_snippet", None)).lower()
    if not snippet:
        return False
    unavailable_markers = (
        "недоступ",
        "занят",
        "выбранное время",
        "time is unavailable",
        "slot",
    )
    validation_markers = (
        "не передан обязательный параметр",
        "обязательн",
        "validation",
        "required",
        '"errors"',
    )
    if any(marker in snippet for marker in validation_markers):
        return False
    return any(marker in snippet for marker in unavailable_markers)


async def _handle_booking_create_error(target: Message | CallbackQuery, state: FSMContext, exc: Exception) -> None:
    trace_id = getattr(exc, "trace_id", None) or "n/a"
    status = getattr(exc, "status_code", None)
    snippet = (getattr(exc, "response_snippet", None) or str(exc))[:500]
    data = await state.get_data()
    endpoint = getattr(exc, "endpoint", None) or _safe_str((data.get("reschedule_request_debug") or {}).get("endpoint")) or "/unknown"
    is_reschedule = _safe_str(data.get("my_bookings_mode")) == "reschedule"
    ctx = data.get("reschedule_context") if isinstance(data.get("reschedule_context"), dict) else {}
    req_debug = data.get("reschedule_request_debug") if isinstance(data.get("reschedule_request_debug"), dict) else {}
    step_failed = _safe_str(req_debug.get("step")) or ("create_new" if is_reschedule else "create_booking")
    company_id = _safe_str(ctx.get("company_id") or data.get("yclients_company_id")) or "n/a"
    record_id = _safe_str(ctx.get("record_id") or data.get("reschedule_old_record_id") or data.get("my_bookings_origin_record_id")) or "n/a"
    new_record_id = _safe_str(req_debug.get("new_record_id")) or "n/a"
    method = _safe_str(getattr(exc, "method", None) or req_debug.get("method")) or "n/a"
    staff_id = _safe_str(ctx.get("staff_id") or data.get("selected_staff_id")) or "n/a"
    service_ids = ctx.get("service_ids") if isinstance(ctx.get("service_ids"), list) else []
    services = ",".join([_safe_str(x) for x in service_ids if _safe_str(x)]) or (_safe_str(data.get("selected_service_id")) or "n/a")
    client_id = _safe_str(ctx.get("client_id")) or "n/a"
    seance_length = _safe_str(ctx.get("seance_length")) or "n/a"
    datetime_iso = _safe_str(data.get("selected_datetime")) or "n/a"
    state_keys = ",".join(sorted(data.keys())) or "n/a"
    local_payload_keys = req_debug.get("local_payload_keys") if isinstance(req_debug.get("local_payload_keys"), list) else []
    local_payload_keys_text = ",".join([_safe_str(k) for k in local_payload_keys if _safe_str(k)]) or "n/a"
    local_payload_has_id = req_debug.get("local_payload_has_id") if isinstance(req_debug.get("local_payload_has_id"), bool) else False
    local_payload_preview = _safe_str(req_debug.get("local_payload_preview")) or "n/a"
    transport_intent = _safe_str(req_debug.get("transport_intent")) or "n/a"
    transport_content_type_intent = _safe_str(req_debug.get("transport_content_type_intent")) or "n/a"
    transport_form_keys = req_debug.get("transport_form_keys") if isinstance(req_debug.get("transport_form_keys"), list) else []
    transport_form_keys_text = ",".join([_safe_str(k) for k in transport_form_keys if _safe_str(k)]) or "n/a"
    transport_form_has_id = req_debug.get("transport_form_has_id") if isinstance(req_debug.get("transport_form_has_id"), bool) else False
    transport_preview = _safe_str(req_debug.get("transport_preview")) or "n/a"
    transport_debug = getattr(exc, "transport_debug", None) if isinstance(getattr(exc, "transport_debug", None), dict) else {}
    wire_body_transport = _safe_str(transport_debug.get("body_transport")) or "n/a"
    wire_content_type = _safe_str(transport_debug.get("content_type")) or "n/a"
    wire_uses_json = "yes" if transport_debug.get("uses_json_arg") is True else ("no" if transport_debug.get("uses_json_arg") is False else "n/a")
    wire_uses_data = "yes" if transport_debug.get("uses_data_arg") is True else ("no" if transport_debug.get("uses_data_arg") is False else "n/a")
    wire_payload_keys = transport_debug.get("payload_keys") if isinstance(transport_debug.get("payload_keys"), list) else []
    wire_payload_keys_text = ",".join([_safe_str(k) for k in wire_payload_keys if _safe_str(k)]) or "n/a"
    wire_body_preview = _safe_str(transport_debug.get("body_preview")) or "n/a"
    contract_fix = _safe_str(req_debug.get("contract_fix")) or "n/a"
    traceback_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:])[:600]
    _log_booking_issue(
        "booking_create_failed",
        user_id=(target.from_user.id if isinstance(target, CallbackQuery) else target.from_user.id),
        entry_mode=_safe_str(data.get("entry_mode")) or None,
        staff_id=staff_id if staff_id != "n/a" else None,
        service_id=services if services != "n/a" else None,
        selected_datetime=datetime_iso if datetime_iso != "n/a" else None,
        callback_data=(target.data if isinstance(target, CallbackQuery) else None),
        endpoint=endpoint,
        method=method,
        summary=type(exc).__name__,
    )
    missing_fields = "n/a"
    if isinstance(exc, RuntimeError) and str(exc).startswith("RESCHEDULE_CONTEXT_MISSING:"):
        missing_fields = str(exc).split(":", 1)[1] or "n/a"
    if isinstance(exc, RuntimeError) and str(exc).startswith("RESCHEDULE_PAYLOAD_MISSING:"):
        missing_fields = str(exc).split(":", 1)[1] or "n/a"

    title = "🚨 YClients reschedule failed" if is_reschedule else "🚨 YClients create booking failed"
    bot = target.bot if isinstance(target, CallbackQuery) else target.bot
    try:
        await bot.send_message(
            DEV_DIAGNOSTICS_TG_ID,
            f"""{title}
🧩 trace_id: {trace_id}
🪜 step_failed: {step_failed}
➡️ endpoint: {endpoint}
🛠 method: {method}
📡 status: {status or 'n/a'}
🏢 company_id: {company_id}
🆔 old_record_id: {record_id}
🆕 new_record_id: {new_record_id}
👤 staff_id: {staff_id}
✂️ services: {services}
🙍 client_id: {client_id}
⏱ seance_length: {seance_length}
🕒 datetime: {datetime_iso}
🧱 local_payload_keys: {local_payload_keys_text}
🔖 local_payload_has_id: {'yes' if local_payload_has_id else 'no'}
🧪 local_payload_preview: {local_payload_preview}
🧰 transport_intent: {transport_intent}
📮 transport_content_type_intent: {transport_content_type_intent}
🧾 transport_form_keys: {transport_form_keys_text}
🏷 transport_form_has_id: {'yes' if transport_form_has_id else 'no'}
🧪 transport_preview: {transport_preview}
🌐 wire_body_transport: {wire_body_transport}
🌐 wire_content_type: {wire_content_type}
🌐 wire_uses_json_arg: {wire_uses_json}
🌐 wire_uses_data_arg: {wire_uses_data}
🌐 wire_payload_keys: {wire_payload_keys_text}
🌐 wire_body_preview: {wire_body_preview}
🧭 contract_fix: {contract_fix}
🗂 state_keys: {state_keys}
🧪 missing_fields: {missing_fields}
📄 response: {snippet}
🪵 traceback_last_lines:
{traceback_tail or 'n/a'}""",
        )
    except Exception:
        logger.exception("Failed to send booking diagnostics")

    if isinstance(exc, RuntimeError) and (
        str(exc).startswith("RESCHEDULE_CONTEXT_MISSING:")
        or str(exc).startswith("RESCHEDULE_PAYLOAD_MISSING:")
        or str(exc).startswith("RESCHEDULE_CREATE_PHONE_MISSING:")
    ):
        text = "⚠️ Не удалось подготовить перенос записи. Попробуйте ещё раз 🙂"
    elif isinstance(exc, YClientsAuthError) or status in {401, 403}:
        text = "❌ Нет доступа к YClients. Проверьте токены ⚙️"
    elif is_reschedule and _is_time_unavailable_error(exc):
        text = "😕 Это время уже недоступно. Выберите другое 🙂"
        await state.set_state(BookingFlowStates.CHOOSE_TIME)
    elif is_reschedule:
        text = "⚠️ Не удалось перенести запись. Попробуйте ещё раз 🙂"
    else:
        text = "⚠️ Техническая ошибка. Попробуйте ещё раз через минуту."

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text)
        await target.answer()
    else:
        await target.answer(text)


async def _create_booking_core(
    state: FSMContext,
    *,
    user_id: int,
    fullname: str,
    phone: str,
    bot: Any,
) -> dict[str, str]:
    company_id, has_user_token = await _get_company_context()

    from app.handlers.my_bookings import maybe_process_reschedule_final

    rescheduled = await maybe_process_reschedule_final(
        state=state,
        user_id=user_id,
        fullname=fullname,
        phone=phone,
        bot=bot,
    )
    if rescheduled is not None:
        return rescheduled

    await state.update_data(yclients_company_id=company_id)
    if not has_user_token:
        raise YClientsCredentialsError("Missing user token")

    data = await state.get_data()

    service_id = _safe_str(data.get("selected_service_id"))
    datetime_iso = _safe_str(data.get("selected_datetime"))
    if not service_id or not datetime_iso:
        raise RuntimeError("Missing booking context in FSM")

    staff_id = _safe_str(data.get("selected_staff_id")) or None
    client, _ = await _build_client()
    bot_comment_tag = await _build_booking_bot_comment_tag(company_id)
    booking_source = _safe_str(data.get("booking_source")) or None
    booking_origin_type = _safe_str(data.get("booking_origin")) or _safe_str(data.get("booking_origin_type")) or None
    lost_days_raw = data.get("lost_days")
    lost_days = int(lost_days_raw) if isinstance(lost_days_raw, int) or (isinstance(lost_days_raw, str) and str(lost_days_raw).isdigit()) else None
    logger.info(
        "booking_origin_before_create user_tg_id=%s lost_days=%s booking_origin=%s fsm_keys=%s",
        user_id,
        lost_days if lost_days is not None else "n/a",
        booking_origin_type or "n/a",
        ",".join(sorted(data.keys())),
    )
    birthday_event_id_raw = data.get("birthday_event_id")
    birthday_discount_context = bool(data.get("birthday_discount_context"))
    birthday_is_test = bool(data.get("birthday_is_test") or data.get("notification_is_test"))
    birthday_source = _safe_str(data.get("birthday_source") or data.get("notification_source")) or None
    bot_comment_tag = apply_birthday_warning(
        bot_comment_tag,
        booking_source=booking_source,
        birthday_discount_context=birthday_discount_context,
    )
    if booking_source == "birthday_funnel" and birthday_discount_context and BIRTHDAY_WARNING in bot_comment_tag:
        logger.info(
            "birthday_booking_context_applied_to_yclients_comment user_tg_id=%s birthday_event_id=%s is_test=%s source=%s",
            user_id,
            birthday_event_id_raw if birthday_event_id_raw is not None else "n/a",
            birthday_is_test,
            birthday_source or "n/a",
        )
    logger.info(
        "booking_comment_before_create user_tg_id=%s booking_origin=%s lost_days=%s comment_keys=%s",
        user_id,
        booking_origin_type or "n/a",
        lost_days if lost_days is not None else "n/a",
        "comment",
    )
    final_comment = _append_lost_client_discount_comment(
        bot_comment_tag,
        booking_origin_type=booking_origin_type,
        lost_days=lost_days,
    )
    logger.info(
        "booking_comment_after_origin_append user_tg_id=%s booking_origin=%s lost_days=%s comment_keys=%s",
        user_id,
        booking_origin_type or "n/a",
        lost_days if lost_days is not None else "n/a",
        "comment",
    )
    if final_comment != bot_comment_tag:
        logger.info(
            "lost_client_comment_appended user_tg_id=%s booking_origin=%s lost_days=%s yclients_record_id=%s",
            user_id,
            booking_origin_type or "n/a",
            lost_days if lost_days is not None else "n/a",
            "pending",
        )
    else:
        logger.info(
            "lost_client_comment_missing user_tg_id=%s booking_origin=%s lost_days=%s yclients_record_id=%s",
            user_id,
            booking_origin_type or "n/a",
            lost_days if lost_days is not None else "n/a",
            "pending",
        )
    try:
        created = await create_booking_or_visit(
            client,
            company_id=company_id,
            service_id=service_id,
            staff_id=staff_id,
            datetime_iso=datetime_iso,
            phone=phone,
            fullname=fullname,
            comment=final_comment,
        )
    finally:
        await client.close()
    logger.info(
        "booking_created_yclients_success tg_id=%s yclients_record_id=%s booking_datetime=%s status=%s",
        user_id,
        created.record_id,
        created.datetime or datetime_iso,
        "created",
    )

    logger.info(
        "booking_local_save_started tg_id=%s yclients_record_id=%s booking_datetime=%s status=%s",
        user_id,
        created.record_id,
        datetime_iso,
        "created",
    )
    logger.info(
        "booking_link_save_start context=%s user_tg_id=%s staff_id=%s service_ids=%s selected_datetime=%s yclients_record_id=%s",
        "booking_flow_create_booking_core",
        user_id,
        staff_id or "n/a",
        service_id,
        datetime_iso,
        created.record_id,
    )
    try:
        await create_booking_link(
            tg_user_id=user_id,
            yclients_record_id=created.record_id,
            company_id=company_id,
            service_id=service_id,
            staff_id=staff_id,
            datetime_iso=datetime_iso,
            status="created",
            raw_payload=created.raw_payload,
        )
    except Exception:
        logger.exception(
            "booking_local_save_failed tg_id=%s yclients_record_id=%s booking_datetime=%s status=%s",
            user_id,
            created.record_id,
            datetime_iso,
            "created",
        )
        raise
    logger.info(
        "booking_local_save_finished tg_id=%s yclients_record_id=%s booking_datetime=%s status=%s",
        user_id,
        created.record_id,
        datetime_iso,
        "created",
    )
    logger.info(
        "booking_link_save_success context=%s user_tg_id=%s staff_id=%s service_ids=%s selected_datetime=%s yclients_record_id=%s",
        "booking_flow_create_booking_core",
        user_id,
        staff_id or "n/a",
        service_id,
        datetime_iso,
        created.record_id,
    )
    client_id = _safe_str(data.get("yclients_client_id") or data.get("resolved_client_id") or data.get("client_id")) or None
    await upsert_telegram_attribution(
        company_id=company_id,
        record_id=created.record_id,
        client_id=client_id,
        created_via="booking",
    )
    if booking_source == "birthday_funnel":
        if isinstance(birthday_event_id_raw, int):
            try:
                await mark_birthday_status(birthday_event_id_raw, "booked_from_birthday_gift", booking_id=created.record_id)
            except Exception:
                logger.exception(
                    "birthday_booking_status_mark_failed user_tg_id=%s birthday_event_id=%s is_test=%s source=%s yclients_booking_id=%s",
                    user_id,
                    birthday_event_id_raw,
                    birthday_is_test,
                    birthday_source or "n/a",
                    created.record_id,
                )
        await state.update_data(
            booking_source=None,
            birthday_event_id=None,
            birthday_discount_context=None,
            birthday_is_test=None,
            birthday_source=None,
            birthday_claimed_at_utc=None,
        )
        logger.info(
            "birthday_booking_context_cleared user_tg_id=%s birthday_event_id=%s is_test=%s source=%s yclients_booking_id=%s",
            user_id,
            birthday_event_id_raw if birthday_event_id_raw is not None else "n/a",
            birthday_is_test,
            birthday_source or "n/a",
            created.record_id,
        )

    try:
        if not await has_booking_attribution(created.record_id):
            booking_created_at_utc = datetime.now(timezone.utc).isoformat()
            click = await find_last_click(client_tg_id=user_id, yclients_client_id=client_id, booking_created_at_utc=booking_created_at_utc, window_days=7)
            if click:
                await mark_attributed(
                    attribution_id=int(click['id']),
                    booking_id=created.record_id,
                    booking_created_at_utc=booking_created_at_utc,
                    revenue=None,
                )
    except Exception:
        logger.exception(
            "booking_notification_attribution_failed context=%s user_tg_id=%s staff_id=%s service_ids=%s selected_datetime=%s yclients_record_id=%s yclients_client_id=%s",
            "booking_flow_create_booking_core",
            user_id,
            staff_id or "n/a",
            service_id,
            datetime_iso,
            created.record_id,
            client_id or "n/a",
        )
    return {"datetime_iso": created.datetime or datetime_iso, "booking_id": created.record_id, "company_id": company_id}
