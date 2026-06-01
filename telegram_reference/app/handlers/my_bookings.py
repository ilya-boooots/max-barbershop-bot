from __future__ import annotations

import logging
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.action_locks import acquire_action_lock, release_action_lock
from app.core.config import get_settings
from app.db.telegram_attribution_repo import deactivate_telegram_attribution, upsert_telegram_attribution
from app.core.navigation import push_screen
from app.core.ui_texts import MY_APPOINTMENTS_BTN
from app.db.booking_links_repo import update_booking_link_status, upsert_booking_link_snapshot
from app.integrations.yclients import (
    YClientsAuthError,
    YClientsBadRequestError,
    YClientsClient,
    YClientsCredentialsError,
    YClientsRateLimitError,
    build_yclients_client,
    notify_yclients_exception,
)
from app.integrations.yclients.endpoints import (
    cancel_booking,
    create_booking_or_visit,
    get_booking_details,
    get_loyalty_info,
    list_client_visits,
    list_user_bookings,
    search_clients,
)
from app.repositories.master_photos import get_master_photo
from app.repositories.users import get_user
from app.services.cancellation_recovery import create_cancellation_event_from_row
from app.services.client_segments import segment_service
from app.services.company_time import format_dt_for_timezone, parse_yclients_datetime, resolve_company_timezone
from app.services.contacts import ResolvedContacts, resolve_contacts_for_company
from app.utils.phone import build_phone_match_keys, normalize_phone
from app.ui.buttons import BACK, HOME

router = Router()
logger = logging.getLogger(__name__)

CB = "my_bookings"
CB_OPEN = f"{CB}:open"
CB_ALL = f"{CB}:all"
CB_CANCEL = f"{CB}:cancel"
CB_CANCEL_CONFIRM = f"{CB}:cancel_confirm"
CB_RESCHEDULE = f"{CB}:reschedule"
CB_REPEAT = f"{CB}:repeat"
CB_REPEAT_CONFIRM = f"{CB}:repeat_confirm"
CB_HISTORY = f"{CB}:history"
CB_HISTORY_REPEAT = f"{CB}:history_repeat"
CB_LOYALTY = f"{CB}:loyalty"
CB_ALL_PAGE = f"{CB}:all_page"
CB_ALL_RESCHEDULE = f"{CB}:all_reschedule"
CB_ALL_CANCEL = f"{CB}:all_cancel"
CB_ALL_REPEAT = f"{CB}:all_repeat"

CACHE_TTL_S = 45
ACTION_LOCK_SECONDS = 4
HISTORY_PAGE_SIZE = 5
HISTORY_FETCH_COUNT = 50
DEV_DIAGNOSTICS_TG_ID = 378881880

_CACHE: dict[str, tuple[float, Any]] = {}


@dataclass(frozen=True)
class BookingCard:
    record_id: str
    datetime_value: datetime | None
    service_id: str | None
    service_name: str
    staff_id: str | None
    master_name: str | None
    address: str | None
    phone: str | None
    duration_minutes: int | None
    price: str | None
    status: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class MyBookingsClientContext:
    tg_user_id: int
    user: dict[str, Any] | None
    yclients_client_id: str | None
    phone: str | None
    state_has_context: bool
    resolver_path: str

    @property
    def user_exists(self) -> bool:
        return self.user is not None

    @property
    def is_resolved(self) -> bool:
        return bool(self.yclients_client_id or self.phone)


STATUS_LABELS = {
    "active": "Подтверждена",
    "confirmed": "Подтверждена",
    "approve": "Подтверждена",
    "approved": "Подтверждена",
    "pending": "Ожидает подтверждения",
    "new": "Новая",
    "cancelled": "Отменена",
    "canceled": "Отменена",
    "done": "Завершена",
    "completed": "Завершена",
    "visit": "Завершена",
    "no_show": "Неявка",
}


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at, payload = item
    if time.monotonic() >= expires_at:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: Any, ttl_s: int = CACHE_TTL_S) -> None:
    _CACHE[key] = (time.monotonic() + ttl_s, payload)


def _clear_cache(user_id: int) -> None:
    for k in list(_CACHE.keys()):
        if k.startswith(f"my_bookings:{user_id}:"):
            _CACHE.pop(k, None)

def invalidate_user_my_bookings_cache(user_id: int) -> None:
    _clear_cache(user_id)


async def _build_client() -> tuple[YClientsClient, str]:
    return await build_yclients_client()


def _extract_list(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "items", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _extract_first_service(item: dict[str, Any]) -> tuple[str | None, str]:
    services = item.get("services")
    if isinstance(services, list) and services:
        first = services[0]
        if isinstance(first, dict):
            sid = _s(first.get("id") or first.get("service_id")) or None
            sname = _s(first.get("title") or first.get("name"))
            if sname:
                return sid, sname
    return _s(item.get("service_id")) or None, (_s(item.get("service_name") or item.get("service") or item.get("title")) or "Услуга")


def _extract_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _s(value.get("name") or value.get("title") or value.get("fullname")) or None
    if isinstance(value, list):
        for item in value:
            name = _extract_name(item)
            if name:
                return name
        return None
    return _s(value) or None


def _to_int(value: Any) -> int | None:
    raw = _s(value)
    if not raw:
        return None
    try:
        val = int(float(raw))
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None


def _normalize_duration_minutes(value: Any) -> int | None:
    duration = _to_int(value)
    if not duration:
        return None
    if duration >= 300 and duration % 60 == 0:
        return duration // 60
    return duration


def _format_price(value: Any) -> str | None:
    raw = _s(value)
    if not raw:
        return None
    cleaned = raw.replace("₽", "").replace(" ", "").replace(",", ".")
    try:
        num = float(cleaned)
    except ValueError:
        return raw if "₽" in raw else f"{raw} ₽"
    if num.is_integer():
        return f"{int(num)} ₽"
    return f"{num:.2f} ₽".replace(".", ",")


def _extract_price_from_services(item: dict[str, Any]) -> str | None:
    services = item.get("services")
    if not isinstance(services, list):
        return None
    collected: list[float] = []
    first_text: str | None = None
    for service in services:
        if not isinstance(service, dict):
            continue
        for key in ("discount_price", "price", "cost", "price_min", "value"):
            formatted = _format_price(service.get(key))
            if formatted and not first_text:
                first_text = formatted
            raw = _s(service.get(key)).replace(",", ".")
            try:
                if raw:
                    collected.append(float(raw))
                    break
            except ValueError:
                continue
    if collected:
        return _format_price(sum(collected))
    return first_text


def _extract_price(item: dict[str, Any]) -> str | None:
    direct_keys = ("final_price", "total_price", "amount", "sum", "price", "cost", "price_min")
    for key in direct_keys:
        formatted = _format_price(item.get(key))
        if formatted:
            return formatted
    nested = _extract_price_from_services(item)
    if nested:
        return nested
    for key in ("service", "appointment"):
        block = item.get(key)
        if isinstance(block, dict):
            for nested_key in direct_keys:
                formatted = _format_price(block.get(nested_key))
                if formatted:
                    return formatted
    return None


def _format_status(value: Any) -> str | None:
    raw = _s(value)
    if not raw:
        return None
    normalized = STATUS_LABELS.get(raw.lower(), raw)
    cleaned = _s(normalized)
    if not cleaned or cleaned == "—":
        return None
    return cleaned


def _to_card(item: dict[str, Any], *, contacts: ResolvedContacts) -> BookingCard | None:
    rid = _s(item.get("record_id") or item.get("id") or item.get("booking_id") or item.get("visit_id"))
    if not rid:
        return None
    service_id, service_name = _extract_first_service(item)
    return BookingCard(
        record_id=rid,
        datetime_value=parse_yclients_datetime(_s(item.get("datetime") or item.get("date") or item.get("start"))),
        service_id=service_id,
        service_name=service_name,
        staff_id=_s(item.get("staff_id") or item.get("master_id") or item.get("employee_id")) or None,
        master_name=_extract_name(item.get("staff_name") or item.get("master_name") or item.get("staff")),
        address=contacts.address,
        phone=contacts.phone,
        duration_minutes=_normalize_duration_minutes(item.get("duration") or item.get("seance_length") or item.get("length")),
        price=_extract_price(item),
        status=_format_status(item.get("status") or item.get("record_status") or item.get("state")),
        raw=item,
    )


def _is_upcoming(card: BookingCard) -> bool:
    if card.datetime_value is None:
        return False
    raw_status = _s(card.raw.get("status") or card.raw.get("record_status") or card.raw.get("state")).lower()
    if raw_status in {"cancelled", "canceled", "done", "completed", "visit"}:
        return False
    return card.datetime_value >= datetime.now(timezone.utc) - timedelta(minutes=5)


def _format_dt(dt: datetime | None, timezone_name: str) -> str:
    return format_dt_for_timezone(dt, timezone_name)


def _card_text(card: BookingCard, *, timezone_name: str, title: str = "📅 Моя ближайшая запись") -> str:
    lines = [
        title,
        "",
        f"✂️ Услуга: {card.service_name}",
        f"👤 Мастер: {card.master_name or 'Любой мастер'}",
        f"📅 Дата: {_format_dt(card.datetime_value, timezone_name).split(' ')[0] if card.datetime_value else '—'}",
        f"🕒 Время: {_format_dt(card.datetime_value, timezone_name).split(' ')[1] if card.datetime_value else '—'}",
        f"⏳ Длительность: {card.duration_minutes} мин" if card.duration_minutes else "⏳ Длительность: —",
        f"💰 Цена: {card.price or '—'}",
        f"📍 Адрес: {card.address or '—'}",
        f"📞 Контакты: {card.phone or '—'}",
    ]
    if card.status:
        lines.append(f"🧾 Статус: {card.status}")
    return "\n".join(lines)


async def _get_master_photo_file_id(staff_id: str | None) -> str | None:
    if not staff_id:
        return None
    try:
        company_id = await _company_id()
        row = await get_master_photo(company_id, staff_id)
        return _s((row or {}).get("telegram_file_id")) or None
    except Exception:
        return None


async def _send_booking_card(chat: Message, card: BookingCard, *, timezone_name: str, reply_markup: InlineKeyboardMarkup, title: str) -> None:
    text = _card_text(card, timezone_name=timezone_name, title=title)
    photo = await _get_master_photo_file_id(card.staff_id)
    if photo:
        await chat.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
    else:
        await chat.answer(text, reply_markup=reply_markup)


def _main_actions_kb(*, has_active: bool, show_all: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_active:
        rows.extend(
            [
                [InlineKeyboardButton(text="🔁 Перенести запись", callback_data=CB_RESCHEDULE)],
                [InlineKeyboardButton(text="❌ Отменить запись", callback_data=CB_CANCEL)],
                [InlineKeyboardButton(text="🔂 Повторить запись", callback_data=CB_REPEAT)],
            ]
        )
        if show_all:
            rows.append([InlineKeyboardButton(text="📋 Показать все активные записи", callback_data=CB_ALL)])
    else:
        rows.append([InlineKeyboardButton(text="🔂 Повторить запись", callback_data=CB_REPEAT)])

    rows.append([InlineKeyboardButton(text="🕘 История визитов", callback_data=f"{CB_HISTORY}:1")])
    if get_settings().loyalty_enabled:
        rows.append([InlineKeyboardButton(text="🎁 Лояльность", callback_data=CB_LOYALTY)])
    rows.extend(
        [
            [InlineKeyboardButton(text=BACK, callback_data="nav:home")],
            [InlineKeyboardButton(text=HOME, callback_data="nav:home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отменить", callback_data=CB_CANCEL_CONFIRM)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_OPEN)],
            [InlineKeyboardButton(text=HOME, callback_data="nav:home")],
        ]
    )


def _history_kb(page: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_HISTORY}:{page - 1}"))
    if has_next:
        pager.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_HISTORY}:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.extend(
        [
            [InlineKeyboardButton(text="🔂 Повторить запись", callback_data=CB_HISTORY_REPEAT)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_OPEN)],
            [InlineKeyboardButton(text=HOME, callback_data="nav:home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_yc_error(target: Message | CallbackQuery, exc: Exception, *, action: str) -> None:
    trace_id = getattr(exc, "trace_id", "n/a")
    endpoint = getattr(exc, "endpoint", "n/a")
    status = getattr(exc, "status_code", "n/a")
    snippet = (getattr(exc, "response_snippet", None) or str(exc))[:250]
    tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:])[:500]
    logger.warning("my_bookings_error action=%s trace_id=%s endpoint=%s status=%s snippet=%s", action, trace_id, endpoint, status, snippet)
    user = target.from_user
    user_id = user.id
    username = user.username or "n/a"
    callback_data = target.data if isinstance(target, CallbackQuery) else "n/a"
    try:
        await target.bot.send_message(
            DEV_DIAGNOSTICS_TG_ID,
            "\n".join(
                [
                    "🚨 YClients my_bookings action failed",
                    f"🧩 action: {action}",
                    "🧩 handler: app.handlers.my_bookings._send_yc_error",
                    f"👤 user_id: {user_id}",
                    f"🔖 username: @{username}" if username != "n/a" else "🔖 username: n/a",
                    f"📨 callback_data: {callback_data[:120] if callback_data else 'n/a'}",
                    f"➡️ endpoint: {endpoint}",
                    "🛠 method: n/a",
                    "🧱 payload_keys: n/a",
                    f"📡 status: {status}",
                    f"🧯 exception: {type(exc).__name__}: {str(exc)[:180]}",
                    f"🪵 traceback_last_lines:\n{tb_tail or 'n/a'}",
                ]
            )[:1800],
        )
    except Exception:
        logger.exception("my_bookings_dev_diagnostics_send_failed action=%s", action)

    if isinstance(exc, YClientsCredentialsError):
        text = "⚙️ Интеграция YClients пока не настроена."
    elif isinstance(exc, YClientsAuthError):
        text = "❌ Нет доступа к YClients."
    elif isinstance(exc, YClientsRateLimitError):
        text = "⏳ Слишком много запросов. Попробуйте через минуту 🙂"
    elif isinstance(exc, YClientsBadRequestError):
        text = "⚠️ Не удалось выполнить действие в YClients."
    else:
        text = "⚠️ Техническая ошибка. Попробуйте ещё раз через минуту."
        await notify_yclients_exception(target.bot if isinstance(target, CallbackQuery) else target.bot, exc=exc, action=action)

    msg_target = target.message if isinstance(target, CallbackQuery) else target
    if msg_target:
        await msg_target.answer(text)


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:10]


def _history_sort_key(card: BookingCard) -> float:
    dt = card.datetime_value
    if not isinstance(dt, datetime):
        return float("-inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


async def _log_resolution_diag(*, action: str, trace_id: str, ctx: MyBookingsClientContext, result: str) -> None:
    logger.info(
        "my_bookings_client_resolution trace_id=%s action=%s tg_user_id=%s user_exists=%s yclients_client_id=%s phone=%s state_has_context=%s resolver_path=%s result=%s",
        trace_id,
        action,
        ctx.tg_user_id,
        ctx.user_exists,
        bool(ctx.yclients_client_id),
        bool(ctx.phone),
        ctx.state_has_context,
        ctx.resolver_path,
        result,
    )


async def _resolve_my_bookings_client_context(state: FSMContext | None, user_id: int) -> MyBookingsClientContext:
    data = await state.get_data() if state else {}
    state_client_id = _s(data.get("my_bookings_client_id")) or None
    state_phone = _s(data.get("my_bookings_phone")) or None
    state_has_context = bool(data.get("my_bookings_context_resolved") and (state_client_id or state_phone))

    user = await get_user(user_id)
    db_client_id = _s((user or {}).get("yclients_client_id")) or None
    db_phone = _s((user or {}).get("phone")) or None

    if db_client_id:
        return MyBookingsClientContext(
            tg_user_id=user_id,
            user=user,
            yclients_client_id=db_client_id,
            phone=db_phone or state_phone,
            state_has_context=state_has_context,
            resolver_path="db_client_id",
        )

    if db_phone:
        return MyBookingsClientContext(
            tg_user_id=user_id,
            user=user,
            yclients_client_id=state_client_id,
            phone=db_phone,
            state_has_context=state_has_context,
            resolver_path="db_phone",
        )

    if state_has_context:
        return MyBookingsClientContext(
            tg_user_id=user_id,
            user=user,
            yclients_client_id=state_client_id,
            phone=state_phone,
            state_has_context=True,
            resolver_path="state_context",
        )

    return MyBookingsClientContext(
        tg_user_id=user_id,
        user=user,
        yclients_client_id=state_client_id,
        phone=state_phone,
        state_has_context=state_has_context,
        resolver_path="unresolved",
    )


async def _search_client_by_phone(client: YClientsClient, company_id: str, phone: str | None) -> str | None:
    if not phone:
        return None
    bundle = normalize_phone(phone, default_region="RU")
    keys = sorted(build_phone_match_keys(bundle))
    if not keys:
        return None

    candidates: dict[str, dict[str, Any]] = {}
    for key in keys:
        payload = await search_clients(client, company_id=company_id, query=key, by_phone=True, page=1, count=50)
        for row in _extract_list(payload):
            client_id = _s(row.get("id") or row.get("client_id"))
            if client_id:
                candidates[client_id] = row

    matches: list[str] = []
    expected = set(keys)
    for client_id, row in candidates.items():
        phones: list[str] = []
        for k in ("phone", "tel"):
            value = row.get(k)
            if isinstance(value, str):
                phones.append(value)
        raw_phones = row.get("phones")
        if isinstance(raw_phones, list):
            for part in raw_phones:
                if isinstance(part, str):
                    phones.append(part)
                elif isinstance(part, dict):
                    phone_value = part.get("phone") or part.get("number")
                    if phone_value:
                        phones.append(str(phone_value))
        row_keys: set[str] = set()
        for raw_phone in phones:
            row_keys.update(build_phone_match_keys(normalize_phone(raw_phone, default_region="RU")))
        if row_keys & expected:
            matches.append(client_id)

    if len(matches) == 1:
        return matches[0]
    return None


async def _ensure_client_context(
    *,
    state: FSMContext | None,
    user_id: int,
    action: str,
    company_id: str | None = None,
    client: YClientsClient | None = None,
) -> tuple[MyBookingsClientContext, str]:
    trace_id = _new_trace_id()
    ctx = await _resolve_my_bookings_client_context(state, user_id)

    if ctx.yclients_client_id:
        await _log_resolution_diag(action=action, trace_id=trace_id, ctx=ctx, result="resolved_with_client_id")
        if state:
            await state.update_data(
                my_bookings_context_resolved=True,
                my_bookings_client_id=ctx.yclients_client_id,
                my_bookings_phone=ctx.phone,
            )
        return ctx, trace_id

    if ctx.phone and client and company_id:
        searched_client_id = await _search_client_by_phone(client, company_id, ctx.phone)
        if searched_client_id:
            resolved = MyBookingsClientContext(
                tg_user_id=user_id,
                user=ctx.user,
                yclients_client_id=searched_client_id,
                phone=ctx.phone,
                state_has_context=ctx.state_has_context,
                resolver_path=f"{ctx.resolver_path}->search_by_phone",
            )
            await _log_resolution_diag(action=action, trace_id=trace_id, ctx=resolved, result="resolved_by_phone_search")
            if state:
                await state.update_data(
                    my_bookings_context_resolved=True,
                    my_bookings_client_id=resolved.yclients_client_id,
                    my_bookings_phone=resolved.phone,
                )
            return resolved, trace_id

    await _log_resolution_diag(action=action, trace_id=trace_id, ctx=ctx, result="unresolved")
    return ctx, trace_id


async def _load_user_bookings(user_id: int, state: FSMContext | None = None) -> tuple[list[BookingCard], str | None, str]:
    logger.info("my_bookings_query_started tg_id=%s", user_id)
    key = f"my_bookings:{user_id}:bookings"
    cached = _cache_get(key)
    if cached is not None:
        cards, client_id, timezone_name = cached
        logger.info(
            "my_bookings_query_finished tg_id=%s source=cache records_total=%s yclients_client_id=%s branch_timezone=%s",
            user_id,
            len(cards),
            client_id or "n/a",
            timezone_name,
        )
        return cached

    client, company_id = await _build_client()
    contacts_ctx = await resolve_contacts_for_company(company_id)
    tz_ctx = await resolve_company_timezone(company_id)
    try:
        ctx, _ = await _ensure_client_context(
            state=state,
            user_id=user_id,
            action="my_bookings_load_user_bookings",
            company_id=company_id,
            client=client,
        )
        if not ctx.is_resolved:
            logger.info(
                "my_bookings_query_finished tg_id=%s source=yclients records_total=0 yclients_client_id=%s branch_timezone=%s reason=client_context_unresolved",
                user_id,
                ctx.yclients_client_id or "n/a",
                tz_ctx.timezone_name,
            )
            return [], None, tz_ctx.timezone_name

        payload = await list_user_bookings(
            client,
            company_id=company_id,
            client_id=ctx.yclients_client_id,
            phone=ctx.phone,
            start_date=(datetime.now().date() - timedelta(days=365)).isoformat(),
            end_date=(datetime.now().date() + timedelta(days=365)).isoformat(),
            page=1,
            count=200,
        )
    finally:
        await client.close()

    cards = [card for row in _extract_list(payload) if (card := _to_card(row, contacts=contacts_ctx.resolved))]
    cards = sorted(cards, key=lambda x: x.datetime_value or datetime.max.replace(tzinfo=timezone.utc))
    _cache_set(key, (cards, ctx.yclients_client_id, tz_ctx.timezone_name))
    logger.info(
        "my_bookings_query_finished tg_id=%s source=yclients records_total=%s yclients_client_id=%s branch_timezone=%s",
        user_id,
        len(cards),
        ctx.yclients_client_id or "n/a",
        tz_ctx.timezone_name,
    )
    return cards, ctx.yclients_client_id, tz_ctx.timezone_name


async def _load_active_card(user_id: int, state: FSMContext | None = None) -> tuple[BookingCard | None, list[BookingCard], str | None, str]:
    cards, client_id, timezone_name = await _load_user_bookings(user_id, state=state)
    future: list[BookingCard] = []
    for c in cards:
        if _is_upcoming(c):
            future.append(c)
            continue
        logger.info(
            "my_bookings_filtered_out_reason tg_id=%s yclients_record_id=%s status=%s booking_datetime=%s branch_timezone=%s reason=not_upcoming",
            user_id,
            c.record_id,
            _s(c.raw.get("status") or c.raw.get("record_status") or c.raw.get("state")) or "n/a",
            c.datetime_value.isoformat() if c.datetime_value else "n/a",
            timezone_name,
        )
    return (future[0] if future else None), future, client_id, timezone_name




def _can_show_start_guidance(ctx: MyBookingsClientContext) -> bool:
    return not ctx.user_exists and not ctx.yclients_client_id and not ctx.phone and not ctx.state_has_context

async def _show_entry(target: Message | CallbackQuery, state: FSMContext) -> None:
    await push_screen(state, "my_appointments")
    user_id = target.from_user.id
    chat = target.message if isinstance(target, CallbackQuery) else target
    if not chat:
        return

    entry_ctx, _ = await _ensure_client_context(state=state, user_id=user_id, action="my_bookings_main")
    if not entry_ctx.is_resolved:
        if _can_show_start_guidance(entry_ctx):
            await chat.answer("⚠️ Не удалось найти ваш профиль для записей. Попробуйте начать через /start 🙂")
        else:
            await chat.answer("⚠️ Не удалось загрузить раздел «📅 Мои записи». Попробуйте ещё раз через минуту 🙂")
        return

    try:
        active, future, _, timezone_name = await _load_active_card(user_id, state=state)
    except Exception as exc:
        await _send_yc_error(target, exc, action="my_bookings_entry")
        return

    if active is None:
        await state.update_data(
            my_bookings_context_resolved=True,
            my_bookings_client_id=entry_ctx.yclients_client_id,
            my_bookings_phone=entry_ctx.phone,
            my_bookings_active_record_id=None,
        )
        await chat.answer(
            "📭 У вас пока нет активных записей.",
            reply_markup=_main_actions_kb(has_active=False),
        )
        return

    await state.update_data(
        my_bookings_context_resolved=True,
        my_bookings_client_id=entry_ctx.yclients_client_id,
        my_bookings_phone=entry_ctx.phone,
        my_bookings_active_record_id=active.record_id,
    )
    await _send_booking_card(
        chat,
        active,
        timezone_name=timezone_name,
        reply_markup=_main_actions_kb(has_active=True, show_all=len(future) > 1),
        title="📅 Моя ближайшая запись",
    )


async def _company_id() -> str:
    _, cid = await _build_client()
    return cid


def _all_cards_kb(index: int, total: int, record_id: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pager: list[InlineKeyboardButton] = []
    if index > 0:
        pager.append(InlineKeyboardButton(text="◀️", callback_data=f"{CB_ALL_PAGE}:{index}"))
    pager.append(InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data=f"{CB_ALL_PAGE}:{index + 1}"))
    if index + 1 < total:
        pager.append(InlineKeyboardButton(text="▶️", callback_data=f"{CB_ALL_PAGE}:{index + 2}"))
    rows.append(pager)
    rows.extend(
        [
            [InlineKeyboardButton(text="🔁 Перенести", callback_data=f"{CB_ALL_RESCHEDULE}:{record_id}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"{CB_ALL_CANCEL}:{record_id}")],
            [InlineKeyboardButton(text="🔂 Повторить", callback_data=f"{CB_ALL_REPEAT}:{record_id}")],
            [InlineKeyboardButton(text=BACK, callback_data=CB_OPEN)],
            [InlineKeyboardButton(text=HOME, callback_data="nav:home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_all_page(callback: CallbackQuery, state: FSMContext, page: int) -> None:
    cards, _, timezone_name = await _load_user_bookings(callback.from_user.id, state=state)
    future = [c for c in cards if _is_upcoming(c)]
    if not future:
        await callback.answer("Активных записей нет", show_alert=True)
        return
    index = min(max(page - 1, 0), len(future) - 1)
    card = future[index]
    if callback.message:
        await _send_booking_card(
            callback.message,
            card,
            timezone_name=timezone_name,
            reply_markup=_all_cards_kb(index, len(future), card.record_id),
            title="📋 Активная запись",
        )
    await callback.answer()


@router.message(F.text == MY_APPOINTMENTS_BTN)
async def my_bookings_entry(message: Message, state: FSMContext) -> None:
    await _show_entry(message, state)


@router.callback_query(F.data == "book_flow:my_bookings")
@router.callback_query(F.data == CB_OPEN)
async def my_bookings_open(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_entry(callback, state)
    await callback.answer()


@router.callback_query(F.data == CB_ALL)
async def my_bookings_show_all(callback: CallbackQuery, state: FSMContext) -> None:
    await _render_all_page(callback, state, 1)


@router.callback_query(F.data.startswith(f"{CB_ALL_PAGE}:"))
async def my_bookings_show_all_page(callback: CallbackQuery, state: FSMContext) -> None:
    page_raw = _s(callback.data).split(":")[-1]
    page = int(page_raw) if page_raw.isdigit() and int(page_raw) > 0 else 1
    await _render_all_page(callback, state, page)


async def _get_card_by_record_id(user_id: int, record_id: str, state: FSMContext | None = None) -> BookingCard | None:
    cards, _, _ = await _load_user_bookings(user_id, state=state)
    return next((c for c in cards if c.record_id == record_id), None)


@router.callback_query(F.data.startswith(f"{CB_ALL_RESCHEDULE}:"))
async def my_bookings_all_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    rid = _s(callback.data).split(":")[-1]
    card = await _get_card_by_record_id(callback.from_user.id, rid, state=state)
    if not card:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await state.update_data(my_bookings_active_record_id=rid)
    await _start_prefilled_date_flow(callback, state, card=card, mode="reschedule")
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_ALL_REPEAT}:"))
async def my_bookings_all_repeat(callback: CallbackQuery, state: FSMContext) -> None:
    rid = _s(callback.data).split(":")[-1]
    await state.update_data(my_bookings_repeat_record_id=rid)
    await my_bookings_repeat_preview(callback, state)


@router.callback_query(F.data.startswith(f"{CB_ALL_CANCEL}:"))
async def my_bookings_all_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    rid = _s(callback.data).split(":")[-1]
    await state.update_data(my_bookings_active_record_id=rid)
    await my_bookings_cancel_ask(callback, state)


@router.callback_query(F.data == CB_CANCEL)
async def my_bookings_cancel_ask(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = _s(data.get("my_bookings_active_record_id"))
    if not rid:
        await callback.answer("Активная запись не найдена", show_alert=True)
        return
    if callback.message:
        await callback.message.answer("❗️Вы уверены, что хотите отменить запись?", reply_markup=_cancel_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == CB_CANCEL_CONFIRM)
async def my_bookings_cancel_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = _s(data.get("my_bookings_active_record_id"))
    if not rid:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    user_id = callback.from_user.id
    if not acquire_action_lock("booking_cancel", user_id, rid, ttl_s=ACTION_LOCK_SECONDS):
        await callback.answer("⏳ Уже выполняю действие")
        return

    try:
        card = await _get_card_by_record_id(user_id, rid, state=state)
        client, company_id = await _build_client()
        try:
            result = await cancel_booking(client, company_id=company_id, record_id=rid)
        finally:
            await client.close()

        await update_booking_link_status(tg_user_id=user_id, yclients_record_id=rid, status="cancelled", raw_payload=result.raw_payload)
        await upsert_booking_link_snapshot(
            tg_user_id=user_id,
            yclients_record_id=rid,
            company_id=company_id,
            service_id=None,
            staff_id=None,
            datetime_iso=datetime.now(timezone.utc).isoformat(),
            status="cancelled",
            raw_payload=result.raw_payload,
        )
        if card:
            try:
                logger.info(
                    "cancellation_event_create_started tg_id=%s yclients_client_id=%s record_id=%s cancelled_at_utc=%s source=%s",
                    user_id,
                    _s((card.raw.get("client") or {}).get("id") or card.raw.get("client_id")) or None,
                    rid,
                    datetime.now(timezone.utc).isoformat(),
                    "my_bookings_cancel",
                )
                event_id = await create_cancellation_event_from_row(row=card.raw, source="my_bookings_cancel", force_tg_id=user_id)
                logger.info(
                    "cancellation_event_create_finished tg_id=%s yclients_client_id=%s record_id=%s cancelled_at_utc=%s source=%s event_id=%s",
                    user_id,
                    _s((card.raw.get("client") or {}).get("id") or card.raw.get("client_id")) or None,
                    rid,
                    datetime.now(timezone.utc).isoformat(),
                    "my_bookings_cancel",
                    event_id,
                )
            except Exception:
                logger.exception(
                    "cancellation_event_create_failed tg_id=%s yclients_client_id=%s record_id=%s cancelled_at_utc=%s source=%s error_summary=%s",
                    user_id,
                    _s((card.raw.get("client") or {}).get("id") or card.raw.get("client_id")) or None,
                    rid,
                    datetime.now(timezone.utc).isoformat(),
                    "my_bookings_cancel",
                    "create_cancellation_event_exception",
                )
                logger.exception("my_bookings_cancel_recovery_event_failed record_id=%s tg_user_id=%s", rid, user_id)
        _clear_cache(user_id)
        try:
            await segment_service.ensure_segment_fresh("cancelled_30", force=True)
            logger.info("cancelled_recent_cache_refreshed actor_tg_id=%s segment_key=cancelled_30 source_function=my_bookings_cancel_confirm", user_id)
        except Exception as exc:
            logger.exception("cancelled_recent_cache_refresh_failed actor_tg_id=%s segment_key=cancelled_30 error_summary=%s", user_id, str(exc)[:200])
        if callback.message:
            await callback.message.answer("✅ Запись отменена.")
        await _show_entry(callback, state)
        await callback.answer()
    except YClientsBadRequestError:
        if callback.message:
            await callback.message.answer("⚠️ Эту запись уже нельзя отменить онлайн.")
        await callback.answer()
    except (YClientsAuthError, YClientsCredentialsError, YClientsRateLimitError):
        if callback.message:
            await callback.message.answer("⚠️ Не удалось отменить запись. Попробуйте ещё раз через минуту.")
        await callback.answer()
    except Exception as exc:
        await _send_yc_error(callback, exc, action="my_bookings_cancel")
    finally:
        release_action_lock("booking_cancel", user_id, rid)


def _to_prefill_payload(card: BookingCard) -> dict[str, Any] | None:
    if not card.service_id:
        return None
    return {
        "selected_service_id": card.service_id,
        "selected_service_name": card.service_name,
        "selected_service_price": card.price,
        "selected_service_duration": f"{card.duration_minutes} мин" if card.duration_minutes else None,
        "selected_staff_id": card.staff_id,
        "selected_staff_name": card.master_name or "Любой мастер",
        "book_services": [
            {
                "id": card.service_id,
                "name": card.service_name,
                "category_id": _s(card.raw.get("category_id")) or "0",
                "category_name": _s(card.raw.get("category_name")) or "Из моих записей",
                "price": card.price,
                "duration": f"{card.duration_minutes} мин" if card.duration_minutes else None,
            }
        ],
    }


async def _start_prefilled_date_flow(callback: CallbackQuery, state: FSMContext, *, card: BookingCard, mode: str) -> None:
    from app.handlers.booking_flow import BookingFlowStates, _show_date_picker

    payload = _to_prefill_payload(card)
    if not payload:
        await callback.answer("⚠️ Не удалось определить услугу для записи", show_alert=True)
        return

    client, company_id = await _build_client()
    try:
        details = await get_booking_details(client, company_id=company_id, record_id=card.record_id)
    except Exception as exc:
        logger.exception(
            "reschedule_prefill_get_booking_details_failed user_id=%s record_id=%s yclients_record_id=%s",
            callback.from_user.id,
            card.record_id,
            card.record_id,
        )
        tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-5:])[:700]
        try:
            await callback.bot.send_message(
                DEV_DIAGNOSTICS_TG_ID,
                "\n".join(
                    [
                        "🚨 YClients reschedule prefill failed",
                        "🧩 action: reschedule_prefill_get_booking_details",
                        "🧩 handler: app.handlers.my_bookings._start_prefilled_date_flow",
                        f"👤 user_id: {callback.from_user.id}",
                        f"🔖 username: @{callback.from_user.username}" if callback.from_user.username else "🔖 username: n/a",
                        f"🆔 record_id: {card.record_id}",
                        f"🆔 yclients_record_id: {card.record_id}",
                        f"🧯 exception: {type(exc).__name__}: {str(exc)[:180]}",
                        f"🪵 traceback_last_lines:\n{tb_tail or 'n/a'}",
                    ]
                )[:1800],
            )
        except Exception:
            logger.exception("reschedule_prefill_dev_diagnostics_send_failed record_id=%s", card.record_id)
        await callback.answer("⚠️ Не удалось подготовить перенос записи. Попробуйте позже.", show_alert=True)
        return
    finally:
        await client.close()

    details_row = details.get("data") if isinstance(details, dict) and isinstance(details.get("data"), dict) else details if isinstance(details, dict) else {}
    if not isinstance(details_row, dict) or not details_row:
        details_row = card.raw

    service_ids = _extract_service_ids(details_row, card.service_id)
    client_id = _extract_client_id(details_row)
    seance_length = _extract_seance_length(card)

    payload["my_bookings_mode"] = mode
    payload["my_bookings_origin_record_id"] = card.record_id
    payload["reschedule_old_record_id"] = card.record_id
    client_row = details_row.get("client") if isinstance(details_row.get("client"), dict) else {}
    payload["reschedule_context"] = {
        "record_id": card.record_id,
        "company_id": company_id,
        "service_ids": service_ids,
        "service_name": card.service_name,
        "staff_id": card.staff_id,
        "staff_name": card.master_name,
        "client_id": client_id,
        "client_phone": _s(client_row.get("phone") or details_row.get("phone")),
        "client_name": _s(client_row.get("name") or client_row.get("fullname") or details_row.get("fullname")),
        "seance_length": seance_length,
        "old_datetime": _s(details_row.get("datetime") or details_row.get("date") or details_row.get("start")),
        "datetime": _s(details_row.get("datetime") or details_row.get("date") or details_row.get("start")),
    }
    await state.clear()
    await state.update_data(**payload)
    await state.set_state(BookingFlowStates.CHOOSE_DATE)
    await _show_date_picker(callback, state)


@router.callback_query(F.data == CB_RESCHEDULE)
async def my_bookings_reschedule(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = _s(data.get("my_bookings_active_record_id"))
    if not rid:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    cards, _, _ = await _load_user_bookings(callback.from_user.id, state=state)
    card = next((c for c in cards if c.record_id == rid), None)
    if not card:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await _start_prefilled_date_flow(callback, state, card=card, mode="reschedule")
    await callback.answer()


@router.callback_query(F.data == CB_REPEAT)
@router.callback_query(F.data == CB_HISTORY_REPEAT)
async def my_bookings_repeat_preview(callback: CallbackQuery, state: FSMContext) -> None:
    cards, _, timezone_name = await _load_user_bookings(callback.from_user.id, state=state)
    data = await state.get_data()
    preferred_rid = _s(data.get("my_bookings_repeat_record_id"))
    card = next((c for c in cards if c.record_id == preferred_rid and c.service_id), None)
    if not card:
        card = next((c for c in reversed(cards) if c.service_id), None)
    if not card:
        await callback.answer("Нет записей для повтора", show_alert=True)
        return
    await state.update_data(my_bookings_repeat_record_id=card.record_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Повторить запись", callback_data=CB_REPEAT_CONFIRM)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_OPEN)],
            [InlineKeyboardButton(text=HOME, callback_data="nav:home")],
        ]
    )
    if callback.message:
        await _send_booking_card(callback.message, card, timezone_name=timezone_name, reply_markup=kb, title="🔂 Повторить запись")
    await callback.answer()


@router.callback_query(F.data == CB_REPEAT_CONFIRM)
async def my_bookings_repeat_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = _s(data.get("my_bookings_repeat_record_id"))
    cards, _, _ = await _load_user_bookings(callback.from_user.id, state=state)
    card = next((c for c in cards if c.record_id == rid), None)
    if not card:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    await _start_prefilled_date_flow(callback, state, card=card, mode="repeat")
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB_HISTORY}:"))
async def my_bookings_history(callback: CallbackQuery, state: FSMContext) -> None:
    page = 1

    try:
        client, company_id = await _build_client()
        try:
            ctx, _ = await _ensure_client_context(
                state=state,
                user_id=callback.from_user.id,
                action="my_bookings_history",
                company_id=company_id,
                client=client,
            )
            if not ctx.yclients_client_id and ctx.phone:
                cards, _, _ = await _load_user_bookings(callback.from_user.id, state=state)
                for card in cards:
                    cid = _extract_client_id(card.raw)
                    if cid:
                        ctx = MyBookingsClientContext(
                            tg_user_id=ctx.tg_user_id,
                            user=ctx.user,
                            yclients_client_id=cid,
                            phone=ctx.phone,
                            state_has_context=ctx.state_has_context,
                            resolver_path=f"{ctx.resolver_path}->bookings_payload",
                        )
                        await state.update_data(
                            my_bookings_context_resolved=True,
                            my_bookings_client_id=cid,
                            my_bookings_phone=ctx.phone,
                        )
                        break
            if not ctx.yclients_client_id:
                if callback.message:
                    if _can_show_start_guidance(ctx):
                        await callback.message.answer("⚠️ Не удалось найти ваш профиль для записей. Попробуйте начать через /start 🙂")
                    else:
                        await callback.message.answer("⚠️ Не удалось загрузить историю визитов. Попробуйте открыть «📅 Мои записи» ещё раз.")
                await callback.answer()
                return
            payload = await list_client_visits(client, company_id=company_id, client_id=ctx.yclients_client_id, page=page, count=HISTORY_FETCH_COUNT)
        finally:
            await client.close()
    except Exception as exc:
        await _send_yc_error(callback, exc, action="my_bookings_history")
        return

    contacts_ctx = await resolve_contacts_for_company(company_id)
    tz_ctx = await resolve_company_timezone(company_id)
    cards = [c for row in _extract_list(payload) if (c := _to_card(row, contacts=contacts_ctx.resolved)) and not _is_upcoming(c)]
    cards.sort(key=_history_sort_key, reverse=True)
    cards = cards[:HISTORY_PAGE_SIZE]
    await state.update_data(my_bookings_history_page=page)
    if not cards:
        if callback.message:
            await callback.message.answer("🕘 История визитов пока пуста.", reply_markup=_history_kb(page, has_next=False))
        await callback.answer()
        return

    lines = ["🕘 История визитов", ""]
    for idx, card in enumerate(cards, start=1):
        lines.append(f"{idx}. ✂️ {card.service_name}")
        lines.append(f"   👤 {card.master_name or 'Любой мастер'}")
        lines.append(f"   📅 {_format_dt(card.datetime_value, tz_ctx.timezone_name)}")
        lines.append(f"   💰 {card.price or '—'}")
        if card.status:
            lines.append(f"   🧾 {card.status}")
    if callback.message:
        await callback.message.answer("\n".join(lines), reply_markup=_history_kb(page, has_next=False))
    await callback.answer()


@router.callback_query(F.data == CB_LOYALTY)
async def my_bookings_loyalty(callback: CallbackQuery, state: FSMContext) -> None:
    if not get_settings().loyalty_enabled:
        if callback.message:
            await callback.message.answer(
                "🎁 Раздел лояльности временно недоступен.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=HOME, callback_data="nav:home")]]
                ),
            )
        await callback.answer()
        return
    try:
        client, company_id = await _build_client()
        try:
            ctx, _ = await _ensure_client_context(
                state=state,
                user_id=callback.from_user.id,
                action="my_bookings_loyalty",
                company_id=company_id,
                client=client,
            )
            if not ctx.yclients_client_id and ctx.phone:
                cards, _, _ = await _load_user_bookings(callback.from_user.id, state=state)
                for card in cards:
                    cid = _extract_client_id(card.raw)
                    if cid:
                        ctx = MyBookingsClientContext(
                            tg_user_id=ctx.tg_user_id,
                            user=ctx.user,
                            yclients_client_id=cid,
                            phone=ctx.phone,
                            state_has_context=ctx.state_has_context,
                            resolver_path=f"{ctx.resolver_path}->bookings_payload",
                        )
                        await state.update_data(
                            my_bookings_context_resolved=True,
                            my_bookings_client_id=cid,
                            my_bookings_phone=ctx.phone,
                        )
                        break
            if not ctx.yclients_client_id:
                if callback.message:
                    if _can_show_start_guidance(ctx):
                        await callback.message.answer("⚠️ Не удалось найти ваш профиль для записей. Попробуйте начать через /start 🙂")
                    else:
                        await callback.message.answer("🎁 Данные по лояльности пока недоступны.")
                await callback.answer()
                return
            payload = await get_loyalty_info(client, company_id=company_id, client_id=ctx.yclients_client_id)
        finally:
            await client.close()
    except Exception as exc:
        await _send_yc_error(callback, exc, action="my_bookings_loyalty")
        return

    row = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload if isinstance(payload, dict) else {}
    if not isinstance(row, dict) or not row:
        if callback.message:
            await callback.message.answer("🎁 Данные по лояльности пока недоступны.")
        await callback.answer()
        return

    balance = _s(row.get("balance") or row.get("wallet") or row.get("deposit"))
    points = _s(row.get("points") or row.get("bonus") or row.get("bonuses"))
    level = _s(row.get("tier") or row.get("level") or row.get("category"))
    discount = _s(row.get("discount") or row.get("discount_percent"))
    lines = ["🎁 Лояльность", ""]
    if balance:
        lines.append(f"💳 Баланс: {balance}")
    if points:
        lines.append(f"⭐ Бонусы: {points}")
    if level:
        lines.append(f"🏅 Уровень: {level}")
    if discount:
        lines.append(f"🏷 Скидка: {discount}")
    if len(lines) == 2:
        lines.append("🎁 Данные по лояльности пока недоступны.")

    if callback.message:
        await callback.message.answer("\n".join(lines))
    await callback.answer()


def _extract_service_ids(item: dict[str, Any], fallback_service_id: str | None) -> list[str]:
    ids: list[str] = []
    services = item.get("services")
    if isinstance(services, list):
        for service in services:
            if not isinstance(service, dict):
                continue
            sid = _s(service.get("id") or service.get("service_id"))
            if sid:
                ids.append(sid)
    if not ids and fallback_service_id:
        ids.append(fallback_service_id)
    return ids


def _extract_client_id(item: dict[str, Any]) -> str | None:
    client = item.get("client")
    if isinstance(client, dict):
        cid = _s(client.get("id") or client.get("client_id"))
        if cid:
            return cid
    return _s(item.get("client_id")) or None


def _extract_seance_length(card: BookingCard) -> int | None:
    value = _to_int(card.raw.get("seance_length") or card.raw.get("length") or card.raw.get("duration"))
    if value:
        return value
    if card.duration_minutes:
        return card.duration_minutes * 60
    return None



async def maybe_process_reschedule_final(
    *,
    state: FSMContext,
    user_id: int,
    fullname: str,
    phone: str,
    bot: Any,
) -> dict[str, str] | None:
    data = await state.get_data()
    if _s(data.get("my_bookings_mode")) != "reschedule":
        return None
    return await reschedule_via_rebook(state=state, user_id=user_id, fullname=fullname, phone=phone, bot=bot)


async def reschedule_via_rebook(
    *,
    state: FSMContext,
    user_id: int,
    fullname: str,
    phone: str,
    bot: Any,
) -> dict[str, str]:
    data = await state.get_data()
    trace_id = f"resch-{int(time.time())}-{user_id}"
    ctx = data.get("reschedule_context") if isinstance(data.get("reschedule_context"), dict) else {}

    record_id = _s(ctx.get("record_id") or data.get("reschedule_old_record_id") or data.get("my_bookings_origin_record_id"))
    company_id = _s(ctx.get("company_id") or data.get("yclients_company_id"))
    service_ids = [sid for sid in (ctx.get("service_ids") or []) if _s(sid)] if isinstance(ctx.get("service_ids"), list) else []
    staff_id = _s(ctx.get("staff_id") or data.get("selected_staff_id"))
    client_id = _s(ctx.get("client_id"))
    seance_length = _to_int(ctx.get("seance_length"))
    datetime_iso = _s(data.get("reschedule_new_datetime") or data.get("selected_datetime"))

    missing: list[str] = []
    if not company_id:
        missing.append("company_id")
    if not record_id:
        missing.append("record_id")
    if not staff_id:
        missing.append("staff_id")
    if not service_ids:
        missing.append("services")
    if not client_id:
        missing.append("client_id")
    if not seance_length:
        missing.append("seance_length")
    if not datetime_iso:
        missing.append("datetime")

    if missing:
        keys = sorted(data.keys())
        logger.error(
            "reschedule_context_missing trace_id=%s record_id=%s missing=%s keys=%s",
            trace_id,
            record_id or "n/a",
            ",".join(missing),
            keys,
        )
        raise RuntimeError(f"RESCHEDULE_CONTEXT_MISSING:{','.join(missing)}")

    client_phone = _s(ctx.get("client_phone") or data.get("registered_phone") or phone)
    client_name = _s(ctx.get("client_name") or fullname) or "Гость"
    if not client_phone:
        raise RuntimeError("RESCHEDULE_CREATE_PHONE_MISSING:client_phone")

    await state.update_data(
        reschedule_request_debug={
            "trace_id": trace_id,
            "step": "create_new",
            "method": "POST",
            "endpoint": f"/api/v1/book_record/{company_id}",
            "company_id": company_id,
            "old_record_id": record_id,
            "staff_id": staff_id,
            "services": service_ids,
            "client_id": client_id,
            "seance_length": seance_length,
            "datetime": datetime_iso,
        }
    )

    client, _ = await _build_client()
    cancel_error: Exception | None = None
    cancel_result: Any = None
    try:
        created = await create_booking_or_visit(
            client,
            company_id=company_id,
            service_id=service_ids[0],
            staff_id=staff_id,
            datetime_iso=datetime_iso,
            phone=client_phone,
            fullname=client_name,
        )

        await state.update_data(
            reschedule_request_debug={
                "trace_id": trace_id,
                "step": "cancel_old",
                "method": "DELETE",
                "endpoint": f"/api/v1/record/{company_id}/{record_id}",
                "company_id": company_id,
                "old_record_id": record_id,
                "new_record_id": created.record_id,
                "staff_id": staff_id,
                "services": service_ids,
                "client_id": client_id,
                "seance_length": seance_length,
                "datetime": datetime_iso,
            }
        )
        try:
            cancel_result = await cancel_booking(client, company_id=company_id, record_id=record_id)
        except Exception as exc:  # noqa: BLE001
            cancel_error = exc
    finally:
        await client.close()

    await upsert_booking_link_snapshot(
        tg_user_id=user_id,
        yclients_record_id=created.record_id,
        company_id=company_id,
        service_id=service_ids[0],
        staff_id=staff_id,
        datetime_iso=created.datetime or datetime_iso,
        status="created",
        raw_payload=created.raw_payload,
    )
    await upsert_telegram_attribution(
        company_id=company_id,
        record_id=created.record_id,
        client_id=client_id,
        created_via="reschedule",
        original_record_id=record_id,
    )

    warning = ""
    if cancel_error is None and cancel_result is not None:
        await update_booking_link_status(
            tg_user_id=user_id,
            yclients_record_id=record_id,
            status="cancelled",
            raw_payload=cancel_result.raw_payload,
        )
        await deactivate_telegram_attribution(record_id=record_id)
    else:
        warning = "cancel_old_failed"
        status = getattr(cancel_error, "status_code", None) if cancel_error else None
        endpoint = getattr(cancel_error, "endpoint", None) if cancel_error else None
        snippet = (_s(getattr(cancel_error, "response_snippet", None) or cancel_error) if cancel_error else "n/a")[:350]
        try:
            await bot.send_message(
                DEV_DIAGNOSTICS_TG_ID,
                "\n".join(
                    [
                        "🚨 YClients reschedule cancel-old failed",
                        f"🧩 trace_id: {trace_id}",
                        "🪜 step_failed: cancel_old",
                        f"🏢 company_id: {company_id}",
                        f"🆔 old_record_id: {record_id}",
                        f"🆕 new_record_id: {created.record_id}",
                        f"👤 staff_id: {staff_id}",
                        f"✂️ services: {','.join(service_ids)}",
                        f"🙍 client_id: {client_id}",
                        f"🕒 datetime: {datetime_iso}",
                        f"➡️ endpoint: {endpoint or f'/api/v1/record/{company_id}/{record_id}'}",
                        f"📡 status: {status or 'n/a'}",
                        f"📄 response: {snippet}",
                    ]
                ),
            )
        except Exception:
            logger.exception("Failed to send reschedule cancel-old diagnostics")

    _clear_cache(user_id)
    result = {
        "datetime_iso": created.datetime or datetime_iso,
        "booking_id": created.record_id,
        "company_id": company_id,
        "old_record_id": record_id,
    }
    if warning:
        result["warning"] = warning
    return result


async def my_bookings_from_flow(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_entry(callback, state)
