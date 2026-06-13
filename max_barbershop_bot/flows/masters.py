"""Real masters section for the MAX bot."""

from __future__ import annotations

import logging

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows.booking import start_staff_first_booking_with_master
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.booking import BookingMasterItem, BookingService, BookingServiceError, format_master_title
from max_barbershop_bot.services.master_photos import MasterPhotosService
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import MENU_MASTERS_PAYLOAD
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard

logger = logging.getLogger(__name__)

MASTERS_ITEM_PAYLOAD_PREFIX = "masters:item:"
MASTERS_BACK_PAYLOAD = "masters:back"
MASTERS_HOME_PAYLOAD = "masters:home"
MASTERS_BOOK_PAYLOAD = "masters:book"

_MASTERS_STATE_KEY = "masters_section_items"
_SELECTED_MASTER_INDEX_STATE_KEY = "masters_section_selected_index"
_MAX_MASTERS = 20

MASTERS_LIST_TEXT = "Выберите мастера 💈"
MASTERS_EMPTY_TEXT = """Мастера пока недоступны 🙏

Пожалуйста, попробуйте позже."""
MASTERS_ERROR_TEXT = """Не удалось загрузить мастеров 🙏

Пожалуйста, попробуйте позже."""
MASTERS_STALE_TEXT = """Данные о мастерах устарели 🙏

Откройте список мастеров заново."""


def register_masters_routes(router: Router) -> None:
    """Register masters section callbacks."""

    router.on_callback(MENU_MASTERS_PAYLOAD, handle_masters_start)
    router.on_callback(MASTERS_BACK_PAYLOAD, handle_masters_back)
    router.on_callback(MASTERS_HOME_PAYLOAD, handle_masters_home)
    router.on_callback(MASTERS_BOOK_PAYLOAD, handle_masters_book)
    for index in range(_MAX_MASTERS):
        router.on_callback(f"{MASTERS_ITEM_PAYLOAD_PREFIX}{index}", handle_masters_item)


async def handle_masters_start(context: RouterContext) -> None:
    """Open a real YClients-backed masters list."""

    await context.answer_callback("Открываем мастеров 💈")
    await _open_masters_list(context)


async def handle_masters_item(context: RouterContext) -> None:
    """Open selected master detail by short index payload."""

    await context.answer_callback("Открываем карточку мастера 💈")
    index = _payload_index(context.event.callback_payload)
    masters = _masters(context)
    if index is None or masters is None or index < 0 or index >= len(masters):
        await _show_stale(context)
        return
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_INDEX_STATE_KEY, index)
    await _show_master_detail(context, masters[index])


async def handle_masters_book(context: RouterContext) -> None:
    """Start staff-first booking with the selected master."""

    await context.answer_callback("Записываемся к мастеру ✂️")
    index = state.get_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_INDEX_STATE_KEY)
    masters = _masters(context)
    if not isinstance(index, int) or masters is None or index < 0 or index >= len(masters):
        await _show_stale(context)
        return
    await start_staff_first_booking_with_master(context, masters[index])


async def handle_masters_back(context: RouterContext) -> None:
    """Back from detail to list, or from list to main menu."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.MASTER_DETAILS_SCREEN:
        masters = _masters(context)
        if masters:
            await _show_masters_list(context, masters, push_current=False)
            return
    await show_home(context)


async def handle_masters_home(context: RouterContext) -> None:
    """Return to role-aware main menu."""

    await context.answer_callback("Открываем главное меню 🏠")
    await show_home(context)


async def _open_masters_list(context: RouterContext) -> None:
    service = BookingService(YClientsSettingsRepository(DEFAULT_DATABASE_PATH))
    try:
        masters = await service.get_available_masters()
    except BookingServiceError as exc:
        logger.warning("Masters section load failed: error_class=%s", type(exc).__name__)
        _push_current_screen(context, state.MASTERS_SCREEN)
        await context.send_text(MASTERS_ERROR_TEXT, keyboard=_masters_navigation_keyboard())
        return

    masters = masters[:_MAX_MASTERS]
    state.set_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY, masters)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_MASTER_INDEX_STATE_KEY, None)
    await _show_masters_list(context, masters)


async def _show_masters_list(context: RouterContext, masters: list[BookingMasterItem], *, push_current: bool = True) -> None:
    if push_current:
        _push_current_screen(context, state.MASTERS_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.MASTERS_SCREEN)
    if not masters:
        await context.send_text(MASTERS_EMPTY_TEXT, keyboard=_masters_navigation_keyboard())
        return
    rows = [
        [MaxButton(text=format_master_title(master), payload=f"{MASTERS_ITEM_PAYLOAD_PREFIX}{index}")]
        for index, master in enumerate(masters)
    ]
    rows.extend(_navigation_rows())
    await context.send_text(MASTERS_LIST_TEXT, keyboard=MaxInlineKeyboard.from_rows(rows))


async def _show_master_detail(context: RouterContext, master: BookingMasterItem) -> None:
    _push_current_screen(context, state.MASTER_DETAILS_SCREEN)
    attachment = _master_photo_attachment(master.yclients_master_id)
    await context.send_text(
        _master_detail_text(master),
        keyboard=MaxInlineKeyboard.from_rows(
            [
                [MaxButton(text="✂️ Записаться к мастеру", payload=MASTERS_BOOK_PAYLOAD)],
                [MaxButton(text="⬅️ Назад", payload=MASTERS_BACK_PAYLOAD)],
                [MaxButton(text="🏠 Главное меню", payload=MASTERS_HOME_PAYLOAD)],
            ]
        ),
        attachments=[attachment] if attachment else None,
    )


def _master_detail_text(master: BookingMasterItem) -> str:
    lines = [f"💈 {master.title}"]
    if master.specialization:
        lines.append(f"Специализация: {master.specialization}")
    if master.rating:
        lines.append(f"Рейтинг: ⭐️ {master.rating}")
    return "\n".join(lines)


def _master_photo_attachment(yclients_staff_id: str | None) -> dict[str, object] | None:
    try:
        service = MasterPhotosService(
            MasterPhotosRepository(DEFAULT_DATABASE_PATH),
            YClientsSettingsRepository(DEFAULT_DATABASE_PATH),
        )
        return service.photo_attachment(yclients_staff_id)
    except Exception as exc:  # noqa: BLE001 - photo is optional UX.
        logger.warning("Masters section photo skipped safely: error_class=%s", type(exc).__name__)
        return None


async def _show_stale(context: RouterContext) -> None:
    await context.send_text(
        MASTERS_STALE_TEXT,
        keyboard=MaxInlineKeyboard.from_rows(
            [
                [MaxButton(text="👥 Мастера", payload=MENU_MASTERS_PAYLOAD)],
                [MaxButton(text="🏠 Главное меню", payload=MASTERS_HOME_PAYLOAD)],
            ]
        ),
    )


def _masters_navigation_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows(_navigation_rows())


def _navigation_rows() -> list[list[MaxButton]]:
    return [
        [MaxButton(text="⬅️ Назад", payload=MASTERS_BACK_PAYLOAD)],
        [MaxButton(text="🏠 Главное меню", payload=MASTERS_HOME_PAYLOAD)],
    ]


def _masters(context: RouterContext) -> list[BookingMasterItem] | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _MASTERS_STATE_KEY)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, BookingMasterItem)]


def _payload_index(payload: str | None) -> int | None:
    if not payload or not payload.startswith(MASTERS_ITEM_PAYLOAD_PREFIX):
        return None
    raw = payload.removeprefix(MASTERS_ITEM_PAYLOAD_PREFIX)
    if not raw.isdigit():
        return None
    return int(raw)


def _push_current_screen(context: RouterContext, next_screen: str) -> None:
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current != next_screen:
        state.push_screen(_user_id(context), _chat_id(context), current)
    state.set_current_screen(_user_id(context), _chat_id(context), next_screen)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id or context.event.max_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id
