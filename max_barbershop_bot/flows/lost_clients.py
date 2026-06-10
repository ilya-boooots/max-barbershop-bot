"""Dedicated lost clients UI flow for the MAX bot."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_broadcasts
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows.broadcasts import open_segment_broadcast_text
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.lost_clients import (
    LostClientsLoadError,
    LostClientsNotConfiguredError,
    LostClientsResult,
    LostClientsService,
    format_lost_clients_summary,
    lost_clients_to_broadcast_recipients,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    LOST_CLIENTS_BACK_PAYLOAD,
    LOST_CLIENTS_BROADCAST_PAYLOAD,
    LOST_CLIENTS_HOME_PAYLOAD,
    LOST_CLIENTS_OPEN_PAYLOAD,
    LOST_CLIENTS_REFRESH_PAYLOAD,
    client_segments_menu_keyboard,
    lost_clients_result_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BROADCAST_NO_ACCESS_TEXT,
    LOST_CLIENTS_BROADCAST_LIMIT_TEXT,
    LOST_CLIENTS_LOAD_ERROR_TEXT,
    LOST_CLIENTS_ZERO_RECIPIENTS_TEXT,
    YCLIENTS_NOT_CONFIGURED_TEXT,
)

logger = logging.getLogger(__name__)

_LOST_CLIENTS_RESULT_KEY = "lost_clients_result"
_LOST_CLIENTS_RECIPIENTS_KEY = "lost_clients_recipients"


def register_lost_clients_routes(router: Router) -> None:
    """Register dedicated lost clients callbacks."""

    router.on_callback(LOST_CLIENTS_OPEN_PAYLOAD, handle_lost_clients_open)
    router.on_callback(LOST_CLIENTS_REFRESH_PAYLOAD, handle_lost_clients_refresh)
    router.on_callback(LOST_CLIENTS_BROADCAST_PAYLOAD, handle_lost_clients_broadcast)
    router.on_callback(LOST_CLIENTS_BACK_PAYLOAD, handle_lost_clients_back)
    router.on_callback(LOST_CLIENTS_HOME_PAYLOAD, handle_lost_clients_home)


async def handle_lost_clients_open(context: RouterContext) -> None:
    """Open and load the dedicated lost clients screen."""

    if not _can_open_lost_clients(context):
        await _send_no_access(context)
        return
    await _show_lost_clients(context)


async def handle_lost_clients_refresh(context: RouterContext) -> None:
    """Reload lost clients from YClients."""

    if not _can_open_lost_clients(context):
        await _send_no_access(context)
        return
    await _show_lost_clients(context, notification="Обновляем потерянных клиентов 🔄")


async def handle_lost_clients_broadcast(context: RouterContext) -> None:
    """Start one-time broadcast wizard for reachable lost clients."""

    if not _can_open_lost_clients(context):
        await _send_no_access(context)
        return
    result = _stored_result(context)
    if result is None:
        await _show_lost_clients(context, notification="Загружаем потерянных клиентов 😔")
        result = _stored_result(context)
        if result is None:
            return

    recipients = lost_clients_to_broadcast_recipients(result.clients)
    await _answer_callback(context, "Готовим рассылку по потерянным клиентам 📣")
    if not recipients:
        await context.send_text(LOST_CLIENTS_ZERO_RECIPIENTS_TEXT, keyboard=lost_clients_result_keyboard(can_broadcast=False))
        return

    if result.total > len(recipients):
        await context.send_text(
            f"{LOST_CLIENTS_BROADCAST_LIMIT_TEXT}\n\nДоступно получателей: {len(recipients)} из {result.total}",
            keyboard=lost_clients_result_keyboard(can_broadcast=True),
        )

    await open_segment_broadcast_text(
        context,
        audience_key="lost_clients",
        audience_label="😔 Потерянные клиенты",
        recipients=recipients,
        return_screen=state.LOST_CLIENTS_SCREEN,
    )


async def handle_lost_clients_back(context: RouterContext) -> None:
    """Return from lost clients screen to client segments menu."""

    await _answer_callback(context, "Возвращаемся назад ⬅️")
    _clear_lost_clients_state(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENTS_MENU_SCREEN)
    await context.send_text("🎯 Сегменты клиентов\n\nВыберите сегмент:", keyboard=client_segments_menu_keyboard())


async def handle_lost_clients_home(context: RouterContext) -> None:
    """Return to role-based main menu."""

    await _answer_callback(context, "Открываем главное меню 🏠")
    _clear_lost_clients_state(context)
    await show_home(context)


async def _show_lost_clients(context: RouterContext, *, notification: str = "Загружаем потерянных клиентов 😔") -> None:
    await _answer_callback(context, notification)
    try:
        result = await LostClientsService(_yclients_settings_repository(), _users_repository()).get_lost_clients()
    except LostClientsNotConfiguredError:
        state.set_current_screen(_user_id(context), _chat_id(context), state.LOST_CLIENTS_SCREEN)
        await context.send_text(YCLIENTS_NOT_CONFIGURED_TEXT, keyboard=lost_clients_result_keyboard(can_broadcast=False))
        return
    except LostClientsLoadError:
        state.set_current_screen(_user_id(context), _chat_id(context), state.LOST_CLIENTS_SCREEN)
        await context.send_text(LOST_CLIENTS_LOAD_ERROR_TEXT, keyboard=lost_clients_result_keyboard(can_broadcast=False))
        return

    recipients = lost_clients_to_broadcast_recipients(result.clients)
    logger.info("lost_clients_ui_loaded total=%s mappable_count=%s", result.total, len(recipients))
    state.set_state_data_value(_user_id(context), _chat_id(context), _LOST_CLIENTS_RESULT_KEY, result)
    state.set_state_data_value(_user_id(context), _chat_id(context), _LOST_CLIENTS_RECIPIENTS_KEY, recipients)
    state.set_current_screen(_user_id(context), _chat_id(context), state.LOST_CLIENTS_SCREEN)
    await context.send_text(format_lost_clients_summary(result), keyboard=lost_clients_result_keyboard(can_broadcast=bool(recipients)))


def _stored_result(context: RouterContext) -> LostClientsResult | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _LOST_CLIENTS_RESULT_KEY)
    return value if isinstance(value, LostClientsResult) else None


def _can_open_lost_clients(context: RouterContext) -> bool:
    return can_view_broadcasts(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"
    return _staff_repository().get_highest_role(platform_user_id, platform=PLATFORM_MAX)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback(context, BROADCAST_NO_ACCESS_TEXT)
    await context.send_text(BROADCAST_NO_ACCESS_TEXT)


async def _answer_callback(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


def _clear_lost_clients_state(context: RouterContext) -> None:
    for key in (_LOST_CLIENTS_RESULT_KEY, _LOST_CLIENTS_RECIPIENTS_KEY):
        state.set_state_data_value(_user_id(context), _chat_id(context), key, None)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _users_repository() -> UsersRepository:
    return UsersRepository(_database_path())


def _staff_repository() -> StaffRolesRepository:
    return StaffRolesRepository(_database_path())


def _yclients_settings_repository() -> YClientsSettingsRepository:
    return YClientsSettingsRepository(_database_path())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
