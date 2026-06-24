"""One-time broadcast flow handlers for the MAX bot."""

from __future__ import annotations

import logging
import os
from uuid import uuid4

from max_barbershop_bot.core import state
from max_barbershop_bot.core.action_locks import DEFAULT_ACTION_LOCK_TTL_SECONDS, acquire_action_lock, is_action_locked, release_action_lock
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH, ConfigError, load_config
from max_barbershop_bot.core.telegram_runtime import TelegramRuntimeStatus, build_telegram_runtime_status
from max_barbershop_bot.core.permissions import can_view_broadcasts
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, PLATFORM_TELEGRAM, UsersRepository
from max_barbershop_bot.repositories.telegram_users import TelegramUsersRepository
from max_barbershop_bot.repositories.omnichannel_broadcasts import OmnichannelBroadcastRepository
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.omnichannel_broadcasts import (
    AUDIENCE_SOURCE_YCLIENTS_ALL,
    BroadcastAttachmentPayload,
    MaxBroadcastDeliveryAdapter,
    OmnichannelBroadcastService,
    TelegramBotApiBroadcastAdapter,
    TelegramUnavailableBroadcastAdapter,
)
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials, load_active_yclients_settings
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
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
_BROADCAST_ESTIMATE_KEY = "broadcast_omnichannel_estimate"
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
    """Move from preview to final YClients audience confirmation."""

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
    await _select_audience(context, ALL_USERS_AUDIENCE)


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

    if audience.key == SELF_AUDIENCE.key:
        recipients, skipped_disabled, skipped_missing = _resolve_audience_recipients(context, audience)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY, audience.key)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY, audience.label)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENT_COUNT_KEY, len(recipients))
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_RECIPIENTS_KEY, recipients)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SKIPPED_DISABLED_KEY, skipped_disabled)
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_SKIPPED_MISSING_KEY, skipped_missing)
        await _answer_callback_if_needed(context)
        _push_current_screen(context, state.BROADCAST_ONE_TIME_CONFIRM_SCREEN)
        await context.send_text(build_broadcast_confirm_text(audience_label=audience.label, recipient_count=len(recipients), text=text, attachment_type=_broadcast_attachment_type(context)), keyboard=broadcast_confirm_keyboard(can_send=bool(recipients)))
        return

    try:
        clients = await _fetch_yclients_clients(context)
    except Exception as exc:
        logger.warning("MAX omnichannel broadcast diagnostic: audience_estimate_failed error_type=%s", type(exc).__name__, exc_info=True)
        await context.send_text("⚠️ Не удалось получить базу клиентов YClients. Проверьте настройки и попробуйте позже.", keyboard=broadcast_preview_keyboard(has_attachment=_broadcast_attachment(context) is not None))
        return
    service = _omnichannel_service(context)
    estimate = service.estimate(clients, attachment=_normalized_attachment(context))
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_KEY, AUDIENCE_SOURCE_YCLIENTS_ALL)
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_AUDIENCE_LABEL_KEY, "база YClients")
    state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ESTIMATE_KEY, estimate)
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.BROADCAST_ONE_TIME_CONFIRM_SCREEN)
    await context.send_text(_format_omnichannel_confirm(estimate), keyboard=broadcast_confirm_keyboard(can_send=estimate.total_deliveries > 0))


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
    audience = _broadcast_audience(context)
    is_omnichannel = audience.key == AUDIENCE_SOURCE_YCLIENTS_ALL
    if not recipients and not is_omnichannel:
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
        if audience.key == AUDIENCE_SOURCE_YCLIENTS_ALL:
            clients = await _fetch_yclients_clients(context)
            report = await _omnichannel_service(context).send(
                clients=clients,
                text=text,
                origin_platform=PLATFORM_MAX,
                created_by_user_id=context.event.platform_user_id,
                attachment=_normalized_attachment(context),
                sleep_seconds=0.1,
            )
            _clear_broadcast_state(context)
            state.set_current_screen(_user_id(context), _chat_id(context), state.BROADCAST_ONE_TIME_REPORT_SCREEN)
            await context.send_text(_format_omnichannel_report(report), keyboard=broadcast_report_keyboard())
            release_action_lock(_BROADCAST_SEND_LOCK_KEY)
            return
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
    release_action_lock(_BROADCAST_SEND_LOCK_KEY)
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
    preview_text = build_broadcast_preview(_broadcast_text(context) or "", _broadcast_attachment_type(context))
    try:
        clients = await _fetch_yclients_clients(context)
        estimate = _omnichannel_service(context).estimate(clients, attachment=_normalized_attachment(context))
        state.set_state_data_value(_user_id(context), _chat_id(context), _BROADCAST_ESTIMATE_KEY, estimate)
        preview_text = f"{preview_text}\n\n{_format_omnichannel_preview_estimate(estimate)}"
    except Exception as exc:
        logger.warning("MAX omnichannel broadcast diagnostic: preview_estimate_failed error_type=%s", type(exc).__name__)
        preview_text = f"{preview_text}\n\n⚠️ Оценка аудитории YClients сейчас недоступна. Перед отправкой бот попробует пересчитать аудиторию."
    await context.send_text(
        preview_text,
        keyboard=broadcast_preview_keyboard(has_attachment=_broadcast_attachment(context) is not None),
        attachments=[_broadcast_attachment(context)] if _broadcast_attachment(context) else None,
    )



async def _fetch_yclients_clients(context: RouterContext):
    settings = load_active_yclients_settings(YClientsSettingsRepository(_database_path()), operation="omnichannel_broadcast_clients")
    if not has_required_yclients_credentials(settings):
        raise RuntimeError("YClients settings are incomplete")
    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=str(settings.company_id))
        return await service.fetch_all_yclients_clients(company_id=str(settings.company_id))


def _omnichannel_service(context: RouterContext) -> OmnichannelBroadcastService:
    telegram_adapter, telegram_repo, telegram_unavailable_reason, telegram_diagnostics = _telegram_delivery_dependencies()
    return OmnichannelBroadcastService(
        users_repository=_users_repository(),
        telegram_users_repository=telegram_repo,
        attribution_repository=PlatformAttributionRepository(_database_path()),
        history_repository=OmnichannelBroadcastRepository(_database_path()),
        adapters={
            PLATFORM_MAX: MaxBroadcastDeliveryAdapter(context.sender),
            PLATFORM_TELEGRAM: telegram_adapter,
        },
        telegram_unavailable_reason=telegram_unavailable_reason,
        telegram_diagnostics=telegram_diagnostics,
    )


def _telegram_delivery_dependencies():
    try:
        config = load_config()
    except ConfigError:
        config = None
    if config is None:
        status = _telegram_status_from_config_error()
    else:
        status = build_telegram_runtime_status(config)
    _log_telegram_runtime_status(status)

    repo = TelegramUsersRepository((config.telegram_db_path or "").strip()) if config and status.db_path_configured else None
    if status.adapter_kind == "real" and config is not None:
        adapter = TelegramBotApiBroadcastAdapter(bot_token=config.telegram_bot_token or "")
    else:
        adapter = TelegramUnavailableBroadcastAdapter()
    _assert_telegram_adapter_selection(status, adapter)
    return adapter, repo if status.users_table_found else None, status.unavailable_reason, status.as_dict()


def _telegram_status_from_config_error() -> TelegramRuntimeStatus:
    return TelegramRuntimeStatus(
        token_configured=False,
        db_path_configured=False,
        db_path_value_masked_or_present_only="not_configured",
        db_exists=False,
        db_readable=False,
        users_table_found=False,
        users_count=0,
        users_with_chat_id_count=0,
        adapter_kind="unavailable",
        unavailable_reason="token_missing",
        config_source="config_error",
        project_cwd=os.getcwd(),
        project_path=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        env_file_checked=(),
        git_commit=None,
        runtime_version=None,
    )


def _assert_telegram_adapter_selection(status: TelegramRuntimeStatus, adapter) -> None:
    if status.token_configured and status.db_path_configured and status.db_exists and status.users_table_found:
        if not isinstance(adapter, TelegramBotApiBroadcastAdapter):
            logger.error(
                "telegram_status_adapter_mismatch token_configured=%s db_path_configured=%s db_exists=%s users_table_found=%s adapter=%s reason=%s commit=%s cwd=%s",
                status.token_configured,
                status.db_path_configured,
                status.db_exists,
                status.users_table_found,
                type(adapter).__name__,
                status.unavailable_reason,
                status.git_commit,
                status.project_cwd,
            )
            raise RuntimeError("telegram_status_adapter_mismatch")
    elif not isinstance(adapter, TelegramUnavailableBroadcastAdapter):
        logger.error("telegram_status_adapter_mismatch expected_unavailable adapter=%s reason=%s", type(adapter).__name__, status.unavailable_reason)
        raise RuntimeError("telegram_status_adapter_mismatch")


def _log_telegram_runtime_status(status: TelegramRuntimeStatus) -> None:
    logger.info(
        "MAX Telegram runtime status: token_configured=%s db_path_configured=%s db_exists=%s users_table_found=%s adapter=%s reason=%s cwd=%s commit=%s config_source=%s env_file_checked=%s",
        status.token_configured,
        status.db_path_configured,
        status.db_exists,
        status.users_table_found,
        status.adapter_kind,
        status.unavailable_reason,
        status.project_cwd,
        status.git_commit,
        status.config_source,
        list(status.env_file_checked),
    )


async def run_telegram_broadcast_smoke_check() -> dict[str, object]:
    """Safe Telegram adapter smoke check; never sends to clients."""

    try:
        config = load_config()
        test_chat_id = config.telegram_test_chat_id
    except ConfigError:
        config = None
        test_chat_id = None
    adapter, repo, _, diagnostics = _telegram_delivery_dependencies()
    result: dict[str, object] = {
        "token_configured": bool(diagnostics.get("token_configured")),
        "db_path_configured": bool(diagnostics.get("db_path_configured")),
        "db_exists": bool(diagnostics.get("db_exists")),
        "db_readable": bool(diagnostics.get("db_readable")),
        "users_table_readable": bool(diagnostics.get("users_table_found")),
        "users_count": int(diagnostics.get("users_count") or 0),
        "users_with_chat_id_count": int(diagnostics.get("users_with_chat_id_count") or 0),
        "test_send_message": "Тестовая отправка пропущена: TELEGRAM_TEST_CHAT_ID не задан",
    }
    if isinstance(adapter, TelegramBotApiBroadcastAdapter):
        smoke = await adapter.smoke_check(test_chat_id=test_chat_id)
        result.update(smoke)
        if test_chat_id and smoke.get("test_send_ok"):
            result["test_send_message"] = "✅ Тестовая отправка Telegram выполнена"
    logger.info(
        "MAX Telegram broadcast diagnostic: smoke_check token_configured=%s telegram_db_path_configured=%s telegram_db_exists=%s telegram_db_readable=%s telegram_users_table_found=%s telegram_users_count=%s telegram_users_with_chat_id_count=%s get_me_ok=%s test_send_skipped=%s",
        result["token_configured"], result["db_path_configured"], result["db_exists"], result["db_readable"], result["users_table_readable"], result["users_count"], result["users_with_chat_id_count"], result.get("get_me_ok"), result.get("test_send_skipped"),
    )
    return result


def _normalized_attachment(context: RouterContext) -> BroadcastAttachmentPayload | None:
    attachment_type = _broadcast_attachment_type(context)
    if not attachment_type:
        return None
    return BroadcastAttachmentPayload(type=attachment_type, original_platform=PLATFORM_MAX, max_payload=_broadcast_attachment(context), telegram_payload=None)



def _format_omnichannel_preview_estimate(estimate) -> str:
    warning = f"\n{estimate.media_warning}" if estimate.media_warning else ""
    _, _, _, telegram_diagnostics = _telegram_delivery_dependencies()
    return (
        "Аудитория: база YClients\n"
        f"Клиентов в YClients: {estimate.total_yclients_clients}\n"
        f"Доступны в Telegram: {estimate.telegram_candidates}\n"
        f"Доступны в MAX: {estimate.max_candidates}\n"
        f"Есть в обоих: {estimate.both_platforms}\n"
        f"Будет отправлено в Telegram: {estimate.telegram_selected}\n"
        f"Будет отправлено в MAX: {estimate.max_selected}\n"
        f"Недоступны: {estimate.unreachable}\n"
        f"Дубликаты исключены: {estimate.duplicates_excluded}"
        f"\n\n{_format_telegram_admin_diagnostics(telegram_diagnostics, estimate)}"
        f"{warning}"
    )

def _format_omnichannel_confirm(estimate) -> str:
    warning = f"\n\n{estimate.media_warning}" if estimate.media_warning else ""
    return (
        "⚠️ Подтвердите рассылку\n\n"
        "Аудитория: база YClients\n"
        f"Клиентов в YClients: {estimate.total_yclients_clients}\n"
        f"Доступны в Telegram: {estimate.telegram_candidates}\n"
        f"Доступны в MAX: {estimate.max_candidates}\n"
        f"Есть в обоих: {estimate.both_platforms}\n"
        f"Будет отправлено в Telegram: {estimate.telegram_selected}\n"
        f"Будет отправлено в MAX: {estimate.max_selected}\n"
        f"Недоступны: {estimate.unreachable}\n"
        f"Дубликаты исключены: {estimate.duplicates_excluded}\n"
        f"Всего доставок: {estimate.total_deliveries}"
        f"{warning}"
    )


def _format_omnichannel_report(report) -> str:
    telegram_reason = _telegram_report_reason(report)
    reason_line = f"\nПричина Telegram: {telegram_reason}" if telegram_reason else ""
    diagnostics_line = _format_telegram_admin_diagnostics(getattr(report, "telegram_diagnostics", {}))
    return (
        "✅ Рассылка завершена\n\n"
        f"Клиентов в YClients: {report.total_yclients_clients}\n"
        f"Выбрано в Telegram: {getattr(report, 'telegram_selected', 0)}\n"
        f"Выбрано в MAX: {getattr(report, 'max_selected', 0)}\n"
        f"Telegram: {report.telegram_sent} отправлено\n"
        f"MAX: {report.max_sent} отправлено\n"
        f"Не доставлено: {report.not_delivered}\n"
        f"Ошибок: {report.failed}\n"
        f"Дубликатов исключено: {report.skipped_duplicate}\n"
        f"Telegram недоступен: {report.skipped_sender_unavailable}\n"
        f"Заблокировали бота: {report.skipped_blocked}\n"
        f"Медиа не поддержано: {report.skipped_media_unsupported}"
        f"\n\n{diagnostics_line}"
        f"{reason_line}"
    )


def _telegram_report_reason(report) -> str | None:
    if report.telegram_sent > 0:
        return None
    if getattr(report, "telegram_unavailable_reason", None) == "token_missing":
        return "Telegram: не подключён — token_missing"
    if getattr(report, "telegram_unavailable_reason", None) == "db_path_missing":
        return "Telegram: не подключён — db_path_missing"
    if getattr(report, "telegram_unavailable_reason", None) == "db_not_found":
        return "Telegram DB не найдена"
    if getattr(report, "telegram_unavailable_reason", None) == "db_unreadable":
        return "Telegram DB недоступна для чтения"
    if getattr(report, "telegram_unavailable_reason", None) == "users_table_missing":
        return "Telegram users table не найдена"
    if getattr(report, "telegram_selected", 0) == 0:
        return "Telegram users matched: 0"
    if getattr(report, "telegram_unavailable_reason", None) == "adapter_mismatch":
        return "Telegram: adapter_mismatch"
    if getattr(report, "telegram_unavailable_reason", None) == "old_runtime_or_missing_commit":
        return "Telegram: old_runtime_or_missing_commit"
    if report.skipped_sender_unavailable:
        reason = getattr(report, "telegram_unavailable_reason", None) or getattr(report, "last_telegram_error_short", None) or "sender_unavailable"
        return f"Telegram adapter unavailable — {reason}"
    if report.skipped_blocked:
        return "Telegram API 403 blocked"
    if report.skipped_media_unsupported:
        return "media unsupported"
    if getattr(report, "last_telegram_error_short", None):
        return str(report.last_telegram_error_short)
    if report.failed:
        return "Telegram API error"
    return None


def _format_telegram_admin_diagnostics(diagnostics: dict[str, object] | None, estimate=None) -> str:
    diagnostics = diagnostics or {}
    adapter = str(diagnostics.get("adapter_kind") or diagnostics.get("adapter") or "unavailable")
    reason = diagnostics.get("unavailable_reason") or diagnostics.get("reason")
    if estimate is not None and getattr(estimate, "telegram_matching_diagnostics", None):
        diagnostics = {**diagnostics, **estimate.telegram_matching_diagnostics}
    reason_line = f"\nПричина: {reason}" if adapter != "real" and reason else ""
    matching = _format_telegram_matching_diagnostics(diagnostics)
    return (
        "🔌 Telegram connection\n"
        f"Token: {'configured' if diagnostics.get('token_configured') else 'not configured'}\n"
        f"DB path: {'configured' if diagnostics.get('db_path_configured') else 'not configured'}\n"
        f"DB exists: {'yes' if diagnostics.get('db_exists') else 'no'}\n"
        f"DB readable: {'yes' if diagnostics.get('db_readable') else 'no'}\n"
        f"users table: {'yes' if diagnostics.get('users_table_found') else 'no'}\n"
        f"users count: {int(diagnostics.get('users_count') or 0)}\n"
        f"users with chat_id count: {int(diagnostics.get('users_with_chat_id_count') or 0)}\n"
        f"adapter: {adapter}\n"
        f"runtime commit/version: {diagnostics.get('git_commit') or diagnostics.get('runtime_version') or 'missing'}\n"
        f"project path: {diagnostics.get('project_path') or 'unknown'}\n"
        f"cwd: {diagnostics.get('project_cwd') or 'unknown'}"
        f"{reason_line}"
        f"{matching}"
    )



def _format_telegram_matching_diagnostics(diagnostics: dict[str, object]) -> str:
    if "yclients_clients_count" not in diagnostics and "phone_key_intersection_count" not in diagnostics:
        return ""
    reason = diagnostics.get("telegram_unmatched_reason")
    reason_line = f"\ntelegram_unmatched_reason: {reason}" if reason else ""
    return (
        "\n\n📊 Telegram matching diagnostics:\n"
        f"telegram_users_count: {int(diagnostics.get('telegram_users_count') or diagnostics.get('users_count') or 0)}\n"
        f"telegram_users_with_chat_id_count: {int(diagnostics.get('telegram_users_with_chat_id_count') or diagnostics.get('users_with_chat_id_count') or 0)}\n"
        f"telegram_users_with_any_phone_count: {int(diagnostics.get('telegram_users_with_any_phone_count') or diagnostics.get('users_with_any_phone_count') or 0)}\n"
        f"telegram_users_with_yclients_client_id_count: {int(diagnostics.get('telegram_users_with_yclients_client_id_count') or diagnostics.get('users_with_yclients_client_id_count') or 0)}\n"
        f"yclients_clients_count: {int(diagnostics.get('yclients_clients_count') or 0)}\n"
        f"yclients_clients_with_any_phone_count: {int(diagnostics.get('yclients_clients_with_any_phone_count') or 0)}\n"
        f"yclients_clients_with_id_count: {int(diagnostics.get('yclients_clients_with_id_count') or 0)}\n"
        f"phone_key_intersection_count: {int(diagnostics.get('phone_key_intersection_count') or 0)}\n"
        f"client_id_intersection_count: {int(diagnostics.get('client_id_intersection_count') or 0)}\n"
        f"telegram_matched_by_client_id_count: {int(diagnostics.get('telegram_matched_by_client_id_count') or 0)}\n"
        f"telegram_matched_by_phone_count: {int(diagnostics.get('telegram_matched_by_phone_count') or 0)}\n"
        f"telegram_matches_before_deliverable_count: {int(diagnostics.get('telegram_matches_before_deliverable_count') or 0)}\n"
        f"telegram_matches_rejected_not_deliverable_count: {int(diagnostics.get('telegram_matches_rejected_not_deliverable_count') or 0)}\n"
        f"rejected_no_chat_id_count: {int(diagnostics.get('rejected_no_chat_id_count') or 0)}\n"
        f"rejected_notifications_disabled_count: {int(diagnostics.get('rejected_notifications_disabled_count') or 0)}\n"
        f"rejected_blocked_count: {int(diagnostics.get('rejected_blocked_count') or 0)}\n"
        f"rejected_stopped_count: {int(diagnostics.get('rejected_stopped_count') or 0)}\n"
        f"YClients keys samples: {', '.join(diagnostics.get('yclients_phone_key_samples_masked') or []) or 'n/a'}\n"
        f"Telegram keys samples: {', '.join(diagnostics.get('telegram_phone_key_samples_masked') or []) or 'n/a'}\n"
        f"Matched keys samples: {', '.join(diagnostics.get('matched_phone_key_samples_masked') or []) or 'n/a'}"
        f"{reason_line}"
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
    return os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
