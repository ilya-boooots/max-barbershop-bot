"""One-time broadcast flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_broadcasts
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.broadcasts import (
    ALL_USERS_AUDIENCE,
    BroadcastAudience,
    BroadcastRecipient,
    build_broadcast_confirm_text,
    build_broadcast_preview,
    format_broadcast_report,
    get_all_registered_recipients,
    send_one_time_broadcast,
    validate_broadcast_text,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_BROADCASTS_PAYLOAD,
    BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD,
    BROADCAST_BACK_PAYLOAD,
    BROADCAST_CONFIRM_SEND_PAYLOAD,
    BROADCAST_HOME_PAYLOAD,
    BROADCAST_NEW_PAYLOAD,
    BROADCAST_ONE_TIME_START_PAYLOAD,
    BROADCAST_PREVIEW_EDIT_PAYLOAD,
    BROADCAST_PREVIEW_NEXT_PAYLOAD,
    broadcast_audience_keyboard,
    broadcast_confirm_keyboard,
    broadcast_menu_keyboard,
    broadcast_preview_keyboard,
    broadcast_report_keyboard,
    broadcast_text_keyboard,
    client_segment_result_keyboard,
    lost_clients_result_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BROADCAST_ALREADY_SENDING_TEXT,
    BROADCAST_MENU_TEXT,
    BROADCAST_NO_ACCESS_TEXT,
    BROADCAST_NO_RECIPIENTS_TEXT,
    BROADCAST_SENDING_TEXT,
    BROADCAST_TEXT_INPUT_TEXT,
)

_BROADCAST_TEXT_KEY = "broadcast_text"
_BROADCAST_AUDIENCE_KEY = "broadcast_audience"
_BROADCAST_AUDIENCE_LABEL_KEY = "broadcast_audience_label"
_BROADCAST_RECIPIENT_COUNT_KEY = "broadcast_recipient_count"
_BROADCAST_RECIPIENTS_KEY = "broadcast_recipients"
_BROADCAST_IN_PROGRESS_KEY = "broadcast_in_progress"
_BROADCAST_RETURN_SCREEN_KEY = "broadcast_return_screen"
_BROADCAST_STATE_KEYS = (
    _BROADCAST_TEXT_KEY,
    _BROADCAST_AUDIENCE_KEY,
    _BROADCAST_AUDIENCE_LABEL_KEY,
    _BROADCAST_RECIPIENT_COUNT_KEY,
    _BROADCAST_RECIPIENTS_KEY,
    _BROADCAST_IN_PROGRESS_KEY,
    _BROADCAST_RETURN_SCREEN_KEY,
)


def register_broadcast_routes(router: Router) -> None:
    """Register one-time broadcast callbacks and text steps."""

    router.on_callback(ADMIN_BROADCASTS_PAYLOAD, handle_broadcast_menu)
    router.on_callback(BROADCAST_ONE_TIME_START_PAYLOAD, handle_one_time_start)
    router.on_callback(BROADCAST_PREVIEW_NEXT_PAYLOAD, handle_preview_next)
    router.on_callback(BROADCAST_PREVIEW_EDIT_PAYLOAD, handle_preview_edit)
    router.on_callback(BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD, handle_audience_all_users)
    router.on_callback(BROADCAST_CONFIRM_SEND_PAYLOAD, handle_confirm_send)
    router.on_callback(BROADCAST_NEW_PAYLOAD, handle_one_time_start)
    router.on_callback(BROADCAST_BACK_PAYLOAD, handle_broadcast_back)
    router.on_callback(BROADCAST_HOME_PAYLOAD, handle_broadcast_home)
    router.on_screen_text(state.BROADCAST_ONE_TIME_TEXT_SCREEN, handle_text_input)


async def handle_broadcast_menu(context: RouterContext) -> None:
    """Open broadcast menu for allowed roles only."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем рассылку 📣")
    _push_current_screen(context, state.BROADCAST_MENU_SCREEN)
    _clear_broadcast_state(context)
    await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())


async def handle_one_time_start(context: RouterContext) -> None:
    """Ask admin to enter one-time broadcast text."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context, "Введите текст рассылки ✉️")
    _clear_broadcast_state(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())


async def handle_text_input(context: RouterContext) -> None:
    """Validate entered text and show preview."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    validation = validate_broadcast_text(context.event.text)
    if not validation.ok:
        await context.send_text(validation.error or "Текст рассылки не может быть пустым 🙏", keyboard=broadcast_text_keyboard())
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_TEXT_KEY, validation.text)
    if _broadcast_recipients(context):
        await show_segment_broadcast_confirm(context)
        return
    _push_current_screen(context, state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
    await context.send_text(build_broadcast_preview(validation.text), keyboard=broadcast_preview_keyboard())


async def handle_preview_next(context: RouterContext) -> None:
    """Move from preview to audience selection."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    text = _broadcast_text(context)
    if not text:
        await _open_text_step(context)
        return
    await _answer_callback_if_needed(context, "Выберите аудиторию 👥")
    _push_current_screen(context, state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN)
    await context.send_text("✉️ Разовая рассылка\n\nВыберите аудиторию 👇", keyboard=broadcast_audience_keyboard())


async def handle_preview_edit(context: RouterContext) -> None:
    """Return to text editing step."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context, "Изменим текст ✏️")
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())


async def handle_audience_all_users(context: RouterContext) -> None:
    """Select all local registered users with enabled notifications."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    text = _broadcast_text(context)
    if not text:
        await _open_text_step(context)
        return

    recipients = get_all_registered_recipients(_users_repository())
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY, ALL_USERS_AUDIENCE.key)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY, ALL_USERS_AUDIENCE.label)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENT_COUNT_KEY, len(recipients))
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENTS_KEY, recipients)

    await _answer_callback_if_needed(context, "Аудитория выбрана ✅")
    _push_current_screen(context, state.BROADCAST_ONE_TIME_CONFIRM_SCREEN)
    if not recipients:
        await context.send_text(BROADCAST_NO_RECIPIENTS_TEXT, keyboard=broadcast_confirm_keyboard(can_send=False))
        return
    await context.send_text(
        build_broadcast_confirm_text(
            audience_label=ALL_USERS_AUDIENCE.label,
            recipient_count=len(recipients),
            text=text,
        ),
        keyboard=broadcast_confirm_keyboard(can_send=True),
    )


async def open_segment_broadcast_text(
    context: RouterContext,
    *,
    audience_key: str,
    audience_label: str,
    recipients: list[BroadcastRecipient],
    return_screen: str = state.CLIENT_SEGMENT_RESULT_SCREEN,
) -> None:
    """Start one-time broadcast wizard with a prepared segment audience."""

    _clear_broadcast_state(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY, audience_key)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY, audience_label)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENT_COUNT_KEY, len(recipients))
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENTS_KEY, recipients)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RETURN_SCREEN_KEY, return_screen)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(
        f"📣 Рассылка по сегменту\n\nАудитория: {audience_label}\nПолучателей в MAX: {len(recipients)}\n\nВведите текст рассылки 👇",
        keyboard=broadcast_text_keyboard(),
    )


async def show_segment_broadcast_confirm(context: RouterContext) -> None:
    """Show confirmation when broadcast audience was prepared by segment flow."""

    text = _broadcast_text(context)
    recipients = _broadcast_recipients(context)
    if not text:
        await _open_text_step(context)
        return
    label = _broadcast_audience(context).label
    _push_current_screen(context, state.BROADCAST_ONE_TIME_CONFIRM_SCREEN)
    if not recipients:
        await context.send_text(BROADCAST_NO_RECIPIENTS_TEXT, keyboard=broadcast_confirm_keyboard(can_send=False))
        return
    await context.send_text(
        build_broadcast_confirm_text(audience_label=label, recipient_count=len(recipients), text=text),
        keyboard=broadcast_confirm_keyboard(can_send=True),
    )


async def handle_confirm_send(context: RouterContext) -> None:
    """Send the one-time broadcast and show final report."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context):
        await _send_sending_in_progress(context)
        return

    text = _broadcast_text(context)
    recipients = _broadcast_recipients(context)
    if not text:
        await _open_text_step(context)
        return
    if not recipients:
        await _answer_callback_if_needed(context, BROADCAST_NO_RECIPIENTS_TEXT)
        await context.send_text(BROADCAST_NO_RECIPIENTS_TEXT, keyboard=broadcast_confirm_keyboard(can_send=False))
        return

    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY, True)
    state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_SENDING_SCREEN)
    await _answer_callback_if_needed(context, "Отправляем рассылку 🚀")
    await context.send_text(BROADCAST_SENDING_TEXT)

    audience = _broadcast_audience(context)
    report = await send_one_time_broadcast(
        sender=context.sender,
        users_repository=_users_repository(),
        database_path=_database_path(),
        text=text,
        recipients=recipients,
        audience=audience,
        actor_platform_user_id=context.event.platform_user_id,
    )
    _clear_broadcast_state(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_REPORT_SCREEN)
    await context.send_text(format_broadcast_report(report), keyboard=broadcast_report_keyboard())


async def handle_broadcast_back(context: RouterContext) -> None:
    """Handle Back inside broadcast wizard."""

    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context, "Возвращаемся назад ⬅️")
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.BROADCAST_ONE_TIME_TEXT_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_MENU_SCREEN)
        await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())
    elif current == state.BROADCAST_ONE_TIME_PREVIEW_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_TEXT_SCREEN)
        await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())
    elif current == state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
        await context.send_text(build_broadcast_preview(_broadcast_text(context) or ""), keyboard=broadcast_preview_keyboard())
    elif current == state.BROADCAST_ONE_TIME_CONFIRM_SCREEN:
        return_screen = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RETURN_SCREEN_KEY)
        if return_screen == state.CLIENT_SEGMENT_RESULT_SCREEN:
            state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENT_RESULT_SCREEN)
            await context.send_text(
                "Вернулись к выбранному сегменту 🎯\n\nМожно обновить расчёт или снова запустить рассылку.",
                keyboard=client_segment_result_keyboard(can_broadcast=True),
            )
        elif return_screen == state.LOST_CLIENTS_SCREEN:
            state.set_current_screen(_user_id(context), _chat_id(context), state.LOST_CLIENTS_SCREEN)
            await context.send_text(
                "Вернулись к потерянным клиентам 😔\n\nМожно обновить расчёт или снова запустить рассылку.",
                keyboard=lost_clients_result_keyboard(can_broadcast=True),
            )
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN)
            await context.send_text("✉️ Разовая рассылка\n\nВыберите аудиторию 👇", keyboard=broadcast_audience_keyboard())
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_MENU_SCREEN)
        await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())


async def handle_broadcast_home(context: RouterContext) -> None:
    """Return to main menu and clear unsent broadcast state."""

    if _is_sending(context):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context, "Открываем главное меню 🏠")
    await show_home(context)


def _can_open_broadcasts(context: RouterContext) -> bool:
    return can_view_broadcasts(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return "user"
    return _staff_repository().get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current != screen_id:
        state.push_screen(_user_id(context), _chat_id(context), current)
    state.set_current_screen(_user_id(context), _chat_id(context), screen_id)


async def _open_text_step(context: RouterContext) -> None:
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, BROADCAST_NO_ACCESS_TEXT)
    await context.send_text(BROADCAST_NO_ACCESS_TEXT)


async def _send_sending_in_progress(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, BROADCAST_ALREADY_SENDING_TEXT)
    await context.send_text(BROADCAST_ALREADY_SENDING_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


def _broadcast_text(context: RouterContext) -> str | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_TEXT_KEY)
    return value if isinstance(value, str) and value.strip() else None


def _broadcast_audience(context: RouterContext) -> BroadcastAudience:
    key = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY)
    label = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY)
    if isinstance(key, str) and isinstance(label, str) and key and label:
        return BroadcastAudience(key=key, label=label)
    return ALL_USERS_AUDIENCE


def _broadcast_recipients(context: RouterContext) -> list[BroadcastRecipient]:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENTS_KEY)
    return value if isinstance(value, list) else []


def _is_sending(context: RouterContext) -> bool:
    return bool(state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY))


def _clear_broadcast_state(context: RouterContext) -> None:
    for key in _BROADCAST_STATE_KEYS:
        state.set_state_data_value(_user_id(context), _chat_id(context), key, None)


def _user_id(context: RouterContext) -> str | None:
    return context.event.platform_user_id


def _chat_id(context: RouterContext) -> str | None:
    return context.event.chat_id


def _users_repository() -> UsersRepository:
    return UsersRepository(_database_path())


def _staff_repository() -> StaffRolesRepository:
    return StaffRolesRepository(_database_path())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
