"""Clients directory flow for manager+ MAX users."""

from __future__ import annotations

import logging
from datetime import timedelta
from os import getenv
from typing import Any

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import ROLE_USER, is_manager_or_higher
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError, classify_yclients_error
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.integrations.yclients.utils import extract_data_rows, safe_str
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import CompanyTimeService
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)
from max_barbershop_bot.ui.buttons import (
    ADMIN_CLIENTS_DIRECTORY_PAYLOAD,
    CLIENTS_DIRECTORY_BACK_PAYLOAD,
    CLIENTS_DIRECTORY_HOME_PAYLOAD,
    CLIENTS_DIRECTORY_REFRESH_PAYLOAD,
    CLIENTS_DIRECTORY_RESULT_PAYLOAD_PREFIX,
    CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD,
    CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD,
    clients_directory_card_keyboard,
    clients_directory_menu_keyboard,
    clients_directory_results_keyboard,
    clients_directory_search_keyboard,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 8
HISTORY_SIZE = 5
FUTURE_SIZE = 5
MIN_PHONE_DIGITS = 6
MIN_NAME_CHARS = 2
_RESULTS_KEY = "clients_directory_results"
_QUERY_KEY = "clients_directory_query"
_MODE_KEY = "clients_directory_mode"
_SELECTED_KEY = "clients_directory_selected"

STALE_RESULTS_TEXT = "Этот список клиентов уже устарел 🙏\n\nВыполните поиск заново."
NO_ACCESS_TEXT = "⛔️ Этот раздел доступен только команде барбершопа."
NOT_CONFIGURED_TEXT = "⚙️ Не настроены ключи YClients. Проверьте настройки интеграции 🙂"


def register_clients_directory_routes(router: Router) -> None:
    router.on_callback(ADMIN_CLIENTS_DIRECTORY_PAYLOAD, handle_clients_directory_menu)
    router.on_callback(CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD, handle_search_phone)
    router.on_callback(CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD, handle_search_name)
    router.on_callback(CLIENTS_DIRECTORY_REFRESH_PAYLOAD, handle_refresh)
    router.on_callback(CLIENTS_DIRECTORY_BACK_PAYLOAD, handle_back)
    router.on_callback(CLIENTS_DIRECTORY_HOME_PAYLOAD, handle_home)
    for index in range(PAGE_SIZE):
        router.on_callback(f"{CLIENTS_DIRECTORY_RESULT_PAYLOAD_PREFIX}{index}", handle_result)
    router.on_screen_text(state.CLIENTS_DIRECTORY_SEARCH_SCREEN, handle_query_text)


async def handle_clients_directory_menu(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer(context, "Открываем клиентов 👥")
    _push(context, state.CLIENTS_DIRECTORY_MENU_SCREEN)
    _clear(context)
    await _show_menu(context)


async def handle_search_phone(context: RouterContext) -> None:
    await _show_search(context, "phone")


async def handle_search_name(context: RouterContext) -> None:
    await _show_search(context, "name")


async def handle_query_text(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    query = (context.event.text or "").strip()
    mode = str(state.get_state_data_value(_uid(context), _chat(context), _MODE_KEY) or "auto")
    if mode == "phone":
        query = normalize_clients_directory_phone(query)
        if len(_digits(query)) < MIN_PHONE_DIGITS:
            await context.send_text("🙂 Введите телефон подлиннее, хотя бы 6 цифр 📞", keyboard=clients_directory_search_keyboard())
            return
    else:
        if len(query) < MIN_NAME_CHARS:
            await context.send_text("🙂 Введите хотя бы 2 символа имени 🔎", keyboard=clients_directory_search_keyboard())
            return
    state.set_state_data_value(_uid(context), _chat(context), _QUERY_KEY, query)
    await _load_and_show_results(context, force_refresh=True)


async def handle_refresh(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer(context, "Обновляем список 🔄")
    await _load_and_show_results(context, force_refresh=True)


async def handle_result(context: RouterContext) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    index = _payload_index(context.event.callback_payload)
    rows = state.get_state_data_value(_uid(context), _chat(context), _RESULTS_KEY)
    if index is None or not isinstance(rows, list) or index >= len(rows):
        await _answer(context, "Список устарел 🙏")
        await context.send_text(STALE_RESULTS_TEXT, keyboard=clients_directory_menu_keyboard())
        return
    row = rows[index]
    yclients_client_id = _client_id(row)
    if not yclients_client_id:
        await context.send_text(STALE_RESULTS_TEXT, keyboard=clients_directory_menu_keyboard())
        return
    state.set_state_data_value(_uid(context), _chat(context), _SELECTED_KEY, yclients_client_id)
    await _answer(context, "Открываем карточку клиента 👤")
    await _show_card(context, yclients_client_id)


async def handle_back(context: RouterContext) -> None:
    await _answer(context, "Возвращаемся назад ⬅️")
    current = state.get_current_screen(_uid(context), _chat(context))
    if current == state.CLIENTS_DIRECTORY_CARD_SCREEN:
        await _show_results_from_state(context)
        return
    if current == state.CLIENTS_DIRECTORY_RESULTS_SCREEN:
        await _show_search(context, str(state.get_state_data_value(_uid(context), _chat(context), _MODE_KEY) or "name"), answer=False)
        return
    if current == state.CLIENTS_DIRECTORY_SEARCH_SCREEN:
        await _show_menu(context)
        return
    await show_home(context)


async def handle_home(context: RouterContext) -> None:
    await _answer(context, "Открываем главное меню 🏠")
    _clear(context)
    await show_home(context)


async def _show_menu(context: RouterContext) -> None:
    state.set_current_screen(_uid(context), _chat(context), state.CLIENTS_DIRECTORY_MENU_SCREEN)
    await context.send_text("Введите телефон или имя клиента 📲🙂", keyboard=clients_directory_menu_keyboard())


async def _show_search(context: RouterContext, mode: str, *, answer: bool = True) -> None:
    if not _can_access(context):
        await _send_no_access(context)
        return
    if answer:
        await _answer(context, "Введите запрос 👇")
    _push(context, state.CLIENTS_DIRECTORY_SEARCH_SCREEN)
    state.set_state_data_value(_uid(context), _chat(context), _MODE_KEY, mode)
    text = "✍️ Введите телефон клиента в чат, я сразу найду клиента 🙂" if mode == "phone" else "✍️ Введите имя клиента в чат, я сразу найду клиента 🙂"
    await context.send_text(text, keyboard=clients_directory_search_keyboard())


async def _load_and_show_results(context: RouterContext, *, force_refresh: bool = False) -> None:
    del force_refresh
    mode = str(state.get_state_data_value(_uid(context), _chat(context), _MODE_KEY) or "name")
    query = str(state.get_state_data_value(_uid(context), _chat(context), _QUERY_KEY) or "").strip()
    try:
        async with _yclients_layer() as service:
            rows = [card.raw | {"id": card.id, "name": card.name, "phone": card.phone} for card in await service.find_client(query=query, by_phone=mode == "phone", by_name=mode == "name", count=PAGE_SIZE + 1)]
    except Exception as exc:  # noqa: BLE001
        await _send_yclients_error(context, exc, search_type=mode, query_present=bool(query))
        return
    visible = rows[:PAGE_SIZE]
    state.set_state_data_value(_uid(context), _chat(context), _RESULTS_KEY, visible)
    _diag(search_type=mode, query_present=bool(query), normalized_phone_present=mode == "phone" and bool(query), results_count=len(visible))
    await _show_results_from_state(context, has_next=len(rows) > PAGE_SIZE)


async def _show_results_from_state(context: RouterContext, *, has_next: bool = False) -> None:
    rows = state.get_state_data_value(_uid(context), _chat(context), _RESULTS_KEY)
    visible = rows if isinstance(rows, list) else []
    state.set_current_screen(_uid(context), _chat(context), state.CLIENTS_DIRECTORY_RESULTS_SCREEN)
    if not visible:
        await context.send_text("😔 Клиент не найден. Попробуйте другой запрос 🙂", keyboard=clients_directory_search_keyboard())
        return
    await context.send_text("👥 Результаты поиска\n\nВыберите клиента ниже 👇", keyboard=clients_directory_results_keyboard(visible, has_next=has_next))


async def _show_card(context: RouterContext, yclients_client_id: str) -> None:
    time_service = CompanyTimeService(YClientsSettingsRepository(_database_path()))
    today = time_service.today().isoformat()
    end = (time_service.today() + timedelta(days=365)).isoformat()
    try:
        async with _yclients_layer() as service:
            card = await service.get_client_card(yclients_client_id=yclients_client_id)
            future_payload = await service.get_future_records(yclients_client_id=yclients_client_id, start_date=today, end_date=end, count=20)
            visits = await service.get_client_visits(yclients_client_id=yclients_client_id, count=HISTORY_SIZE)
    except Exception as exc:  # noqa: BLE001
        await _send_yclients_error(context, exc, selected_yclients_client_id_present=bool(yclients_client_id))
        return
    future = _active_future_records(extract_data_rows(future_payload), time_service)[:FUTURE_SIZE]
    _diag(selected_yclients_client_id_present=True, future_bookings_count=len(future), history_count=len(visits), loyalty_present=False)
    text = format_clients_directory_card(card.raw if card else {"id": yclients_client_id}, future, [visit.raw for visit in visits], time_service)
    state.set_current_screen(_uid(context), _chat(context), state.CLIENTS_DIRECTORY_CARD_SCREEN)
    await context.send_text(text, keyboard=clients_directory_card_keyboard())


def format_clients_directory_card(client_row: dict[str, Any], future: list[dict[str, Any]], history: list[dict[str, Any]], time_service: CompanyTimeService) -> str:
    yclients_client_id = _client_id(client_row) or "—"
    lines = [
        "👤 Карточка клиента",
        "",
        f"👤 Имя: {_name(client_row)}",
        f"📞 Телефон: {_mask_phone(_phone(client_row))}",
        f"🆔 Client ID: {yclients_client_id}",
        "",
        "📅 Будущие записи",
    ]
    if future:
        for item in future:
            lines.append(f"• 🕒 {_when(item, time_service)} • 💈 {_service(item)} • 👨‍🔧 {_master(item)} • {_status(item)}")
    else:
        lines.append("Будущих записей пока нет 🙂")
    lines.extend(["", "📅 История визитов"])
    if history:
        for item in history[:HISTORY_SIZE]:
            price = _price(item)
            price_part = f" • 💳 {price}" if price != "—" else ""
            lines.append(f"• 🕒 {_when(item, time_service)} • 💈 {_service(item)} • 👨‍🔧 {_master(item)} • {_status(item)}{price_part}")
    else:
        lines.append("История визитов пока пустая 🙂")
    loyalty = _loyalty(client_row)
    if loyalty:
        lines.extend(["", "💎 Лояльность", loyalty])
    return "\n".join(lines)


def normalize_clients_directory_phone(value: str) -> str:
    digits = _digits(value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    if value.strip().startswith("+7") and digits.startswith("7"):
        return f"+{digits}"
    return f"+{digits}" if digits else ""


def _active_future_records(rows: list[dict[str, Any]], time_service: CompanyTimeService) -> list[dict[str, Any]]:
    now = time_service.now()
    active = []
    for row in rows:
        dt = time_service.localize_datetime(row.get("datetime") or row.get("date"))
        if dt is None or dt < now:
            continue
        status = _status(row).lower()
        if any(word in status for word in ("отмен", "cancel", "deleted")):
            continue
        active.append(row)
    return sorted(active, key=lambda item: time_service.localize_datetime(item.get("datetime") or item.get("date")) or now)


class _yclients_layer:
    async def __aenter__(self) -> YClientsServiceLayer:
        settings = load_active_yclients_settings(YClientsSettingsRepository(_database_path()), operation="clients_directory")
        if not has_required_yclients_credentials(settings):
            raise RuntimeError("yclients_not_configured")
        self.client = build_yclients_client_from_active_settings(settings)  # type: ignore[arg-type]
        return YClientsServiceLayer(self.client)
    async def __aexit__(self, *_args: object) -> None:
        await self.client.close()


async def _send_yclients_error(context: RouterContext, exc: Exception, **diag: object) -> None:
    category = classify_yclients_error(exc) if isinstance(exc, YClientsError) else "credentials" if str(exc) == "yclients_not_configured" else "unavailable"
    _diag(yclients_error_category=category, http_status=getattr(exc, "status_code", None), trace_id=getattr(exc, "trace_id", None), **diag)
    if category == "credentials":
        text = NOT_CONFIGURED_TEXT
    elif category == "auth":
        text = "🔐 Ошибка доступа к YClients. Проверьте токены и права 🙂"
    elif category == "rate_limit":
        text = "⏳ Слишком много запросов. Попробуйте через пару секунд 🙂"
    else:
        text = "🛠️ YClients временно недоступен. Попробуйте чуть позже 🙂"
    await context.send_text(text, keyboard=clients_directory_menu_keyboard())


async def _send_no_access(context: RouterContext) -> None:
    await _answer(context, NO_ACCESS_TEXT)
    await context.send_text(NO_ACCESS_TEXT)


def _can_access(context: RouterContext) -> bool:
    return is_manager_or_higher(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    if context.event.platform_user_id is None:
        return ROLE_USER
    return StaffRolesRepository(_database_path()).get_highest_role(context.event.platform_user_id, platform=PLATFORM_MAX)


def _push(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(_uid(context), _chat(context))
    if current != screen_id:
        state.push_screen(_uid(context), _chat(context), current)
    state.set_current_screen(_uid(context), _chat(context), screen_id)


def _clear(context: RouterContext) -> None:
    state.clear_state_data(_uid(context), _chat(context))


def _uid(context: RouterContext) -> str | None: return context.event.platform_user_id

def _chat(context: RouterContext) -> str | None: return context.event.chat_id

async def _answer(context: RouterContext, text: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(text)

def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH

def _digits(value: str) -> str: return "".join(ch for ch in str(value or "") if ch.isdigit())
def _payload_index(payload: str | None) -> int | None:
    raw = str(payload or "").removeprefix(CLIENTS_DIRECTORY_RESULT_PAYLOAD_PREFIX)
    return int(raw) if raw.isdigit() else None

def _client_id(item: dict[str, Any]) -> str: return safe_str(item.get("id") or item.get("client_id"))
def _name(item: dict[str, Any]) -> str: return safe_str(item.get("name") or item.get("fullname")) or "Клиент"
def _phone(item: dict[str, Any]) -> str: return safe_str(item.get("phone") or item.get("tel"))
def _mask_phone(phone: str | None) -> str:
    digits = _digits(str(phone or ""))
    return "—" if not digits else f"+{'*' * max(1, len(digits) - 4)}{digits[-4:]}"
def _when(item: dict[str, Any], time_service: CompanyTimeService) -> str: return time_service.format_datetime(item.get("datetime") or item.get("date"))
def _service(item: dict[str, Any]) -> str: return safe_str(item.get("service_name") or item.get("service") or item.get("services_title")) or "—"
def _master(item: dict[str, Any]) -> str: return safe_str(item.get("staff_name") or item.get("staff") or item.get("master_name")) or "—"
def _status(item: dict[str, Any]) -> str: return safe_str(item.get("status") or item.get("attendance") or item.get("state")) or "—"
def _price(item: dict[str, Any]) -> str: return safe_str(item.get("price") or item.get("cost") or item.get("paid_full")) or "—"
def _loyalty(item: dict[str, Any]) -> str:
    value = item.get("loyalty") or item.get("loyalty_balance") or item.get("balance")
    return f"Баланс: {value}" if value not in (None, "", []) else ""

def _diag(**fields: object) -> None:
    safe = {key: value for key, value in fields.items() if value is not None}
    logger.info("MAX clients directory diagnostic: %s", safe)
