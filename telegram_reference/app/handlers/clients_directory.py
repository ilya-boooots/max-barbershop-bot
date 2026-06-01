from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.branding import clients_directory_template
from app.core.navigation import push_screen
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, has_any_role, require_roles
from app.core.ui_texts import CLIENTS_BTN
from app.integrations.yclients import (
    YClientsAuthError,
    YClientsBadRequestError,
    YClientsClient,
    YClientsCredentialsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    get_yclients_credentials,
)
from app.integrations.yclients.endpoints import get_client_details, list_client_visits, search_clients

router = Router()
logger = logging.getLogger(__name__)

CB = "clients"
PAGE_SIZE = 8
HISTORY_SIZE = 5
CACHE_TTL_S = 45
MIN_PHONE_DIGITS = 6
MIN_NAME_CHARS = 2


@dataclass
class SearchContext:
    query: str = ""
    mode: str = "auto"
    page: int = 1
    selected_client_id: str | None = None


@dataclass
class CacheItem:
    value: list[dict[str, Any]]
    expire_at: float


_CONTEXT_BY_TG_ID: dict[int, SearchContext] = {}
_CACHE: dict[tuple[str, str, int], CacheItem] = {}


class ClientDirectoryStates(StatesGroup):
    awaiting_query = State()


def _state(tg_id: int) -> SearchContext:
    return _CONTEXT_BY_TG_ID.setdefault(tg_id, SearchContext())


def _mask_phone(phone: str | None) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return "—"
    if len(digits) <= 2:
        return "**"
    if len(digits) <= 4:
        return f"***{digits[-2:]}"
    return f"+{'*' * max(1, len(digits) - 4)}{digits[-4:]}"


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return f"+{digits}"


def _detect_mode(query: str) -> tuple[str, str]:
    digits = "".join(ch for ch in query if ch.isdigit())
    if digits:
        return "phone", _normalize_phone(query)
    return "name", query.strip()


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _extract_client_id(item: dict[str, Any]) -> str:
    for key in ("id", "client_id"):
        value = item.get(key)
        if value is not None:
            value_str = str(value).strip()
            if value_str:
                return value_str
    return ""


def _extract_name(item: dict[str, Any]) -> str:
    for key in ("name", "fullname"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    first = str(item.get("first_name") or "").strip()
    last = str(item.get("last_name") or "").strip()
    return f"{first} {last}".strip() or "Клиент"


def _extract_phone(item: dict[str, Any]) -> str:
    for key in ("phone",):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "—"


def _results_kb(items: list[dict[str, Any]], page: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        client_id = _extract_client_id(item)
        if not client_id:
            continue
        label = f"👤 {_extract_name(item)} • 📞 {_mask_phone(_extract_phone(item))}"
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=f"{CB}:open:{client_id}")])

    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"{CB}:page:{page - 1}"))
    if has_next:
        pager.append(InlineKeyboardButton(text="➡️ След", callback_data=f"{CB}:page:{page + 1}"))
    if pager:
        rows.append(pager)

    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB}:refresh")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back:search")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_kb(client_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 История визитов", callback_data=f"{CB}:history:{client_id}:1")],
            [InlineKeyboardButton(text="📋 Шаблон сообщения", callback_data=f"{CB}:template:{client_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back:results")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


def _history_kb(client_id: str, page: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="⬅️ Пред", callback_data=f"{CB}:history:{client_id}:{page - 1}"))
    if has_next:
        pager.append(InlineKeyboardButton(text="➡️ След", callback_data=f"{CB}:history:{client_id}:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:open:{client_id}")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _build_client() -> tuple[YClientsClient, str]:
    return await build_yclients_client()


def _cache_get(key: tuple[str, str, int]) -> list[dict[str, Any]] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    if item.expire_at <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return item.value


def _cache_set(key: tuple[str, str, int], rows: list[dict[str, Any]]) -> None:
    _CACHE[key] = CacheItem(value=rows, expire_at=time.monotonic() + CACHE_TTL_S)


async def _safe_search(staff_id: int, *, force_refresh: bool = False) -> tuple[list[dict[str, Any]], bool]:
    context = _state(staff_id)
    cache_key = (context.mode, context.query.lower(), context.page)
    if force_refresh:
        _CACHE.pop(cache_key, None)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:PAGE_SIZE], len(cached) > PAGE_SIZE

    client: YClientsClient | None = None
    try:
        client, company_id = await _build_client()
        payload = await search_clients(
            client,
            company_id=company_id,
            query=context.query,
            page=context.page,
            count=PAGE_SIZE + 1,
            by_phone=context.mode == "phone",
            by_name=context.mode == "name",
        )
        rows = _extract_rows(payload)
        _cache_set(cache_key, rows)
        return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE
    finally:
        if client is not None:
            await client.close()


async def _show_search_prompt(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ClientDirectoryStates.awaiting_query)
    text = "Введите телефон или имя клиента 📲🙂"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📞 По телефону", callback_data=f"{CB}:mode:phone")],
            [InlineKeyboardButton(text="🔎 По имени", callback_data=f"{CB}:mode:name")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


async def _show_results(target: Message | CallbackQuery, staff_id: int) -> None:
    rows, has_next = await _safe_search(staff_id)
    if not rows:
        text = "😔 Клиент не найден. Попробуйте другой запрос 🙂"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB}:back:search")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        )
    else:
        context = _state(staff_id)
        text = f"👥 Результаты поиска • стр. {context.page}\n\nВыберите клиента ниже 👇"
        kb = _results_kb(rows, context.page, has_next)

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb)


def _fmt_datetime(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return raw[:16]


async def _show_card(callback: CallbackQuery, client_id: str) -> None:
    staff_id = callback.from_user.id
    context = _state(staff_id)
    context.selected_client_id = client_id

    client: YClientsClient | None = None
    try:
        client, company_id = await _build_client()
        payload = await get_client_details(client, company_id=company_id, client_id=client_id)
        row = _extract_rows(payload)
        client_row = row[0] if row else {}

        name = _extract_name(client_row)
        phone = _mask_phone(_extract_phone(client_row))
        last_visit = _extract_value(client_row, ("last_visit_date", "last_visit"))
        visits_count = _extract_value(client_row, ("visits_count",))
        spent_total = _extract_value(client_row, ("spent", "total_spent"))
        notes = _extract_value(client_row, ("comment", "notes"))

        text = (
            "👤 Карточка клиента\n\n"
            f"👤 Имя: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"🆔 Client ID: {client_id}\n"
            f"🕒 Последний визит: {last_visit}\n"
            f"🧾 Кол-во визитов: {visits_count}\n"
            f"💳 Потрачено: {spent_total}\n"
            f"📝 Заметки: {notes}"
        )
        if callback.message:
            await callback.message.edit_text(text, reply_markup=_card_kb(client_id))
        await callback.answer()
    finally:
        if client is not None:
            await client.close()


async def _show_history(callback: CallbackQuery, client_id: str, page: int) -> None:
    client: YClientsClient | None = None
    try:
        client, company_id = await _build_client()
        payload = await list_client_visits(client, company_id=company_id, client_id=client_id, page=page, count=HISTORY_SIZE + 1)
        visits = _extract_rows(payload)
        visible = visits[:HISTORY_SIZE]
        has_next = len(visits) > HISTORY_SIZE
        if not visible:
            text = "📅 История визитов пока пустая 🙂"
        else:
            lines = ["📅 История визитов", ""]
            for item in visible:
                when = _fmt_datetime(_extract_value(item, ("datetime", "date")))
                service = _extract_value(item, ("service_name", "service"))
                master = _extract_value(item, ("staff_name", "staff"))
                lines.append(f"• 🕒 {when} • 💈 {service} • 👨‍🔧 {master}")
            text = "\n".join(lines)

        if callback.message:
            await callback.message.edit_text(text, reply_markup=_history_kb(client_id, page, has_next))
        await callback.answer()
    finally:
        if client is not None:
            await client.close()


async def _send_friendly_error(target: Message | CallbackQuery, exc: Exception) -> None:
    logger.exception("Clients directory error: %s", type(exc).__name__)
    if isinstance(exc, YClientsCredentialsError):
        text = "⚙️ Не настроены ключи YClients. Проверьте настройки интеграции 🙂"
    elif isinstance(exc, YClientsAuthError):
        text = "🔐 Ошибка доступа к YClients. Проверьте токены и права 🙂"
    elif isinstance(exc, YClientsRateLimitError):
        text = "⏳ Слишком много запросов. Попробуйте через пару секунд 🙂"
    elif isinstance(exc, YClientsBadRequestError):
        text = "📝 Не удалось выполнить поиск. Уточните запрос и попробуйте снова 🙂"
    elif isinstance(exc, (YClientsTransportError, YClientsServerError)):
        text = "🛠️ YClients временно недоступен. Попробуйте чуть позже 🙂"
    else:
        text = "😕 Не удалось открыть каталог клиентов. Попробуйте ещё раз 🙂"

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text)
        await target.answer()
    else:
        await target.answer(text)


@router.message(F.text == CLIENTS_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_open(message: Message, state: FSMContext) -> None:
    await push_screen(state, "clients")
    _CONTEXT_BY_TG_ID[message.from_user.id] = SearchContext()
    await _show_search_prompt(message, state)


@router.callback_query(F.data == f"{CB}:back:search")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_back_search(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_search_prompt(callback, state)


@router.callback_query(F.data.startswith(f"{CB}:mode:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_mode(callback: CallbackQuery) -> None:
    mode = (callback.data or "").split(":")[-1]
    context = _state(callback.from_user.id)
    context.mode = mode if mode in {"phone", "name"} else "auto"
    if callback.message:
        await callback.message.answer("✍️ Введите запрос в чат, я сразу найду клиента 🙂")
    await callback.answer()


@router.message(ClientDirectoryStates.awaiting_query, F.text)
async def handle_clients_query(message: Message, state: FSMContext) -> None:
    # role check in message handler because this is broad catch-all
    if message.from_user is None:
        return
    tg_id = message.from_user.id
    if not await has_any_role(tg_id, {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}):
        return

    raw_query = (message.text or "").strip()
    context = _state(tg_id)
    mode, normalized = _detect_mode(raw_query)
    chosen_mode = context.mode if context.mode in {"phone", "name"} else mode
    if chosen_mode == "phone":
        digits = "".join(ch for ch in normalized if ch.isdigit())
        if len(digits) < MIN_PHONE_DIGITS:
            await message.answer("🙂 Введите телефон подлиннее, хотя бы 6 цифр 📞")
            return
    else:
        if len(normalized) < MIN_NAME_CHARS:
            await message.answer("🙂 Введите хотя бы 2 символа имени 🔎")
            return

    context.query = normalized
    context.mode = chosen_mode
    context.page = 1

    try:
        await _show_results(message, tg_id)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(message, exc)


@router.callback_query(F.data.startswith(f"{CB}:page:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_page(callback: CallbackQuery) -> None:
    try:
        page = int((callback.data or "").split(":")[-1])
    except ValueError:
        page = 1
    context = _state(callback.from_user.id)
    context.page = max(1, page)
    try:
        await _show_results(callback, callback.from_user.id)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(callback, exc)


@router.callback_query(F.data == f"{CB}:refresh")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_refresh(callback: CallbackQuery) -> None:
    context = _state(callback.from_user.id)
    _CACHE.pop((context.mode, context.query.lower(), context.page), None)
    try:
        await _show_results(callback, callback.from_user.id)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(callback, exc)


@router.callback_query(F.data.startswith(f"{CB}:open:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_open_card(callback: CallbackQuery) -> None:
    client_id = (callback.data or "").split(":")[-1]
    try:
        await _show_card(callback, client_id)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(callback, exc)


@router.callback_query(F.data == f"{CB}:back:results")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_back_results(callback: CallbackQuery) -> None:
    try:
        await _show_results(callback, callback.from_user.id)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(callback, exc)


@router.callback_query(F.data.startswith(f"{CB}:history:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_history(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    client_id = parts[2]
    try:
        page = max(1, int(parts[3]))
    except ValueError:
        page = 1
    try:
        await _show_history(callback, client_id, page)
    except Exception as exc:  # noqa: BLE001
        await _send_friendly_error(callback, exc)


@router.callback_query(F.data.startswith(f"{CB}:template:"))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_clients_template(callback: CallbackQuery) -> None:
    client_id = (callback.data or "").split(":")[-1]
    context = _state(callback.from_user.id)
    template = clients_directory_template(client_id, context.query)
    if callback.message:
        await callback.message.answer(template)
    await callback.answer("Готово")
