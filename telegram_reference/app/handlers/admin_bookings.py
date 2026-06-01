from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.action_locks import acquire_action_lock
from app.core.navigation import push_screen
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, require_roles
from app.core.ui_texts import ADMIN_APPOINTMENTS_BTN
from app.integrations.yclients import (
    YClientsAuthError,
    YClientsClient,
    YClientsBadRequestError,
    YClientsCredentialsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsUnavailableError,
    build_yclients_client,
)
from app.integrations.yclients.endpoints import (
    ADMIN_BOOKING_CAPABILITIES,
    LOYALTY_CAPABILITIES,
    admin_cancel_booking,
    apply_loyalty_to_visit,
    get_booking_details_admin,
    get_loyalty_info,
    list_bookings_by_date_range,
    list_staff,
)
from app.core.branding import admin_client_contact_template
from app.repositories.loyalty_actions import create_loyalty_action
from app.repositories.staff_action_logs import log_staff_action
from app.repositories.users import get_user_by_tg_id
from app.utils.staff import display_name, role_label

router = Router()
logger = logging.getLogger(__name__)

CB = "admin_bookings"
PAGE_SIZE = 10
CACHE_TTL_S = 20


def _role_text(role: str | None) -> str:
    return role_label(role).split(" ", 1)[-1]


async def _log_booking_admin_action(actor_tg_id: int, action_type: str, human_tail: str, **metadata: Any) -> None:
    actor = await get_user_by_tg_id(actor_tg_id)
    actor_name = display_name(actor or {})
    actor_role = (actor or {}).get("role") or ("developer" if actor_tg_id == 378881880 else None)
    await log_staff_action(
        actor_tg_id=actor_tg_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action_type=action_type,
        human_text=f"{_role_text(actor_role)} {actor_name} {human_tail}.",
        metadata={k: v for k, v in metadata.items() if v not in (None, "")},
    )


@dataclass
class DashboardState:
    day: str = "today"
    master_id: str | None = None
    status: str | None = None
    page: int = 0


_STATE: dict[int, DashboardState] = {}
_CACHE: dict[str, tuple[float, Any]] = {}


class LoyaltyStates(StatesGroup):
    LOYALTY_CHOOSE_ACTION = State()
    LOYALTY_INPUT_VALUE = State()
    LOYALTY_INPUT_COMMENT = State()
    LOYALTY_CONFIRM = State()


def _cache_get(key: str) -> Any | None:
    row = _CACHE.get(key)
    if not row:
        return None
    expire_at, value = row
    if time.monotonic() >= expire_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int = CACHE_TTL_S) -> None:
    _CACHE[key] = (time.monotonic() + ttl, value)


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "result", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _extract_one(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        value = payload.get("data")
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mask_phone(phone: str | None) -> str:
    raw = _s(phone)
    if len(raw) < 6:
        return raw or "—"
    return f"{raw[:2]}***{raw[-3:]}"


def _pick(item: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        v = _s(item.get(key))
        if v:
            return v
    return default


def _service_label(item: dict[str, Any]) -> str:
    if isinstance(item.get("services"), list) and item["services"]:
        first = item["services"][0]
        if isinstance(first, dict):
            return _s(first.get("title") or first.get("name")) or "Услуга"
    return _pick(item, ("service_name", "service", "title"), "Услуга")


def _record_id(item: dict[str, Any]) -> str:
    return _pick(item, ("id", "record_id", "booking_id", "visit_id"), "")


def _datetime_label(item: dict[str, Any]) -> str:
    raw = _pick(item, ("datetime", "date", "start_time", "start"))
    if not raw:
        return "—"
    value = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return raw
    return dt.strftime("%d.%m %H:%M")


def format_admin_booking_list_item(item: dict[str, Any]) -> str:
    time_label = _datetime_label(item)[-5:]
    master = _pick(item, ("staff_name", "master_name", "staff"), "Мастер")
    service = _service_label(item)
    phone = _mask_phone(_pick(item, ("phone", "client_phone", "tel"), ""))
    return f"🕒 {time_label} • 👤 {master[:12]} • ✂️ {service[:16]} • 📞 {phone}"


def format_admin_booking_card(item: dict[str, Any]) -> str:
    record_id = _record_id(item) or "—"
    status = _pick(item, ("status", "record_status", "state"), "—")
    comment = _pick(item, ("comment", "notes"), "—")
    client_name = _pick(item, ("client_name", "fullname", "name"), "—")
    lines = [
        "📋 Карточка записи",
        "",
        f"🧾 ID записи: {record_id}",
        f"🕒 Дата и время: {_datetime_label(item)}",
        f"👤 Мастер: {_pick(item, ('staff_name', 'master_name', 'staff'), '—')}",
        f"✂️ Услуга: {_service_label(item)}",
        f"👤 Клиент: {client_name}",
        f"📞 Телефон: {_mask_phone(_pick(item, ('phone', 'client_phone', 'tel'), ''))}",
        f"🧾 Статус: {status}",
    ]
    if comment != "—":
        lines.append(f"📝 Комментарий: {comment}")
    lines.extend(["", "🛠️ Действия: выберите кнопку ниже 👇"])
    return "\n".join(lines)


def _format_cancel_summary(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            "❗️Отменить запись клиента? 😔",
            "",
            f"🕒 {_datetime_label(item)}",
            f"👤 Клиент: {_pick(item, ('client_name', 'fullname', 'name'), '—')}",
            f"✂️ Услуга: {_service_label(item)}",
            f"💈 Мастер: {_pick(item, ('staff_name', 'master_name', 'staff'), '—')}",
        ]
    )


def _loyalty_identifiers(item: dict[str, Any]) -> tuple[str, str | None]:
    record_id = _record_id(item)
    client_id = _pick(item, ("client_id", "client", "person_id", "customer_id"))
    return record_id, client_id or None


def _loyalty_feature_available(item: dict[str, Any]) -> bool:
    record_id, client_id = _loyalty_identifiers(item)
    return bool(LOYALTY_CAPABILITIES.get("enabled") and record_id and client_id)


def _value_label(action: str) -> str:
    if action == "accrue":
        return "баллов"
    if action == "redeem":
        return "баллов"
    return "₽"


def _format_loyalty_snapshot(payload: dict[str, Any] | list[Any]) -> tuple[str, str | None, str | None, str | None]:
    item = _extract_one(payload) or {}
    points = _pick(item, ("points", "balance", "bonus", "bonus_balance"), "—")
    tier = _pick(item, ("level", "tier", "status", "grade"), "—")
    discount = _pick(item, ("available_discount", "discount", "max_discount"), "—")
    return points, tier, discount, _pick(item, ("message", "comment")) or None


def _loyalty_screen_text(item: dict[str, Any], points: str = "—", tier: str = "—", discount: str = "—", note: str | None = None) -> str:
    client_name = _pick(item, ("client_name", "fullname", "name"), "—")
    masked_phone = _mask_phone(_pick(item, ("phone", "client_phone", "tel"), ""))
    lines = [
        "🎁 Лояльность клиента",
        "",
        f"👤 Клиент: {client_name}",
        f"📞 Телефон: {masked_phone}",
        f"⭐ Баллы/баланс: {points}",
        f"🏅 Уровень: {tier}",
        f"🏷️ Доступная скидка: {discount}",
    ]
    if note:
        lines.append(f"ℹ️ {note}")
    lines.extend(["", "Выберите действие 👇"])
    return "\n".join(lines)


def _loyalty_actions_keyboard(record_id: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if LOYALTY_CAPABILITIES.get("accrue"):
        rows.append([InlineKeyboardButton(text="➕ Начислить", callback_data=f"{CB}:loyalty_action:{record_id}:accrue")])
    if LOYALTY_CAPABILITIES.get("redeem"):
        rows.append([InlineKeyboardButton(text="➖ Списать", callback_data=f"{CB}:loyalty_action:{record_id}:redeem")])
    if LOYALTY_CAPABILITIES.get("discount"):
        rows.append([InlineKeyboardButton(text="🏷️ Применить скидку", callback_data=f"{CB}:loyalty_action:{record_id}:discount")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB}:loyalty:{record_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:open:{record_id}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _contact_message(item: dict[str, Any]) -> str:
    phone = _pick(item, ("phone", "client_phone", "tel"), "")
    masked = _mask_phone(phone)
    client_name = _pick(item, ("client_name", "fullname", "name"), "клиент")
    template = admin_client_contact_template(client_name)
    return f"📞 Связь с клиентом\n\nТелефон: {masked}\n\n📋 Шаблон сообщения:\n{template}"


async def _show_booking_card(callback: CallbackQuery, item: dict[str, Any]) -> None:
    record_id = _record_id(item)
    kb_rows: list[list[InlineKeyboardButton]] = []
    if ADMIN_BOOKING_CAPABILITIES.can_cancel and record_id:
        kb_rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"{CB}:action_cancel:{record_id}")])
    if ADMIN_BOOKING_CAPABILITIES.can_comment and record_id:
        kb_rows.append([InlineKeyboardButton(text="📝 Комментарий", callback_data=f"{CB}:action_comment:{record_id}")])
    if ADMIN_BOOKING_CAPABILITIES.can_reschedule and record_id:
        kb_rows.append([InlineKeyboardButton(text="🔁 Перенести", callback_data=f"{CB}:action_reschedule:{record_id}")])
    kb_rows.append([InlineKeyboardButton(text="📞 Связаться", callback_data=f"{CB}:action_contact:{record_id}")])
    if _loyalty_feature_available(item):
        kb_rows.append([InlineKeyboardButton(text="🎁 Лояльность", callback_data=f"{CB}:loyalty:{record_id}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back_dashboard")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    await callback.message.edit_text(format_admin_booking_card(item), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


async def _build_client() -> tuple[YClientsClient, str]:
    return await build_yclients_client()


def _state(user_id: int) -> DashboardState:
    if user_id not in _STATE:
        _STATE[user_id] = DashboardState()
    return _STATE[user_id]


def _date_range(day: str) -> tuple[str, str, str]:
    base: date = date.today() + timedelta(days=1 if day == "tomorrow" else 0)
    iso = base.isoformat()
    title = "Завтра" if day == "tomorrow" else "Сегодня"
    return iso, iso, title


def _status_bucket(raw: str) -> str:
    value = raw.lower()
    if "cancel" in value or "отмен" in value:
        return "cancelled"
    if "wait" in value or "нов" in value or "pending" in value:
        return "pending"
    return "confirmed"


async def _load_staff_options(client: YClientsClient, company_id: str) -> list[tuple[str, str]]:
    cache_key = f"staff:{company_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await list_staff(client, company_id=company_id)
    options: list[tuple[str, str]] = []
    for item in _extract_rows(payload):
        sid = _pick(item, ("id",))
        name = _pick(item, ("name", "fullname", "title"))
        if sid and name:
            options.append((sid, name))
    _cache_set(cache_key, options, ttl=120)
    return options


async def _render_dashboard(target: Message | CallbackQuery, user_id: int, *, force_refresh: bool = False) -> None:
    st = _state(user_id)
    date_from, date_to, day_title = _date_range(st.day)
    cache_key = f"bookings:{user_id}:{date_from}:{st.master_id or 'all'}:{st.status or 'all'}"

    try:
        if force_refresh:
            _CACHE.pop(cache_key, None)
        rows = _cache_get(cache_key)
        client: YClientsClient | None = None
        company_id = ""
        if rows is None:
            client, company_id = await _build_client()
            payload = await list_bookings_by_date_range(
                client,
                company_id=company_id,
                date_from=date_from,
                date_to=date_to,
                staff_id=st.master_id,
                status=st.status,
                page=1,
                count=200,
            )
            rows = _extract_rows(payload)
            _cache_set(cache_key, rows)
        if st.status:
            rows = [x for x in rows if _s(x.get("status") or x.get("record_status") or x.get("state")) == st.status]
        statuses = sorted({_s(x.get("status") or x.get("record_status") or x.get("state")) for x in rows if _s(x.get("status") or x.get("record_status") or x.get("state"))})
        counters = {"confirmed": 0, "pending": 0, "cancelled": 0}
        for item in rows:
            counters[_status_bucket(_s(item.get("status") or item.get("record_status") or item.get("state")))] += 1

        max_page = max((len(rows) - 1) // PAGE_SIZE, 0)
        st.page = min(st.page, max_page)
        start = st.page * PAGE_SIZE
        page_rows = rows[start : start + PAGE_SIZE]

        lines = [f"📋 Записи — {day_title}"]
        if statuses:
            lines.append(
                f"✅ Подтверждено: {counters['confirmed']} | ⏳ Ожидает: {counters['pending']} | ❌ Отменено: {counters['cancelled']}"
            )
        if not page_rows:
            lines.append("\n😌 На выбранный день записей нет.")

        kb_rows: list[list[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data=f"{CB}:day:today"),
                InlineKeyboardButton(text="📅 Завтра", callback_data=f"{CB}:day:tomorrow"),
            ],
            [
                InlineKeyboardButton(text="👤 Мастер", callback_data=f"{CB}:filter_master"),
                InlineKeyboardButton(text="🧾 Статус", callback_data=f"{CB}:filter_status"),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB}:refresh")],
        ]

        for item in page_rows:
            rid = _pick(item, ("id", "record_id", "booking_id", "visit_id"))
            if rid:
                kb_rows.append([InlineKeyboardButton(text=format_admin_booking_list_item(item), callback_data=f"{CB}:open:{rid}")])

        nav: list[InlineKeyboardButton] = []
        if st.page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB}:page:{st.page - 1}"))
        if st.page < max_page:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB}:page:{st.page + 1}"))
        if nav:
            kb_rows.append(nav)
        kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back")])
        kb_rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])

        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=markup)
            await target.answer()
        else:
            await target.answer(text, reply_markup=markup)

        if client is not None:
            await client.close()
    except YClientsCredentialsError:
        await _safe_error(target, "❌ YClients не настроен. Зайдите в ⚙️ Интеграция YClients")
    except YClientsAuthError:
        await _safe_error(target, "❌ Нет доступа к YClients. Проверьте токены ⚙️")
    except YClientsRateLimitError:
        await _safe_error(target, "⏳ Слишком много запросов. Попробуйте позже 🙂")
    except (YClientsServerError, YClientsTransportError, YClientsUnavailableError):
        await _safe_error(target, "😔 YClients временно недоступен. Попробуйте позже 🙂")
    except Exception:
        logger.exception("admin bookings dashboard failed user_id=%s", user_id)
        await _safe_error(target, "😔 Не удалось загрузить записи. Попробуйте позже 🙂")


async def _safe_error(target: Message | CallbackQuery, text: str) -> None:
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text)
        await target.answer()
        return
    await target.answer(text)


@router.message(F.text == ADMIN_APPOINTMENTS_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_open(message: Message, state: FSMContext) -> None:
    await push_screen(state, "admin_bookings")
    _STATE[message.from_user.id] = DashboardState()
    await _render_dashboard(message, message.from_user.id)


@router.callback_query(F.data.startswith(f"{CB}:day:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_day(callback: CallbackQuery) -> None:
    day = callback.data.split(":")[-1]
    st = _state(callback.from_user.id)
    st.day = "tomorrow" if day == "tomorrow" else "today"
    st.page = 0
    await _render_dashboard(callback, callback.from_user.id)


@router.callback_query(F.data == f"{CB}:refresh")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_refresh(callback: CallbackQuery) -> None:
    await _render_dashboard(callback, callback.from_user.id, force_refresh=True)


@router.callback_query(F.data.startswith(f"{CB}:page:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_page(callback: CallbackQuery) -> None:
    st = _state(callback.from_user.id)
    st.page = max(0, int(callback.data.split(":")[-1]))
    await _render_dashboard(callback, callback.from_user.id)


@router.callback_query(F.data == f"{CB}:filter_master")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_master_picker(callback: CallbackQuery) -> None:
    try:
        client, company_id = await _build_client()
        options = await _load_staff_options(client, company_id)
        await client.close()
    except Exception:
        logger.exception("admin bookings master picker failed")
        await _safe_error(callback, "😔 Не удалось загрузить мастеров. Попробуйте позже 🙂")
        return

    rows = [[InlineKeyboardButton(text="🎲 Все мастера", callback_data=f"{CB}:master:all")]]
    for sid, name in options[:30]:
        rows.append([InlineKeyboardButton(text=f"👤 {name}", callback_data=f"{CB}:master:{sid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back_dashboard")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    await callback.message.edit_text("👤 Выберите мастера для фильтра", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:master:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_set_master(callback: CallbackQuery) -> None:
    value = callback.data.split(":")[-1]
    st = _state(callback.from_user.id)
    st.master_id = None if value == "all" else value
    st.page = 0
    await _render_dashboard(callback, callback.from_user.id)


@router.callback_query(F.data == f"{CB}:filter_status")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_status_picker(callback: CallbackQuery) -> None:
    st = _state(callback.from_user.id)
    date_from, date_to, _ = _date_range(st.day)
    try:
        client, company_id = await _build_client()
        payload = await list_bookings_by_date_range(
            client,
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            staff_id=st.master_id,
            page=1,
            count=200,
        )
        await client.close()
    except Exception:
        logger.exception("admin bookings status picker failed")
        await _safe_error(callback, "😔 Не удалось загрузить статусы. Попробуйте позже 🙂")
        return

    statuses = sorted({_s(x.get("status") or x.get("record_status") or x.get("state")) for x in _extract_rows(payload) if _s(x.get("status") or x.get("record_status") or x.get("state"))})
    rows = [[InlineKeyboardButton(text="📌 Все статусы", callback_data=f"{CB}:status:all")]]
    for status in statuses:
        rows.append([InlineKeyboardButton(text=f"🧾 {status}", callback_data=f"{CB}:status:{status}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back_dashboard")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    await callback.message.edit_text("🧾 Выберите статус для фильтра", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:status:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_set_status(callback: CallbackQuery) -> None:
    value = callback.data.split(":", 2)[-1]
    st = _state(callback.from_user.id)
    st.status = None if value == "all" else value
    st.page = 0
    await _render_dashboard(callback, callback.from_user.id)


@router.callback_query(F.data == f"{CB}:back_dashboard")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_back_dashboard(callback: CallbackQuery) -> None:
    await _render_dashboard(callback, callback.from_user.id)


@router.callback_query(F.data.startswith(f"{CB}:open:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_open_card(callback: CallbackQuery) -> None:
    record_id = callback.data.split(":")[-1]
    try:
        client, company_id = await _build_client()
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        item = _extract_one(payload)
        if not item:
            await _safe_error(callback, "😔 Не удалось открыть запись. Попробуйте позже 🙂")
            return
    except YClientsCredentialsError:
        await _safe_error(callback, "❌ YClients не настроен. Зайдите в ⚙️ Интеграция YClients")
        return
    except YClientsAuthError:
        await _safe_error(callback, "❌ Нет доступа к YClients. Проверьте токены ⚙️")
        return
    except YClientsRateLimitError:
        await _safe_error(callback, "⏳ Слишком много запросов. Попробуйте позже 🙂")
        return
    except (YClientsServerError, YClientsTransportError, YClientsUnavailableError):
        await _safe_error(callback, "😔 YClients временно недоступен. Попробуйте позже 🙂")
        return
    except Exception:
        logger.exception("admin bookings detail failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось открыть запись. Попробуйте позже 🙂")
        return

    await _show_booking_card(callback, item)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:loyalty:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_screen(callback: CallbackQuery, state: FSMContext) -> None:
    record_id = callback.data.split(":")[-1]
    try:
        client, company_id = await _build_client()
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        item = _extract_one(payload)
        if not item:
            await client.close()
            await _safe_error(callback, "😔 Не удалось открыть запись. Попробуйте позже 🙂")
            return
        if not _loyalty_feature_available(item):
            await client.close()
            await _safe_error(callback, "😔 Для этой записи нельзя открыть лояльность. Нужен client_id и record_id 🙂")
            return
        _, client_id = _loyalty_identifiers(item)
        loyalty_payload = await get_loyalty_info(client, company_id=company_id, client_id=str(client_id))
        await client.close()
        points, tier, discount, note = _format_loyalty_snapshot(loyalty_payload)
        await state.clear()
        await state.update_data(loyalty_record_id=record_id, loyalty_client_id=str(client_id), loyalty_company_id=company_id)
        await state.set_state(LoyaltyStates.LOYALTY_CHOOSE_ACTION)
        await callback.message.edit_text(
            _loyalty_screen_text(item, points=points, tier=tier, discount=discount, note=note),
            reply_markup=_loyalty_actions_keyboard(record_id),
        )
        await callback.answer()
    except YClientsBadRequestError as exc:
        msg = str(exc).lower()
        if "loyal" in msg:
            await _safe_error(callback, "😔 Лояльность не настроена в YClients. Проверьте настройки в YClients 🙂")
        else:
            await _safe_error(callback, "😔 Не удалось загрузить данные лояльности. Проверьте параметры записи 🙂")
    except YClientsCredentialsError:
        await _safe_error(callback, "❌ YClients не настроен. Зайдите в ⚙️ Интеграция YClients")
    except YClientsAuthError:
        await _safe_error(callback, "❌ Нет доступа к YClients. Проверьте токены ⚙️")
    except YClientsRateLimitError:
        await _safe_error(callback, "⏳ Слишком много запросов. Попробуйте позже 🙂")
    except (YClientsServerError, YClientsTransportError, YClientsUnavailableError):
        await _safe_error(callback, "😔 YClients временно недоступен. Попробуйте позже 🙂")
    except Exception:
        logger.exception("admin loyalty screen failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось открыть лояльность. Попробуйте позже 🙂")


@router.callback_query(F.data.startswith(f"{CB}:loyalty_action:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_choose_action(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, record_id, action = callback.data.split(":", 3)
    await state.update_data(loyalty_record_id=record_id, loyalty_action=action)
    await state.set_state(LoyaltyStates.LOYALTY_INPUT_VALUE)
    value_hint = "Введите баллы (целое число)." if action in {"accrue", "redeem"} else "Введите скидку в ₽ (например 500)."
    rows = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:loyalty:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    await callback.message.edit_text(f"🎁 Выбрано: {action}\n\n{value_hint}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.message(LoyaltyStates.LOYALTY_INPUT_VALUE)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_input_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    data = await state.get_data()
    action = str(data.get("loyalty_action") or "")
    record_id = str(data.get("loyalty_record_id") or "")
    if not raw:
        await message.answer("⚠️ Введите число, пожалуйста 🙂")
        return
    try:
        value = float(raw)
    except ValueError:
        await message.answer("⚠️ Формат неверный. Пример: 150")
        return
    if value <= 0:
        await message.answer("⚠️ Значение должно быть больше нуля 🙂")
        return
    if action in {"accrue", "redeem"} and not value.is_integer():
        await message.answer("⚠️ Для баллов используйте целое число 🙂")
        return
    normalized_value = str(int(value)) if action in {"accrue", "redeem"} else f"{value:.2f}"
    await state.update_data(loyalty_value=normalized_value)
    await state.set_state(LoyaltyStates.LOYALTY_INPUT_COMMENT)
    rows = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:loyalty:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    await message.answer("📝 Комментарий (необязательно). Отправьте '-' чтобы пропустить.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(LoyaltyStates.LOYALTY_INPUT_COMMENT)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_input_comment(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()
    if comment == "-":
        comment = ""
    data = await state.get_data()
    record_id = str(data.get("loyalty_record_id") or "")
    action = str(data.get("loyalty_action") or "")
    value = str(data.get("loyalty_value") or "")
    await state.update_data(loyalty_comment=comment)
    await state.set_state(LoyaltyStates.LOYALTY_CONFIRM)
    rows = [
        [InlineKeyboardButton(text="✅ Применить", callback_data=f"{CB}:loyalty_apply:{record_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{CB}:loyalty:{record_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:loyalty_back_input:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    after_hint = f"{action} {value} {_value_label(action)}"
    await message.answer(
        "❗️Применить лояльность? 🎁\n\n"
        f"Действие: {action}\n"
        f"Значение: {value} {_value_label(action)}\n"
        f"Комментарий: {comment or '—'}\n"
        f"После применения: {after_hint}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith(f"{CB}:loyalty_back_input:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_back_to_input(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    record_id = callback.data.split(":")[-1]
    action = str(data.get("loyalty_action") or "")
    await state.set_state(LoyaltyStates.LOYALTY_INPUT_VALUE)
    rows = [
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:loyalty:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    value_hint = "Введите баллы (целое число)." if action in {"accrue", "redeem"} else "Введите скидку в ₽ (например 500)."
    await callback.message.edit_text(f"🎁 Выбрано: {action}\n\n{value_hint}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:loyalty_apply:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_loyalty_apply(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    record_id = str(data.get("loyalty_record_id") or callback.data.split(":")[-1])
    client_id = str(data.get("loyalty_client_id") or "") or None
    company_id = str(data.get("loyalty_company_id") or "")
    action = str(data.get("loyalty_action") or "")
    value = str(data.get("loyalty_value") or "")
    comment = str(data.get("loyalty_comment") or "")
    if not acquire_action_lock("admin_loyalty", callback.from_user.id, record_id, ttl_s=4):
        await callback.answer("⏳ Уже выполняю, секундочку 🙂", show_alert=False)
        return
    if not (record_id and company_id and action and value):
        await _safe_error(callback, "😔 Данные лояльности потерялись. Откройте запись заново 🙂")
        await state.clear()
        return
    try:
        client, _ = await _build_client()
        await apply_loyalty_to_visit(
            client,
            company_id=company_id,
            record_id=record_id,
            action_type=action,
            value=value,
            comment=comment or None,
        )
        loyalty_payload: dict[str, Any] | list[Any] = {}
        if client_id:
            loyalty_payload = await get_loyalty_info(client, company_id=company_id, client_id=client_id)
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        item = _extract_one(payload) or {"id": record_id}
        points, tier, discount, note = _format_loyalty_snapshot(loyalty_payload)
        await create_loyalty_action(
            staff_tg_id=callback.from_user.id,
            yclients_visit_or_record_id=record_id,
            yclients_client_id=client_id,
            action_type=action,
            value=value,
            status="success",
        )
        await state.set_state(LoyaltyStates.LOYALTY_CHOOSE_ACTION)
        await callback.message.answer("✅ Готово! Лояльность применена 🎉")
        await callback.message.edit_text(
            _loyalty_screen_text(item, points=points, tier=tier, discount=discount, note=note),
            reply_markup=_loyalty_actions_keyboard(record_id),
        )
        await callback.answer()
    except YClientsBadRequestError as exc:
        await create_loyalty_action(
            staff_tg_id=callback.from_user.id,
            yclients_visit_or_record_id=record_id,
            yclients_client_id=client_id,
            action_type=action or "unknown",
            value=value or "",
            status="fail",
            error_short=str(exc)[:180],
        )
        msg = str(exc).lower()
        if "loyal" in msg:
            await _safe_error(callback, "😔 Лояльность не настроена в YClients. Проверьте настройки в YClients 🙂")
        else:
            await _safe_error(callback, "⚠️ YClients отклонил запрос. Проверьте сумму/баллы и попробуйте снова 🙂")
    except YClientsCredentialsError:
        await _safe_error(callback, "❌ YClients не настроен. Зайдите в ⚙️ Интеграция YClients")
    except YClientsAuthError:
        await _safe_error(callback, "❌ Нет доступа к YClients. Проверьте токены ⚙️")
    except YClientsRateLimitError:
        await _safe_error(callback, "⏳ Слишком много запросов. Попробуйте позже 🙂")
    except (YClientsServerError, YClientsTransportError, YClientsUnavailableError):
        await _safe_error(callback, "😔 YClients временно недоступен. Попробуйте позже 🙂")
    except Exception:
        logger.exception("admin loyalty apply failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось применить лояльность. Попробуйте позже 🙂")


@router.callback_query(F.data.startswith(f"{CB}:action_cancel:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_cancel_confirm(callback: CallbackQuery) -> None:
    record_id = callback.data.split(":")[-1]
    try:
        client, company_id = await _build_client()
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        item = _extract_one(payload)
        if not item:
            await _safe_error(callback, "😔 Не удалось открыть запись. Попробуйте позже 🙂")
            return
    except Exception:
        logger.exception("admin bookings cancel confirm failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось открыть запись. Попробуйте позже 🙂")
        return

    rows = [
        [InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"{CB}:action_cancel_yes:{record_id}")],
        [InlineKeyboardButton(text="❌ Нет", callback_data=f"{CB}:open:{record_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:open:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    await callback.message.edit_text(_format_cancel_summary(item), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:action_cancel_yes:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_cancel_apply(callback: CallbackQuery) -> None:
    record_id = callback.data.split(":")[-1]
    user_id = callback.from_user.id
    if not acquire_action_lock("admin_cancel", user_id, record_id, ttl_s=4):
        await callback.answer("⏳ Уже выполняю, секундочку 🙂", show_alert=False)
        return

    try:
        client, company_id = await _build_client()
        await admin_cancel_booking(client, company_id=company_id, record_id=record_id)
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        _CACHE.clear()
        item = _extract_one(payload) or {"id": record_id, "status": "cancelled"}
        await _log_booking_admin_action(callback.from_user.id, "booking_cancelled_by_staff", "отменил запись клиента через административный раздел", record_id=record_id)
        await callback.message.answer("✅ Запись отменена 🙂")
        await _show_booking_card(callback, item)
        await callback.answer()
    except YClientsCredentialsError:
        await _safe_error(callback, "❌ YClients не настроен. Зайдите в ⚙️ Интеграция YClients")
    except YClientsAuthError:
        await _safe_error(callback, "❌ Нет доступа к YClients. Проверьте токены ⚙️")
    except YClientsRateLimitError:
        await _safe_error(callback, "⏳ Слишком много запросов. Попробуйте позже 🙂")
    except (YClientsServerError, YClientsTransportError, YClientsUnavailableError):
        await _safe_error(callback, "😔 YClients временно недоступен. Попробуйте позже 🙂")
    except Exception:
        logger.exception("admin bookings cancel failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Нельзя отменить эту запись. Проверьте правила в YClients 🙂")


@router.callback_query(F.data.startswith(f"{CB}:action_contact:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_contact(callback: CallbackQuery) -> None:
    record_id = callback.data.split(":")[-1]
    if not acquire_action_lock("admin_contact", callback.from_user.id, record_id, ttl_s=3):
        await callback.answer("⏳ Уже выполняю, секундочку 🙂", show_alert=False)
        return
    try:
        client, company_id = await _build_client()
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        item = _extract_one(payload)
        if not item:
            await _safe_error(callback, "😔 Не удалось открыть контакт клиента. Попробуйте позже 🙂")
            return
    except Exception:
        logger.exception("admin bookings contact failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось открыть контакт клиента. Попробуйте позже 🙂")
        return

    rows = [
        [InlineKeyboardButton(text="📋 Скопировать текст", callback_data=f"{CB}:action_contact_copy:{record_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:open:{record_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ]
    await callback.message.edit_text(_contact_message(item), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith(f"{CB}:action_contact_copy:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def admin_bookings_contact_copy(callback: CallbackQuery) -> None:
    record_id = callback.data.split(":")[-1]
    try:
        client, company_id = await _build_client()
        payload = await get_booking_details_admin(client, company_id=company_id, record_id=record_id)
        await client.close()
        item = _extract_one(payload)
        if not item:
            await _safe_error(callback, "😔 Не удалось подготовить шаблон. Попробуйте позже 🙂")
            return
    except Exception:
        logger.exception("admin bookings contact copy failed record_id=%s", record_id)
        await _safe_error(callback, "😔 Не удалось подготовить шаблон. Попробуйте позже 🙂")
        return

    await _log_booking_admin_action(callback.from_user.id, "client_manual_message_prepared", "подготовил ручное сообщение клиенту по записи", record_id=record_id)
    await callback.message.answer("📋 Скопируйте текст ниже (удержанием сообщения):")
    await callback.message.answer(_contact_message(item).split("📋 Шаблон сообщения:\n", 1)[-1])
    await callback.answer("Готово 🙂")
