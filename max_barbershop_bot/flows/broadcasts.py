"""One-time broadcast flow handlers for the MAX bot."""

from __future__ import annotations

import logging
from os import getenv
from uuid import uuid4

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import DEFAULT_ACTION_LOCK_TTL_SECONDS, acquire_action_lock, is_action_locked, release_action_lock
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_broadcasts
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.broadcasts import (
    ALL_USERS_AUDIENCE,
    SELF_AUDIENCE,
    BroadcastAudience,
    BroadcastRecipient,
    build_broadcast_confirm_text,
    build_broadcast_preview,
    build_recipients_from_users,
    format_broadcast_report,
    BroadcastDraft,
    BroadcastSendReport,
    extract_broadcast_attachment,
    send_one_time_broadcast,
    validate_broadcast_text,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_BROADCASTS_PAYLOAD,
    BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD,
    BROADCAST_AUDIENCE_SELF_PAYLOAD,
    BROADCAST_BACK_PAYLOAD,
    BROADCAST_CONFIRM_SEND_PAYLOAD,
    BROADCAST_HOME_PAYLOAD,
    BROADCAST_NEW_PAYLOAD,
    BROADCAST_ONE_TIME_START_PAYLOAD,
    BROADCAST_PREVIEW_EDIT_PAYLOAD,
    BROADCAST_PREVIEW_NEXT_PAYLOAD,
    BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD,
    BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD,
    broadcast_audience_keyboard,
    broadcast_confirm_keyboard,
    broadcast_menu_keyboard,
    broadcast_preview_keyboard,
    broadcast_report_keyboard,
    broadcast_text_keyboard,
)
from max_barbershop_bot.ui.texts import (
    BROADCAST_ALREADY_SENDING_TEXT,
    BROADCAST_MENU_TEXT,
    BROADCAST_NO_ACCESS_TEXT,
    BROADCAST_NO_RECIPIENTS_TEXT,
    BROADCAST_SENDING_TEXT,
    BROADCAST_TEXT_INPUT_TEXT,
)

_BROADCAST_DRAFT_KEY = "broadcast_draft"
_BROADCAST_TEXT_KEY = "broadcast_text"
_BROADCAST_AUDIENCE_KEY = "broadcast_audience"
_BROADCAST_AUDIENCE_LABEL_KEY = "broadcast_audience_label"
_BROADCAST_RECIPIENT_COUNT_KEY = "broadcast_recipient_count"
_BROADCAST_RECIPIENTS_KEY = "broadcast_recipients"
_BROADCAST_IN_PROGRESS_KEY = "broadcast_in_progress"
_BROADCAST_SEND_TOKEN_KEY = "broadcast_send_token"
_BROADCAST_SKIPPED_DISABLED_KEY = "broadcast_skipped_disabled"
_BROADCAST_SKIPPED_MISSING_KEY = "broadcast_skipped_missing"
_BROADCAST_RETURN_SCREEN_KEY = "broadcast_return_screen"
_BROADCAST_ATTACHMENT_TYPE_KEY = "broadcast_attachment_type"
_BROADCAST_ATTACHMENT_KEY = "broadcast_attachment"

logger = logging.getLogger(__name__)
_BROADCAST_SEND_LOCK_KEY = "broadcast:send"


def register_broadcast_routes(router: Router) -> None:
    """Register one-time broadcast callbacks and text steps."""

    router.on_callback(ADMIN_BROADCASTS_PAYLOAD, handle_broadcast_menu)
    router.on_callback(BROADCAST_ONE_TIME_START_PAYLOAD, handle_one_time_start)
    router.on_callback(BROADCAST_PREVIEW_NEXT_PAYLOAD, handle_preview_next)
    router.on_callback(BROADCAST_PREVIEW_EDIT_PAYLOAD, handle_preview_edit)
    router.on_callback(BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD, handle_preview_remove_attachment)
    router.on_callback(BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD, handle_preview_edit_attachment)
    router.on_callback(BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD, handle_audience_all_users)
    router.on_callback(BROADCAST_AUDIENCE_SELF_PAYLOAD, handle_audience_self)
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
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_MENU_SCREEN)
    _clear_broadcast_state(context)
    await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())


async def handle_one_time_start(context: RouterContext) -> None:
    """Ask admin to enter one-time broadcast text."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context)
    _clear_broadcast_state(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())


async def handle_text_input(context: RouterContext) -> None:
    """Validate entered text/media according to the current broadcast screen and show preview."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return

    attachment = extract_broadcast_attachment(context.event.attachments)
    incoming_text = (context.event.text or "").strip()
    if attachment is not None:
        _save_broadcast_attachment(context, attachment.attachment_type, attachment.attachment)
        logger.info(
            "MAX broadcast diagnostic: media_input platform_user_id_present=%s role=%s screen_id=%s draft_text_present=%s attachment_type=%s attachment_present=%s",
            bool(context.event.platform_user_id),
            _actor_role(context),
            state.get_current_screen(_user_id(context), _chat_id(context)),
            bool(incoming_text or _broadcast_text(context)),
            attachment.attachment_type,
            True,
        )
    elif context.event.attachments and not incoming_text:
        await context.send_text("Этот тип вложения пока не поддерживается в MAX 🙏", keyboard=broadcast_text_keyboard())
        return

    validation = validate_broadcast_text(incoming_text or _broadcast_text(context))
    if not validation.ok:
        await context.send_text(validation.error or "Текст рассылки не может быть пустым 🙏", keyboard=broadcast_text_keyboard())
        return

    _save_broadcast_text(context, validation.text)
    if _broadcast_recipients(context):
        await show_segment_broadcast_confirm(context)
        return
    await _show_preview(context)


async def handle_preview_next(context: RouterContext) -> None:
    """Move from preview to audience selection."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    text = _broadcast_text(context)
    if not text:
        await _open_text_step(context)
        return
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN)
    await context.send_text("✉️ Разовая рассылка\n\nВыберите аудиторию 👇", keyboard=broadcast_audience_keyboard())


async def handle_preview_edit(context: RouterContext) -> None:
    """Return to text editing step."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())


async def handle_preview_edit_attachment(context: RouterContext) -> None:
    """Ask admin to send/replace media for the draft."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    await context.send_text("Отправьте фото, GIF или видео для рассылки. Можно добавить подпись текстом 👇", keyboard=broadcast_text_keyboard())


async def handle_preview_remove_attachment(context: RouterContext) -> None:
    """Remove selected media from the broadcast draft and return to preview."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_TYPE_KEY, None)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_KEY, None)
    _save_draft(context)
    await _show_preview(context)


async def handle_audience_all_users(context: RouterContext) -> None:
    """Select all local registered users with enabled notifications."""

    await _select_audience(context, ALL_USERS_AUDIENCE)


async def handle_audience_self(context: RouterContext) -> None:
    """Select current admin as a test recipient."""

    await _select_audience(context, SELF_AUDIENCE)


async def _select_audience(context: RouterContext, audience: BroadcastAudience) -> None:
    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    text = _broadcast_text(context)
    if not text:
        await _open_text_step(context)
        return

    recipients, skipped_disabled, skipped_missing = _resolve_audience_recipients(context, audience)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY, audience.key)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY, audience.label)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENT_COUNT_KEY, len(recipients))
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENTS_KEY, recipients)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SKIPPED_DISABLED_KEY, skipped_disabled)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SKIPPED_MISSING_KEY, skipped_missing)

    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_CONFIRM_SCREEN)
    if not recipients:
        await context.send_text(BROADCAST_NO_RECIPIENTS_TEXT, keyboard=broadcast_confirm_keyboard(can_send=False))
        return
    await context.send_text(
        build_broadcast_confirm_text(
            audience_label=audience.label,
            recipient_count=len(recipients),
            text=text,
            attachment_type=_broadcast_attachment_type(context),
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
        build_broadcast_confirm_text(audience_label=label, recipient_count=len(recipients), text=text, attachment_type=_broadcast_attachment_type(context)),
        keyboard=broadcast_confirm_keyboard(can_send=True),
    )


async def handle_confirm_send(context: RouterContext) -> None:
    """Send the one-time broadcast and show final report."""

    if not _can_open_broadcasts(context):
        await _send_no_access(context)
        return
    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
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

    send_token = uuid4().hex
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY, True)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SEND_TOKEN_KEY, send_token)
    state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_SENDING_SCREEN)
    await _answer_callback_if_needed(context)
    await context.send_text(BROADCAST_SENDING_TEXT)

    if is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY, False)
        await _send_sending_in_progress(context)
        return

    if not acquire_action_lock(_BROADCAST_SEND_LOCK_KEY, ttl_seconds=DEFAULT_ACTION_LOCK_TTL_SECONDS):
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY, False)
        logger.info(
            "MAX antiflood/action lock diagnostic: event_type=%s platform_user_id_present=%s chat_id_present=%s action=%s lock_key_type=%s lock_acquired=%s lock_active=%s ttl_seconds=%s payload_present=%s",
            context.event.update_type, bool(_user_id(context)), bool(_chat_id(context)), "broadcast_send", "broadcast:send", False, True, DEFAULT_ACTION_LOCK_TTL_SECONDS, bool(context.event.callback_payload),
        )
        await _send_sending_in_progress(context)
        return

    try:
        if state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SEND_TOKEN_KEY) != send_token:
            await context.send_text("⚠️ Эта рассылка уже отправляется или была отправлена.")
            release_action_lock(_BROADCAST_SEND_LOCK_KEY)
            return
        audience = _broadcast_audience(context)
        report = await send_one_time_broadcast(
            sender=context.sender,
            users_repository=_users_repository(),
            database_path=_database_path(),
            text=text,
            recipients=recipients,
            attachment=_broadcast_attachment(context),
            audience=audience,
            actor_platform_user_id=context.event.platform_user_id,
        )
    except Exception as exc:
        logger.warning(
            "MAX broadcast diagnostic: send_failed actor_platform_user_id_present=%s audience_type=%s recipients_count=%s lock_active=%s error_type=%s",
            bool(context.event.platform_user_id),
            _broadcast_audience(context).key,
            len(recipients),
            is_action_locked(_BROADCAST_SEND_LOCK_KEY),
            type(exc).__name__,
            exc_info=True,
        )
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_IN_PROGRESS_KEY, False)
        release_action_lock(_BROADCAST_SEND_LOCK_KEY)
        await context.send_text("⚠️ Не удалось завершить рассылку. Попробуйте позже.", keyboard=broadcast_report_keyboard())
        return

    report = BroadcastSendReport(
        total=report.total,
        sent=report.sent,
        failed=report.failed,
        blocked=report.blocked,
        stopped=report.stopped,
        skipped_notifications_disabled=_state_int(context, _BROADCAST_SKIPPED_DISABLED_KEY),
        skipped_missing_recipient_id=_state_int(context, _BROADCAST_SKIPPED_MISSING_KEY),
        rate_limited=report.rate_limited,
        broadcast_id=report.broadcast_id,
    )
    _clear_broadcast_state(context)
    state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_REPORT_SCREEN)
    await context.send_text(format_broadcast_report(report), keyboard=broadcast_report_keyboard())


async def handle_broadcast_back(context: RouterContext) -> None:
    """Handle Back inside broadcast wizard."""

    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context)
    current = state.get_current_screen(_user_id(context), _chat_id(context))
    if current == state.BROADCAST_ONE_TIME_TEXT_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_MENU_SCREEN)
        await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())
    elif current == state.BROADCAST_ONE_TIME_PREVIEW_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_TEXT_SCREEN)
        await context.send_text(BROADCAST_TEXT_INPUT_TEXT, keyboard=broadcast_text_keyboard())
    elif current == state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
        await _show_preview(context, push_current=False)
    elif current == state.BROADCAST_ONE_TIME_CONFIRM_SCREEN:
        return_screen = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RETURN_SCREEN_KEY)
        if return_screen == state.CLIENT_SEGMENT_RESULT_SCREEN:
            state.set_current_screen(_user_id(context), _chat_id(context), state.CLIENT_SEGMENT_RESULT_SCREEN)
            await context.send_text("Вернитесь к сегменту через меню рассылки 🎯", keyboard=broadcast_menu_keyboard())
        elif return_screen == state.LOST_CLIENTS_SCREEN:
            state.set_current_screen(_user_id(context), _chat_id(context), state.LOST_CLIENTS_SCREEN)
            await context.send_text("Вернитесь к потерянным клиентам через меню сегментов 😔", keyboard=broadcast_menu_keyboard())
        else:
            state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_AUDIENCE_SCREEN)
            await context.send_text("✉️ Разовая рассылка\n\nВыберите аудиторию 👇", keyboard=broadcast_audience_keyboard())
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_MENU_SCREEN)
        await context.send_text(BROADCAST_MENU_TEXT, keyboard=broadcast_menu_keyboard())


async def handle_broadcast_home(context: RouterContext) -> None:
    """Return to main menu and clear unsent broadcast state."""

    if _is_sending(context) or is_action_locked(_BROADCAST_SEND_LOCK_KEY):
        await _send_sending_in_progress(context)
        return
    await _answer_callback_if_needed(context)
    _clear_broadcast_state(context)
    await show_home(context)


async def _show_preview(context: RouterContext, *, push_current: bool = True) -> None:
    if push_current:
        _push_current_screen(context, state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
    else:
        state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
    await context.send_text(
        build_broadcast_preview(_broadcast_text(context) or "", _broadcast_attachment_type(context)),
        keyboard=broadcast_preview_keyboard(has_attachment=_broadcast_attachment(context) is not None),
        attachments=[_broadcast_attachment(context)] if _broadcast_attachment(context) else None,
    )


def _save_broadcast_text(context: RouterContext, text: str) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_TEXT_KEY, text)
    _save_draft(context)


def _save_broadcast_attachment(context: RouterContext, attachment_type: str, attachment: dict[str, object]) -> None:
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_TYPE_KEY, attachment_type)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_KEY, attachment)
    _save_draft(context)


def _save_draft(context: RouterContext) -> None:
    audience = _broadcast_audience(context)
    state.set_state_data_value(
        _user_id(context),
        _chat_id(context),
        _BROADCAST_DRAFT_KEY,
        BroadcastDraft(
            text=_broadcast_text(context),
            attachment_type=_broadcast_attachment_type(context),
            attachment=_broadcast_attachment(context),
            audience_key=audience.key,
            audience_label=audience.label,
        ),
    )


def _resolve_audience_recipients(
    context: RouterContext,
    audience: BroadcastAudience,
) -> tuple[list[BroadcastRecipient], int, int]:
    repo = _users_repository()
    if audience.key == SELF_AUDIENCE.key:
        current = repo.find_by_platform_user_id(str(_user_id(context) or ""), platform=PLATFORM_MAX) if _user_id(context) else None
        candidates = [current] if current else []
    else:
        candidates = repo.list_users_for_broadcast_audience(platform=PLATFORM_MAX)

    skipped_disabled = 0
    skipped_missing = 0
    sendable = []
    for user in candidates:
        if user is None:
            continue
        if not user.notifications_enabled:
            skipped_disabled += 1
            continue
        if not (user.max_user_id or user.chat_id):
            skipped_missing += 1
            continue
        sendable.append(user)
    recipients = build_recipients_from_users(sendable)
    logger.info(
        "MAX broadcast diagnostic: audience_resolved actor_platform_user_id_present=%s audience=%s total_candidates=%s recipients_count=%s skipped_notifications_disabled=%s skipped_blocked_stopped=%s",
        bool(context.event.platform_user_id),
        audience.key,
        len(candidates),
        len(recipients),
        skipped_disabled,
        0,
    )
    return recipients, skipped_disabled, skipped_missing


def _state_int(context: RouterContext, key: str) -> int:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


async def _answer_callback_if_needed(context: RouterContext, notification: str | None = None) -> None:
    if context.event.callback_id:
        await context.answer_callback()


def _broadcast_text(context: RouterContext) -> str | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_TEXT_KEY)
    return value if isinstance(value, str) and value.strip() else None


def _broadcast_attachment_type(context: RouterContext) -> str | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_TYPE_KEY)
    return value if isinstance(value, str) and value in {"photo", "gif", "video"} else None


def _broadcast_attachment(context: RouterContext) -> dict[str, object] | None:
    value = state.get_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ATTACHMENT_KEY)
    return value if isinstance(value, dict) else None


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
    state.clear_state_data(_user_id(context), _chat_id(context))


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
