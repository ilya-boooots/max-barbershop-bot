"""Client segments UI flow for the MAX bot."""

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
from max_barbershop_bot.services.broadcasts import BroadcastRecipient
from max_barbershop_bot.services.client_segments import (
    ClientSegmentMember,
    ClientSegmentResult,
    ClientSegmentService,
    ClientSegmentsLoadError,
    ClientSegmentsNotConfiguredError,
    format_segment_summary,
    format_segments_overview,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    BROADCAST_SEGMENTS_PAYLOAD,
    SEGMENTS_ALL_CLIENTS_PAYLOAD,
    SEGMENTS_ACTIVE_7_PAYLOAD,
    SEGMENTS_ACTIVE_30_PAYLOAD,
    SEGMENTS_ACTIVE_90_PAYLOAD,
    SEGMENTS_BACK_PAYLOAD,
    SEGMENTS_BROADCAST_PAYLOAD,
    SEGMENTS_HOME_PAYLOAD,
    broadcast_menu_keyboard,
    client_segment_result_keyboard,
    client_segments_menu_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BROADCAST_MENU_TEXT,
    BROADCAST_NO_ACCESS_TEXT,
    CLIENT_SEGMENTS_BROADCAST_LIMIT_TEXT,
    CLIENT_SEGMENTS_LOAD_ERROR_TEXT,
    YCLIENTS_NOT_CONFIGURED_TEXT,
)

logger = logging.getLogger(__name__)

_SELECTED_SEGMENT_PAYLOAD_KEY = "selected_segment_payload"
_SELECTED_SEGMENT_RESULT_KEY = "selected_segment_result"
_SELECTED_SEGMENT_RECIPIENTS_KEY = "selected_segment_recipients"

_SEGMENT_CALLBACKS = {
    SEGMENTS_ALL_CLIENTS_PAYLOAD,
    SEGMENTS_ACTIVE_7_PAYLOAD,
    SEGMENTS_ACTIVE_30_PAYLOAD,
    SEGMENTS_ACTIVE_90_PAYLOAD,
}


def register_client_segment_routes(router: Router) -> None:
    """Register segment menu and result callbacks."""

    router.on_callback(BROADCAST_SEGMENTS_PAYLOAD, handle_segments_menu)
    for payload in _SEGMENT_CALLBACKS:
        router.on_callback(payload, handle_segment_selected)
    router.on_callback(SEGMENTS_BROADCAST_PAYLOAD, handle_segment_broadcast)
    router.on_callback(SEGMENTS_BACK_PAYLOAD, handle_segments_back)
    router.on_callback(SEGMENTS_HOME_PAYLOAD, handle_segments_home)


async def handle_segments_menu(context: RouterContext) -> None:
    """Open client segments menu."""

    if not _can_open_segments(context):
        await _send_no_access(context)
        return
    await _answer_callback(context)
    _clear_segment_state(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENTS_MENU_SCREEN)
    await _show_segments_overview(context)


async def handle_segment_selected(context: RouterContext) -> None:
    """Calculate and show one selected segment."""

    if not _can_open_segments(context):
        await _send_no_access(context)
        return
    payload = context.event.callback_payload
    if payload not in _SEGMENT_CALLBACKS:
        return
    await _show_segment(context, payload)


async def handle_segment_broadcast(context: RouterContext) -> None:
    """Start broadcast wizard for mapped MAX users in the selected segment."""

    if not _can_open_segments(context):
        await _send_no_access(context)
        return
    result = _stored_segment_result(context)
    recipients = _stored_segment_recipients(context)
    if result is None:
        await handle_segments_menu(context)
        return
    await _answer_callback(context)
    if result.count > len(recipients):
        await context.send_text(
            f"{CLIENT_SEGMENTS_BROADCAST_LIMIT_TEXT}\n\nДоступно для рассылки: {len(recipients)} из {result.count}.",
            keyboard=client_segment_result_keyboard(can_broadcast=bool(recipients)),
        )
        if not recipients:
            return
    await open_segment_broadcast_text(
        context,
        audience_key=f"segment:{result.segment_type}",
        audience_label=f"{result.title} · YClients: {result.count}",
        recipients=recipients,
    )


async def handle_segments_back(context: RouterContext) -> None:
    """Navigate back from segments screens."""

    await _answer_callback(context)
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.CLIENT_SEGMENT_RESULT_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENTS_MENU_SCREEN)
        await _show_segments_overview(context)
        return
    state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_MENU_SCREEN)
    await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())


async def handle_segments_home(context: RouterContext) -> None:
    """Return to role-based main menu."""

    await _answer_callback(context)
    _clear_segment_state(context)
    await show_home(context)


async def _show_segments_overview(context: RouterContext) -> None:
    try:
        await context.send_text("⏳ Считаю сегменты по YClients...")
        overview = await ClientSegmentService(_yclients_settings_repository()).get_core_segments_overview()
    except ClientSegmentsNotConfiguredError:
        await context.send_text(YCLIENTS_NOT_CONFIGURED_TEXT, keyboard=client_segments_menu_keyboard())
        return
    except ClientSegmentsLoadError:
        await context.send_text(CLIENT_SEGMENTS_LOAD_ERROR_TEXT, keyboard=client_segments_menu_keyboard())
        return
    await context.send_text(format_segments_overview(overview), keyboard=client_segments_menu_keyboard())

async def _show_segment(context: RouterContext, payload: str, *, notification: str | None = None) -> None:
    await _answer_callback(context, notification)
    try:
        result = await _load_segment(payload)
    except ClientSegmentsNotConfiguredError:
        state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENTS_MENU_SCREEN)
        await context.send_text(YCLIENTS_NOT_CONFIGURED_TEXT, keyboard=client_segments_menu_keyboard())
        return
    except ClientSegmentsLoadError:
        state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENTS_MENU_SCREEN)
        await context.send_text(CLIENT_SEGMENTS_LOAD_ERROR_TEXT, keyboard=client_segments_menu_keyboard())
        return

    recipients = _map_members_to_recipients(result.members)
    logger.info(
        "MAX client segments diagnostic: segment=%s yclients_clients_count=%s yclients_records_count=%s deduped_count=%s branch_timezone=%s duration_ms=%s",
        result.segment_type,
        result.diagnostics.get("clients_count", result.count),
        result.diagnostics.get("records_count", 0),
        result.count,
        result.branch_timezone,
        result.diagnostics.get("duration_ms", 0),
    )
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SEGMENT_PAYLOAD_KEY, payload)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SEGMENT_RESULT_KEY, result)
    state.set_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SEGMENT_RECIPIENTS_KEY, recipients)
    state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENT_RESULT_SCREEN)

    text = format_segment_summary(result)
    await context.send_text(text, keyboard=client_segment_result_keyboard(can_broadcast=True))


async def _load_segment(payload: str) -> ClientSegmentResult:
    service = ClientSegmentService(_yclients_settings_repository())
    if payload == SEGMENTS_ALL_CLIENTS_PAYLOAD:
        return await service.get_all_clients()
    if payload == SEGMENTS_ACTIVE_7_PAYLOAD:
        return await service.get_active_clients(7)
    if payload == SEGMENTS_ACTIVE_30_PAYLOAD:
        return await service.get_active_clients(30)
    if payload == SEGMENTS_ACTIVE_90_PAYLOAD:
        return await service.get_active_clients(90)
    raise ValueError(f"Unsupported segment payload: {payload}")


def _map_members_to_recipients(members: list[ClientSegmentMember]) -> list[BroadcastRecipient]:
    users = _users_repository().list_broadcast_recipients(platform=PLATFORM_MAX, notifications_enabled=True)
    by_client_id = {str(user.yclients_client_id).strip(): user for user in users if user.yclients_client_id}
    by_phone = {_normalize_phone(user.phone): user for user in users if _normalize_phone(user.phone)}
    recipients: dict[str, BroadcastRecipient] = {}
    for member in members:
        user = None
        if member.yclients_client_id:
            user = by_client_id.get(str(member.yclients_client_id).strip())
        if user is None:
            user = by_phone.get(_normalize_phone(member.phone))
        if user is None:
            continue
        recipients[user.platform_user_id] = BroadcastRecipient(
            platform_user_id=user.platform_user_id,
            max_user_id=user.max_user_id,
            chat_id=user.chat_id,
            display_name=user.display_name or user.first_name,
        )
    return list(recipients.values())


def _stored_segment_result(context: RouterContext) -> ClientSegmentResult | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SEGMENT_RESULT_KEY)
    return value if isinstance(value, ClientSegmentResult) else None


def _stored_segment_recipients(context: RouterContext) -> list[BroadcastRecipient]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _SELECTED_SEGMENT_RECIPIENTS_KEY)
    return value if isinstance(value, list) else []


def _can_open_segments(context: RouterContext) -> bool:
    return can_view_broadcasts(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"
    return _staff_repository().get_highest_role(platform_user_id, platform=PLATFORM_MAX)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback(context, BROADCAST_NO_ACCESS_TEXT)
    await context.send_text(BROADCAST_NO_ACCESS_TEXT)


async def _answer_callback(context: RouterContext, notification: str | None = None) -> None:
    del notification
    if context.event.callback_id:
        await context.answer_callback()


def _clear_segment_state(context: RouterContext) -> None:
    for key in (_SELECTED_SEGMENT_PAYLOAD_KEY, _SELECTED_SEGMENT_RESULT_KEY, _SELECTED_SEGMENT_RECIPIENTS_KEY):
        state.set_state_data_value(_user_id(context), _chat_id(context), key, None)


def _normalize_phone(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())



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
