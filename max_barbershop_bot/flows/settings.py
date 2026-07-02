"""Admin settings hub for the MAX bot."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from os import getenv
from uuid import uuid4
from zoneinfo import ZoneInfo

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH, DEFAULT_REMINDERS_ENABLED, DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS
from max_barbershop_bot.core.permissions import (
    ROLE_USER,
    can_manage_roles,
    can_view_contacts_settings,
    can_view_notification_settings,
    can_view_settings,
    can_view_yclients_settings,
    effective_role,
    is_protected_developer,
)
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows.notification_history import handle_notification_history, handle_notification_history_failed
from max_barbershop_bot.repositories.notification_history import NotificationHistoryRepository
from max_barbershop_bot.flows.staff import handle_staff_menu
from max_barbershop_bot.flows.yclients_settings import handle_connection_check, handle_yclients_menu
from max_barbershop_bot.flows.support import render_support_message
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.support_settings import (
    SupportSettingsRepository,
    build_max_support_url,
    display_support_username,
    effective_support_settings,
)
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactInfo, ContactsService
from max_barbershop_bot.services.navigation import go_back, show_home
from max_barbershop_bot.services.reminder_lifecycle import (
    get_lifecycle_status,
    start_reminder_lifecycle,
    stop_reminder_lifecycle,
)
from max_barbershop_bot.services.notifications import BOOKING_CONFIRMATION_IMMEDIATE, BOOKING_REMINDER_2H, BOOKING_REMINDER_48H
from max_barbershop_bot.services.reminders import (
    BookingNotificationContext,
    booking_reminder_keyboard,
    render_booking_notification_text,
    send_booking_notification,
)
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.ui.buttons import (
    ADMIN_SETTINGS_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    DEV_DIAGNOSTICS_FAILED_NOTIFICATIONS_PAYLOAD,
    DEV_DIAGNOSTICS_BOT_LOGS_CSV_PAYLOAD,
    DEV_DIAGNOSTICS_BOT_LOGS_PAYLOAD,
    DEV_DIAGNOSTICS_EVENT_SEARCH_PAYLOAD,
    DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD,
    DEV_DIAGNOSTICS_LOGS_PREV_PAYLOAD,
    DEV_DIAGNOSTICS_NOOP_PAYLOAD,
    DEV_DIAGNOSTICS_REFRESH_PAYLOAD,
    DEV_DIAGNOSTICS_RESTART_HELP_PAYLOAD,
    DEV_DIAGNOSTICS_STATUS_PAYLOAD,
    DEV_DIAGNOSTICS_USER_LOGS_PAYLOAD,
    DEV_DIAGNOSTICS_YCLIENTS_SMOKE_PAYLOAD,
    SETTINGS_CONTACTS_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD,
    SETTINGS_CONTACTS_MAP_DELETE_PREFIX,
    SETTINGS_CONTACTS_MAP_EDIT_PREFIX,
    SETTINGS_CONTACTS_MAP_GOOGLE_PAYLOAD,
    SETTINGS_CONTACTS_MAP_HIDE_PREFIX,
    SETTINGS_CONTACTS_MAP_SHOW_PREFIX,
    SETTINGS_CONTACTS_MAP_TWOGIS_PAYLOAD,
    SETTINGS_CONTACTS_MAP_YANDEX_PAYLOAD,
    SETTINGS_CONTACTS_PREVIEW_PAYLOAD,
    SETTINGS_CONTACTS_RESET_PAYLOAD,
    SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD,
    SETTINGS_DIAGNOSTICS_PAYLOAD,
    SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD,
    SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD,
    SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD,
    SETTINGS_SUPPORT_PAYLOAD,
    SETTINGS_SUPPORT_PREVIEW_PAYLOAD,
    SETTINGS_HOME_PAYLOAD,
    SETTINGS_NOTIFICATIONS_PAYLOAD,
    SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD,
    SETTINGS_NOTIFICATIONS_DISABLE_PAYLOAD,
    SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD,
    SETTINGS_NOTIFICATIONS_TESTS_PAYLOAD,
    SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD,
    SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD,
    SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD,
    SETTINGS_ROLES_PAYLOAD,
    SETTINGS_YCLIENTS_PAYLOAD,
    settings_contacts_input_keyboard,
    settings_contacts_keyboard,
    settings_contacts_map_keyboard,
    settings_diagnostics_keyboard,
    settings_menu_keyboard,
    settings_notifications_keyboard,
    settings_notification_tests_keyboard,
    settings_status_keyboard,
    settings_support_input_keyboard,
    settings_support_keyboard,
)
from max_barbershop_bot.ui.texts import (
    SETTINGS_MENU_TEXT,
    SETTINGS_NO_ACCESS_TEXT,
)
from max_barbershop_bot.services.developer_diagnostics import (
    NO_ACCESS_TEXT as DEV_DIAGNOSTICS_NO_ACCESS_TEXT,
    build_developer_diagnostics_text,
    build_developer_status_text,
)
from max_barbershop_bot.repositories.diagnostics import DiagnosticsRepository
from max_barbershop_bot.services.diagnostics import sanitize_text
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard

LOG_LINES_LIMIT = 200
LOG_CHUNK_LIMIT = 3000
STATE_LOG_PAGES_KEY = "devdiag_log_pages"
STATE_LOG_PAGE_INDEX_KEY = "devdiag_log_page_index"
logger = logging.getLogger(__name__)


def register_settings_routes(router: Router) -> None:
    """Register the settings hub and its lightweight subsections."""

    router.on_callback(ADMIN_SETTINGS_PAYLOAD, handle_settings_menu)
    router.on_callback(SETTINGS_YCLIENTS_PAYLOAD, handle_settings_yclients)
    router.on_callback(SETTINGS_CONTACTS_PAYLOAD, handle_settings_contacts)
    router.on_callback(SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD, handle_settings_contacts_edit_address)
    router.on_callback(SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD, handle_settings_contacts_edit_phone)
    router.on_callback(SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD, handle_settings_contacts_edit_schedule)
    router.on_callback(SETTINGS_CONTACTS_PREVIEW_PAYLOAD, handle_settings_contacts_preview)
    router.on_callback(SETTINGS_CONTACTS_MAP_YANDEX_PAYLOAD, handle_settings_contacts_map_yandex)
    router.on_callback(SETTINGS_CONTACTS_MAP_TWOGIS_PAYLOAD, handle_settings_contacts_map_twogis)
    router.on_callback(SETTINGS_CONTACTS_MAP_GOOGLE_PAYLOAD, handle_settings_contacts_map_google)
    router.on_callback_prefix(SETTINGS_CONTACTS_MAP_EDIT_PREFIX, handle_settings_contacts_map_edit)
    router.on_callback_prefix(SETTINGS_CONTACTS_MAP_HIDE_PREFIX, handle_settings_contacts_map_hide)
    router.on_callback_prefix(SETTINGS_CONTACTS_MAP_SHOW_PREFIX, handle_settings_contacts_map_show)
    router.on_callback_prefix(SETTINGS_CONTACTS_MAP_DELETE_PREFIX, handle_settings_contacts_map_delete)
    router.on_callback(SETTINGS_CONTACTS_RESET_PAYLOAD, handle_settings_contacts_reset)
    router.on_callback(SETTINGS_SUPPORT_PAYLOAD, handle_settings_support)
    router.on_callback(SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD, handle_settings_support_edit_username)
    router.on_callback(SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD, handle_settings_support_edit_description)
    router.on_callback(SETTINGS_SUPPORT_PREVIEW_PAYLOAD, handle_settings_support_preview)
    router.on_callback(SETTINGS_NOTIFICATIONS_PAYLOAD, handle_settings_notifications)
    router.on_callback(SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD, handle_settings_notifications_toggle)
    router.on_callback(SETTINGS_NOTIFICATIONS_DISABLE_PAYLOAD, handle_settings_notifications_toggle)
    router.on_callback(SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD, handle_settings_notifications_smoke)
    router.on_callback(SETTINGS_NOTIFICATIONS_TESTS_PAYLOAD, handle_settings_notifications_tests)
    router.on_callback(SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD, handle_settings_notifications_run_test)
    router.on_callback(SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD, handle_settings_notifications_run_test)
    router.on_callback(SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD, handle_settings_notifications_run_test)
    router.on_callback(SETTINGS_ROLES_PAYLOAD, handle_settings_roles)
    router.on_callback(SETTINGS_DIAGNOSTICS_PAYLOAD, handle_settings_diagnostics)
    router.on_callback(DEV_DIAGNOSTICS_REFRESH_PAYLOAD, handle_settings_diagnostics_refresh)
    router.on_callback(DEV_DIAGNOSTICS_BOT_LOGS_PAYLOAD, handle_settings_diagnostics_bot_logs)
    router.on_callback(DEV_DIAGNOSTICS_LOGS_PREV_PAYLOAD, handle_settings_diagnostics_log_pagination)
    router.on_callback(DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD, handle_settings_diagnostics_log_pagination)
    router.on_callback(DEV_DIAGNOSTICS_NOOP_PAYLOAD, handle_settings_diagnostics_noop)
    router.on_callback(DEV_DIAGNOSTICS_BOT_LOGS_CSV_PAYLOAD, handle_settings_diagnostics_bot_logs_csv)
    router.on_callback(DEV_DIAGNOSTICS_USER_LOGS_PAYLOAD, handle_settings_diagnostics_user_logs_prompt)
    router.on_callback(DEV_DIAGNOSTICS_EVENT_SEARCH_PAYLOAD, handle_settings_diagnostics_event_search_prompt)
    router.on_callback(DEV_DIAGNOSTICS_STATUS_PAYLOAD, handle_settings_diagnostics_status)
    router.on_callback(DEV_DIAGNOSTICS_YCLIENTS_SMOKE_PAYLOAD, handle_settings_diagnostics_yclients_smoke)
    router.on_callback(DEV_DIAGNOSTICS_RESTART_HELP_PAYLOAD, handle_settings_diagnostics_restart_help)
    router.on_callback(DEV_DIAGNOSTICS_FAILED_NOTIFICATIONS_PAYLOAD, handle_settings_diagnostics_failed_notifications)
    router.on_callback(SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD, handle_settings_notification_history)
    router.on_callback(SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD, handle_settings_yclients_check)
    router.on_callback(SETTINGS_BACK_PAYLOAD, handle_settings_back)
    router.on_callback(SETTINGS_HOME_PAYLOAD, handle_settings_home)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN, handle_settings_contacts_address_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN, handle_settings_contacts_phone_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN, handle_settings_contacts_schedule_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_YANDEX_MAPS_SCREEN, handle_settings_contacts_yandex_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_TWOGIS_SCREEN, handle_settings_contacts_twogis_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_GOOGLE_MAPS_SCREEN, handle_settings_contacts_google_input)
    router.on_screen_text(state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN, handle_settings_support_username_input)
    router.on_screen_text(state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN, handle_settings_support_description_input)
    router.on_screen_text(state.SETTINGS_DIAGNOSTICS_USER_LOGS_INPUT_SCREEN, handle_settings_diagnostics_user_logs_input)
    router.on_screen_text(state.SETTINGS_DIAGNOSTICS_EVENT_SEARCH_INPUT_SCREEN, handle_settings_diagnostics_event_search_input)


async def handle_settings_menu(context: RouterContext) -> None:
    """Open role-based settings hub."""

    actor_role = _actor_role(context)
    if not can_view_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    _push_current_screen(context, state.SETTINGS_MENU_SCREEN)
    _audit(context, actor_role, action="settings_opened", section="settings")
    await _show_settings_menu(context, actor_role)


async def handle_settings_yclients(context: RouterContext) -> None:
    """Route YClients settings to the existing YClients flow."""

    actor_role = _actor_role(context)
    if not can_view_yclients_settings(actor_role):
        await _send_no_access(context)
        return
    _audit(context, actor_role, action="settings_section_opened", section="yclients")
    await handle_yclients_menu(context)


async def handle_settings_contacts(context: RouterContext) -> None:
    """Show contacts override editor ported from the Telegram settings UX."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    _audit(context, actor_role, action="settings_section_opened", section="contacts")
    await _show_contacts_editor(context)


async def handle_settings_contacts_edit_address(context: RouterContext) -> None:
    """Ask for a new contacts address."""

    await _start_contacts_edit(context, state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN, "🏠 Введите новый адрес:")


async def handle_settings_contacts_edit_phone(context: RouterContext) -> None:
    """Ask for a new contacts phone."""

    await _start_contacts_edit(context, state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN, "📞 Введите новый телефон:")


async def handle_settings_contacts_edit_schedule(context: RouterContext) -> None:
    """Ask for a new contacts work schedule."""

    await _start_contacts_edit(context, state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN, "⏰ Введите новый режим работы:")


async def handle_settings_contacts_preview(context: RouterContext) -> None:
    """Show contacts preview using the same resolved contacts service as the public screen."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    contacts = await ContactsService(YClientsSettingsRepository(_database_path())).get_contacts()
    state.set_current_screen(context.event.platform_user_id, _settings_state_chat_id(context), state.SETTINGS_CONTACTS_SCREEN)
    await context.send_text(_render_contacts_preview(contacts), keyboard=settings_contacts_keyboard())


async def handle_settings_contacts_reset(context: RouterContext) -> None:
    """Clear local contacts edits and fall back to YClients."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    YClientsSettingsRepository(_database_path()).set_contacts_override({})
    _audit(
        context,
        actor_role,
        action="contacts_override_cleared",
        section="contacts",
        metadata={"field": "contacts_override"},
    )
    await context.send_text("♻️ Локальные правки контактов сброшены. Теперь используются данные из YClients.")
    await _show_contacts_editor(context)


async def handle_settings_contacts_address_input(context: RouterContext) -> None:
    """Save contacts address text input."""

    await _save_contact_field(context, field="address", value=context.event.text or "")


async def handle_settings_contacts_phone_input(context: RouterContext) -> None:
    """Save contacts phone text input."""

    await _save_contact_field(context, field="phone", value=context.event.text or "")


async def handle_settings_contacts_schedule_input(context: RouterContext) -> None:
    """Save contacts schedule text input."""

    await _save_contact_field(context, field="schedule", value=context.event.text or "")


async def handle_settings_contacts_map_yandex(context: RouterContext) -> None:
    await _show_contacts_map_editor(context, "yandex")


async def handle_settings_contacts_map_twogis(context: RouterContext) -> None:
    await _show_contacts_map_editor(context, "twogis")


async def handle_settings_contacts_map_google(context: RouterContext) -> None:
    await _show_contacts_map_editor(context, "google")


async def handle_settings_contacts_map_edit(context: RouterContext) -> None:
    map_key = _payload_suffix(context, SETTINGS_CONTACTS_MAP_EDIT_PREFIX)
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    await _start_contacts_edit(context, meta["screen"], f"🗺 Введите ссылку для {meta['prompt_name']}:")


async def handle_settings_contacts_map_hide(context: RouterContext) -> None:
    await _set_contacts_map_enabled(context, _payload_suffix(context, SETTINGS_CONTACTS_MAP_HIDE_PREFIX), enabled=False)


async def handle_settings_contacts_map_show(context: RouterContext) -> None:
    map_key = _payload_suffix(context, SETTINGS_CONTACTS_MAP_SHOW_PREFIX)
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    override = YClientsSettingsRepository(_database_path()).get_contacts_override()
    if not str(override.get(meta["url_field"]) or "").strip():
        await context.send_text("Сначала добавьте ссылку 🙏")
        await _show_contacts_map_editor(context, map_key)
        return
    await _set_contacts_map_enabled(context, map_key, enabled=True)


async def handle_settings_contacts_map_delete(context: RouterContext) -> None:
    map_key = _payload_suffix(context, SETTINGS_CONTACTS_MAP_DELETE_PREFIX)
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    repo = YClientsSettingsRepository(_database_path())
    override = repo.get_contacts_override()
    override[meta["url_field"]] = ""
    override[meta["enabled_field"]] = False
    repo.set_contacts_override(override)
    await _show_contacts_map_editor(context, map_key)


async def handle_settings_contacts_yandex_input(context: RouterContext) -> None:
    await _save_contact_map_url(context, "yandex", context.event.text or "")


async def handle_settings_contacts_twogis_input(context: RouterContext) -> None:
    await _save_contact_map_url(context, "twogis", context.event.text or "")


async def handle_settings_contacts_google_input(context: RouterContext) -> None:
    await _save_contact_map_url(context, "google", context.event.text or "")


async def handle_settings_support(context: RouterContext) -> None:
    """Show support settings editor."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    _audit(context, actor_role, action="settings_section_opened", section="support")
    await _show_support_editor(context)


async def handle_settings_support_edit_username(context: RouterContext) -> None:
    """Ask for support username."""

    await _start_support_edit(context, state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN, "👤 Введите username поддержки в Telegram, например @flowbots1sup:")


async def handle_settings_support_edit_description(context: RouterContext) -> None:
    """Ask for support description."""

    await _start_support_edit(context, state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN, "📝 Введите текст поддержки:")


async def handle_settings_support_preview(context: RouterContext) -> None:
    """Show public support screen preview."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    settings = _support_settings()
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_SUPPORT_SCREEN)
    await context.send_text(render_support_message(settings), keyboard=settings_support_keyboard())


async def handle_settings_support_username_input(context: RouterContext) -> None:
    """Save support username text input."""

    await _save_support_settings(context, support_username=context.event.text or "", support_description=None)


async def handle_settings_support_description_input(context: RouterContext) -> None:
    """Save support description text input."""

    await _save_support_settings(context, support_username=None, support_description=context.event.text or "")


async def handle_settings_notifications(context: RouterContext) -> None:
    """Show notification settings status and link existing history."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _ensure_reminder_loop_if_enabled(context)
    _audit(context, actor_role, action="settings_section_opened", section="notifications")
    await _show_notifications_settings(context)


async def handle_settings_notifications_toggle(context: RouterContext) -> None:
    """Persist the global notification switch and rerender the screen."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    enabled = context.event.callback_payload == SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD
    AppSettingsRepository(_database_path()).set_notifications_enabled(enabled)
    interval_seconds = _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    if enabled:
        await start_reminder_lifecycle(
            context.sender,
            database_path=_database_path(),
            interval_seconds=interval_seconds,
        )
    elif not enabled:
        await stop_reminder_lifecycle(reason="notifications_disabled")
    _audit(
        context,
        actor_role,
        action="notifications_enabled_updated",
        section="notifications",
        metadata={"notifications_enabled": enabled},
    )
    await _show_notifications_settings(context)


async def handle_settings_notifications_smoke(context: RouterContext) -> None:
    """Run a non-destructive notification smoke check."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _ensure_reminder_loop_if_enabled(context)
    text = _build_notifications_smoke_text()
    _audit(context, actor_role, action="notifications_smoke_checked", section="notifications")
    await context.send_text(text, keyboard=settings_notifications_keyboard(enabled=AppSettingsRepository(_database_path()).notifications_enabled()))


async def handle_settings_notifications_tests(context: RouterContext) -> None:
    """Open safe per-notification tests, ported from Telegram dev-tests UX."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    text = (
        "🧪 Тест уведомлений\n\n"
        "Выберите конкретное уведомление для безопасной проверки.\n"
        "Тесты не отправляют сообщения клиентам, не меняют YClients и не создают реальные записи."
    )
    await context.send_text(text, keyboard=settings_notification_tests_keyboard())


async def handle_settings_notifications_run_test(context: RouterContext) -> None:
    """Run one safe notification dry-run test for the current admin/developer."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    payload = context.event.callback_payload or ""
    text = await _send_notification_test_and_build_result_text(context, payload)
    _audit(context, actor_role, action="notifications_safe_test_run", section="notifications", metadata={"payload": payload})
    await context.send_text(text, keyboard=settings_notification_tests_keyboard())


async def _show_notifications_settings(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_NOTIFICATIONS_SCREEN)
    repository = AppSettingsRepository(_database_path())
    enabled = repository.notifications_enabled()
    poll_interval_seconds = _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    lifecycle = get_lifecycle_status()
    loop_label, loop_reason = _format_loop_status(enabled, lifecycle)
    status_label = "✅ включены" if enabled else "❌ выключены"
    text = (
        "🔔 Уведомления\n\n"
        f"Текущий статус: {status_label}\n"
        f"Цикл напоминаний: {loop_label}\n"
        f"{loop_reason}"
        f"Интервал проверки: {poll_interval_seconds} сек.\n\n"
        "Бот может отправлять:\n"
        "• ✅ Подтверждение записи сразу\n"
        "• ✅ Подтверждение записи за 48 часов (или за 6 часов, если запись ближе)\n"
        "• ⏰ Напоминание за 2 часа"
    )
    await context.send_text(text, keyboard=settings_notifications_keyboard(enabled=enabled))


def _build_notifications_smoke_text() -> str:
    repository = AppSettingsRepository(_database_path())
    settings_ok = True
    history_ok = True
    yclients_ok = True
    try:
        notifications_enabled = repository.notifications_enabled()
    except Exception:
        settings_ok = False
        notifications_enabled = True
    try:
        NotificationHistoryRepository(_database_path()).count_by_status()
    except Exception:
        history_ok = False
    try:
        yclients_settings = YClientsSettingsRepository(_database_path()).get_active()
        yclients_ok = bool(yclients_settings and yclients_settings.company_id)
    except Exception:
        yclients_ok = False
    lifecycle = get_lifecycle_status()
    interval_seconds = _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    loop_smoke = _format_smoke_loop_status(notifications_enabled, lifecycle)
    duplicate_prevention_ok = True
    blocked_stopped_ok = True
    lines = [
        "🔄 Проверка уведомлений",
        "",
        f"Настройки: {'✅ OK' if settings_ok else '❌ ошибка'}",
        f"Глобальный статус: {'✅ включены' if notifications_enabled else '❌ выключены'}",
        f"История уведомлений: {'✅ OK' if history_ok else '❌ ошибка записи/чтения'}",
        "Отправка сообщений: ✅ OK",
        f"Цикл напоминаний: {loop_smoke}",
        f"Интервал: {interval_seconds} сек.",
        f"YClients: {'✅ OK' if yclients_ok else '⚠️ не настроен'}",
        f"Дедупликация: {'✅ OK' if duplicate_prevention_ok else '❌ ошибка'}",
        f"Blocked/stopped: {'✅ OK' if blocked_stopped_ok else '❌ ошибка'}",
        "",
        "Проверка не отправляет сообщения клиентам ✅",
    ]
    return "\n".join(lines)


async def _ensure_reminder_loop_if_enabled(context: RouterContext) -> None:
    if not AppSettingsRepository(_database_path()).notifications_enabled():
        return
    if get_lifecycle_status().is_running:
        return
    await start_reminder_lifecycle(
        context.sender,
        database_path=_database_path(),
        interval_seconds=_int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30),
    )


def _format_loop_status(enabled: bool, lifecycle) -> tuple[str, str]:
    if not enabled:
        return "⏸ остановлен", ""
    if lifecycle.is_running:
        return "✅ запущен", ""
    if lifecycle.last_error_class:
        return "❌ ошибка", f"Причина: {lifecycle.last_error_class}\n"
    return "❌ ошибка запуска", "Причина: цикл не стартовал в runtime.\n"


def _format_smoke_loop_status(enabled: bool, lifecycle) -> str:
    if enabled and lifecycle.is_running:
        return "✅ запущен"
    if not enabled:
        return "⏸ остановлен, потому что уведомления выключены"
    if lifecycle.last_error_class:
        return f"❌ ошибка: {lifecycle.last_error_class}"
    return "❌ ошибка запуска"



async def _send_notification_test_and_build_result_text(context: RouterContext, payload: str) -> str:
    notification_type_by_payload = {
        SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD: BOOKING_CONFIRMATION_IMMEDIATE,
        SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD: BOOKING_REMINDER_48H,
        SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD: BOOKING_REMINDER_2H,
    }
    label_by_payload = {
        SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD: "подтверждение записи сразу",
        SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD: "подтверждение записи за 48 часов",
        SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD: "напоминание за 2 часа",
    }
    notification_type = notification_type_by_payload.get(payload)
    if notification_type is None:
        return "🧪 Тест уведомлений\n\n❌ Неизвестный тест. Откройте раздел заново."

    now_utc = datetime.now(UTC)
    branch_timezone = "Europe/Moscow"
    booking_datetime = _dev_test_booking_datetime(notification_type, now_utc, branch_timezone)
    record_suffix = "confirm-48h" if notification_type == BOOKING_REMINDER_48H else "2h" if notification_type == BOOKING_REMINDER_2H else "immediate"
    dev_record_id = f"dev-test-{record_suffix}-{context.event.platform_user_id}-{int(now_utc.timestamp() * 1000)}-{uuid4().hex[:8]}"
    reminder_type = "confirm_2d" if notification_type == BOOKING_REMINDER_48H else "reminder_2h" if notification_type == BOOKING_REMINDER_2H else "immediate"
    logger.info(
        "dev_test_booking_reminder_clicked actor_platform_user_id=%s yclients_record_id=%s reminder_type=%s",
        context.event.platform_user_id,
        dev_record_id,
        reminder_type,
    )

    booking_context = BookingNotificationContext(
        platform_user_id=str(context.event.platform_user_id or "dev-safe-test"),
        max_user_id=context.event.max_user_id or context.event.platform_user_id,
        chat_id=context.event.chat_id,
        yclients_record_id=dev_record_id,
        yclients_client_id="dev-test-client",
        notification_type=notification_type,
        booking_datetime=booking_datetime,
        service_name="МУЖСКАЯ СТРИЖКА" if notification_type in {BOOKING_REMINDER_48H, BOOKING_REMINDER_2H} else "Тестовая стрижка",
        master_name="Рената Пономарёва" if notification_type in {BOOKING_REMINDER_48H, BOOKING_REMINDER_2H} else "Тестовый мастер",
        client_name="Илья" if notification_type in {BOOKING_REMINDER_48H, BOOKING_REMINDER_2H} else "Тестовый клиент",
        branch_address=await _dev_test_branch_address(),
        scheduled_for=now_utc,
    )
    result = await send_booking_notification(
        context.sender,
        database_path=_database_path(),
        context=booking_context,
        timezone_name=branch_timezone,
        keyboard=booking_reminder_keyboard(booking_context),
        respect_global_settings=False,
    )
    preview = render_booking_notification_text(booking_context, branch_timezone)
    sent = bool(result and result.status == "sent" and result.sent_at)
    status_line = "✅ Тестовое уведомление отправлено." if sent else "⚠️ Тестовое событие создано, но уведомление не отправилось. Проверьте логи."
    logger.info(
        "dev_test_booking_reminder_process_finished actor_platform_user_id=%s yclients_record_id=%s reminder_type=%s status_after=%s sent_at_utc=%s error_summary=%s",
        context.event.platform_user_id,
        dev_record_id,
        reminder_type,
        result.status if result else "failed",
        result.sent_at if result else None,
        result.delivery_error_message if result else "send_booking_notification_returned_none",
    )
    return (
        "🧪 Тест уведомлений\n\n"
        f"Тип: {label_by_payload[payload]}\n"
        f"Статус: {status_line}\n"
        f"yclients_record_id={dev_record_id}\n"
        f"reminder_type={reminder_type}\n"
        f"history_status={(result.status if result else 'failed')}\n\n"
        "Что проверено:\n"
        "• ✅ шаблон уведомления собирается по Telegram reference\n"
        "• ✅ сообщение реально отправляется текущему MAX-пользователю/чату\n"
        "• ✅ запись помечена как dev/test и не трогает YClients/реальных клиентов\n"
        "• ✅ результат пишется в notification_history/notification_delivery\n\n"
        "Предпросмотр текста:\n"
        f"{preview}"
    )[:3900]


def _dev_test_booking_datetime(notification_type: str, now_utc: datetime, branch_timezone: str) -> datetime:
    branch_tz = ZoneInfo(branch_timezone)
    now_local = now_utc.astimezone(branch_tz)
    visit_date = (now_local + timedelta(days=3 if notification_type == BOOKING_REMINDER_48H else 1)).date()
    if notification_type in {BOOKING_REMINDER_48H, BOOKING_REMINDER_2H}:
        return datetime.combine(visit_date, time(hour=21, minute=0), tzinfo=branch_tz)
    return now_local + timedelta(days=1, hours=2)


async def _dev_test_branch_address() -> str:
    try:
        contacts = await ContactsService(YClientsSettingsRepository(_database_path())).get_contacts()
    except Exception:
        return "Тестовый адрес"
    return str(contacts.address or "Тестовый адрес").strip() or "Тестовый адрес"


def _build_notification_button_test_text() -> str:
    expected_payloads = {
        SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD: "✅ Включить уведомления",
        SETTINGS_NOTIFICATIONS_DISABLE_PAYLOAD: "❌ Выключить уведомления",
        SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD: "🧾 История уведомлений",
        SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD: "🔄 Проверить работу уведомлений",
        SETTINGS_NOTIFICATIONS_TESTS_PAYLOAD: "🧪 Тест уведомлений",
        SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD: "✅ Тест подтверждения сразу",
        SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD: "✅ Тест подтверждения за 48 часов",
        SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD: "⏰ Тест напоминания за 2 часа",
        SETTINGS_NOTIFICATIONS_PAYLOAD: "⬅️ Назад к уведомлениям",
        SETTINGS_BACK_PAYLOAD: "⬅️ Назад",
        SETTINGS_HOME_PAYLOAD: "🏠 Главное меню",
    }
    registered_payloads = set(expected_payloads)
    errors: list[str] = []
    keyboards = [settings_notifications_keyboard(enabled=True), settings_notifications_keyboard(enabled=False), settings_notification_tests_keyboard()]
    for keyboard in keyboards:
        seen: list[str] = []
        for row in keyboard.rows:
            for button in row:
                payload = button.payload
                if not payload:
                    errors.append(f"нет payload у кнопки {button.text}")
                    continue
                if payload in seen:
                    errors.append(f"дубликат payload {payload}")
                seen.append(payload)
                if payload not in registered_payloads:
                    errors.append(f"нет handler для {button.text} / {payload}")
    lifecycle_read_ok = get_lifecycle_status() is not None
    checks = [
        ("Экран уведомлений", True),
        ("Включить/выключить", not errors),
        ("Тест подтверждения сразу", SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD in registered_payloads),
        ("Тест 48 часов", SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD in registered_payloads),
        ("Тест 2 часа", SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD in registered_payloads),
        ("История уведомлений", SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD in registered_payloads),
        ("Проверка работы уведомлений", SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD in registered_payloads),
        ("Назад / Главное меню", SETTINGS_BACK_PAYLOAD in registered_payloads and SETTINGS_HOME_PAYLOAD in registered_payloads),
        ("Callback handlers", not errors),
        ("Статус цикла напоминаний", lifecycle_read_ok),
    ]
    lines = ["🧪 Тест кнопок уведомлений", ""]
    for label, ok in checks:
        lines.append(f"{'✅' if ok else '❌'} {label}")
    if errors:
        lines.extend(["", "Ошибки:"])
        lines.extend(f"• {error}" for error in errors[:10])
    lines.extend(["", f"Ошибок: {len(errors)}"])
    return "\n".join(lines)


async def handle_settings_roles(context: RouterContext) -> None:
    """Route roles to the existing staff flow."""

    actor_role = _actor_role(context)
    if not can_manage_roles(actor_role):
        await _send_no_access(context)
        return
    _audit(context, actor_role, action="settings_section_opened", section="roles")
    await handle_staff_menu(context)


async def handle_settings_diagnostics(context: RouterContext) -> None:
    """Show protected developer diagnostics."""

    actor_role = _actor_role(context)
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_SCREEN)
    text = await build_developer_diagnostics_text(database_path=_database_path())
    _audit(context, actor_role, action="settings_section_opened", section="diagnostics")
    await context.send_text(text, keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_refresh(context: RouterContext) -> None:
    """Refresh protected developer diagnostics."""

    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    text = await build_developer_diagnostics_text(database_path=_database_path())
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_SCREEN)
    _audit(context, _actor_role(context), action="settings_section_refreshed", section="diagnostics")
    await context.send_text(text, keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_bot_logs(context: RouterContext) -> None:
    """Show last bot log lines in safe chunks."""

    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    lines = _load_log_lines()
    if lines is None:
        await context.send_text("Логи пока недоступны 🙏", keyboard=settings_diagnostics_keyboard())
        return
    pages = _chunk_lines(lines)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGES_KEY, pages)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGE_INDEX_KEY, 0)
    await _send_logs_page(context, 0)


async def handle_settings_diagnostics_log_pagination(context: RouterContext) -> None:
    """Navigate bot log pages."""

    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    current = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGE_INDEX_KEY)
    page_index = int(current) if isinstance(current, int) else 0
    if context.event.callback_payload == DEV_DIAGNOSTICS_LOGS_PREV_PAYLOAD:
        page_index -= 1
    else:
        page_index += 1
    await _send_logs_page(context, page_index)


async def handle_settings_diagnostics_bot_logs_csv(context: RouterContext) -> None:
    """Explain CSV behavior because MAX file upload is not wired for documents."""

    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    lines = _load_log_lines()
    if lines is None:
        await context.send_text("Логи пока недоступны 🙏", keyboard=settings_diagnostics_keyboard())
        return
    csv_path = DiagnosticsRepository(_database_path()).export_bot_logs_csv(LOG_LINES_LIMIT)
    csv_bytes = csv_path.read_bytes()
    try:
        csv_path.unlink(missing_ok=True)
    except OSError:
        pass
    sent = await _send_file_to_current_chat(
        context,
        csv_bytes,
        filename="bot_logs_last_200.csv",
        caption="📦 Логи бота CSV",
    )
    if not sent:
        await context.send_text(
            "📦 Скачать логи бота (CSV)\n\n"
            "Не удалось отправить CSV-файл через MAX. Показываю безопасный текстовый вариант:\n\n"
            f"{_short(_build_logs_csv_text(lines), 2800)}",
            keyboard=settings_diagnostics_keyboard(),
        )


async def handle_settings_diagnostics_noop(context: RouterContext) -> None:
    if context.event.callback_id:
        await context.answer_callback()


async def handle_settings_diagnostics_user_logs_prompt(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_USER_LOGS_INPUT_SCREEN)
    await context.send_text("👤 Логи пользователя\n\nВведите user_id или @username:", keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_user_logs_input(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    query = (context.event.text or "").strip()
    repository = DiagnosticsRepository(_database_path())
    rows = repository.find_user_events(query, limit=500)
    summary = repository.summarize_events(rows)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_SCREEN)
    await context.send_text(
        "👤 Логи пользователя\n\n" + _render_user_events(rows, summary=summary),
        keyboard=settings_diagnostics_keyboard(),
    )


async def handle_settings_diagnostics_event_search_prompt(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_EVENT_SEARCH_INPUT_SCREEN)
    await context.send_text("🔎 Поиск по событиям\n\nВведите ключевое слово:", keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_event_search_input(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    query = (context.event.text or "").strip()
    rows = DiagnosticsRepository(_database_path()).search_events(query, limit=500)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_SCREEN)
    await context.send_text("🔎 Поиск по событиям\n\n" + _render_user_events(rows), keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_status(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await context.send_text(await build_developer_status_text(database_path=_database_path()), keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_yclients_smoke(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await context.send_text("🧪 YClients: client sync smoke test\n\nSmoke test пока недоступен в MAX 🙏", keyboard=settings_diagnostics_keyboard())


async def handle_settings_diagnostics_restart_help(context: RouterContext) -> None:
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await context.send_text(
        "♻️ Перезапустить бота (инструкция)\n\n"
        "Для перезапуска выполните на сервере:\n"
        "sudo systemctl restart telegram-bot@max-barbershop-bot\n\n"
        "Проверить статус:\n"
        "sudo systemctl status telegram-bot@max-barbershop-bot",
        keyboard=settings_diagnostics_keyboard(),
    )


async def handle_settings_diagnostics_failed_notifications(context: RouterContext) -> None:
    """Open existing failed notification history from protected diagnostics."""

    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    _audit(context, _actor_role(context), action="diagnostics_failed_notifications_opened", section="diagnostics")
    await handle_notification_history_failed(context)


async def handle_settings_notification_history(context: RouterContext) -> None:
    """Open existing notification history from settings."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    _audit(context, actor_role, action="diagnostics_notification_history_opened", section="diagnostics")
    await handle_notification_history(context)


async def handle_settings_yclients_check(context: RouterContext) -> None:
    """Run existing YClients check from diagnostics."""

    actor_role = _actor_role(context)
    if not _is_protected_developer(context):
        await _send_dev_no_access(context)
        return
    _audit(context, actor_role, action="diagnostics_yclients_check_started", section="diagnostics")
    await handle_connection_check(context)


async def handle_settings_back(context: RouterContext) -> None:
    """Return from a settings subsection to the hub, or from hub to previous screen."""

    actor_role = _actor_role(context)
    if not can_view_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    current = state.get_current_screen(context.event.platform_user_id, _settings_state_chat_id(context))
    if current in {state.SETTINGS_CONTACTS_MAP_SCREEN, state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN, state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN, state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN, state.SETTINGS_CONTACTS_EDIT_YANDEX_MAPS_SCREEN, state.SETTINGS_CONTACTS_EDIT_TWOGIS_SCREEN, state.SETTINGS_CONTACTS_EDIT_GOOGLE_MAPS_SCREEN}:
        await _show_contacts_editor(context)
        return
    if current in {state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN, state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN}:
        await _show_support_editor(context)
        return
    if current in {state.SETTINGS_CONTACTS_SCREEN, state.SETTINGS_SUPPORT_SCREEN, state.SETTINGS_NOTIFICATIONS_SCREEN, state.SETTINGS_DIAGNOSTICS_SCREEN}:
        await _show_settings_menu(context, actor_role)
        return
    await go_back(context)


async def handle_settings_home(context: RouterContext) -> None:
    """Return to role-based home menu."""

    await _answer_callback_if_needed(context)
    await show_home(context)


async def _show_settings_menu(context: RouterContext, actor_role: str) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MENU_SCREEN)
    await context.send_text(
        SETTINGS_MENU_TEXT,
        keyboard=settings_menu_keyboard(actor_role, protected_developer=_is_protected_developer(context)),
    )


async def _show_contacts_editor(context: RouterContext) -> None:
    contacts = await ContactsService(YClientsSettingsRepository(_database_path())).get_contacts()
    text = (
        "✏️ Редактирование контактов\n\n"
        f"🏠 Адрес: {contacts.address or '—'}\n"
        f"📞 Телефон: {contacts.phone or '—'}\n"
        f"⏰ Режим работы: {contacts.schedule or '—'}\n\n"
        "🗺 Карты:\n"
        f"Яндекс Карты: {_map_status(contacts.raw, 'yandex')}\n"
        f"2GIS: {_map_status(contacts.raw, 'twogis')}\n"
        f"Google Maps: {_map_status(contacts.raw, 'google')}"
    )
    state.set_current_screen(context.event.platform_user_id, _settings_state_chat_id(context), state.SETTINGS_CONTACTS_SCREEN)
    await context.send_text(text, keyboard=settings_contacts_keyboard())


async def _show_support_editor(context: RouterContext) -> None:
    settings = _support_settings()
    username = display_support_username(settings.support_username) or "—"
    support_url = build_max_support_url(settings.support_max_username) or "—"
    text = (
        "🆘 Редактирование поддержки\n\n"
        f"👤 Username: {username}\n"
        f"🔗 MAX-кнопка: {support_url}\n"
        f"📝 Текст: {settings.support_description or '—'}"
    )
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_SUPPORT_SCREEN)
    await context.send_text(text, keyboard=settings_support_keyboard())


async def _start_support_edit(context: RouterContext, screen_id: str, prompt: str) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, screen_id)
    await context.send_text(prompt, keyboard=settings_support_input_keyboard())


async def _save_support_settings(
    context: RouterContext,
    *,
    support_username: str | None,
    support_description: str | None,
) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return

    repository = SupportSettingsRepository(_database_path())
    current = _support_settings()
    if support_username is not None and not support_username.strip():
        await context.send_text("Введите username в формате @username 🙏", keyboard=settings_support_input_keyboard())
        return
    if support_description is not None and not support_description.strip():
        await context.send_text("📝 Текст поддержки не может быть пустым. Введите текст поддержки:", keyboard=settings_support_input_keyboard())
        return

    username = support_username.strip() if support_username is not None else current.support_username
    description = support_description.strip() if support_description is not None else current.support_description
    try:
        repository.upsert_active(username, description)
    except ValueError:
        await context.send_text("Введите username в формате @username 🙏", keyboard=settings_support_input_keyboard())
        return
    _audit(
        context,
        actor_role,
        action="support_settings_updated",
        section="support",
        metadata={"field": "username" if support_username is not None else "description"},
    )
    await context.send_text("✅ Настройки поддержки обновлены")
    await _show_support_editor(context)


def _support_settings():
    return effective_support_settings(SupportSettingsRepository(_database_path()).get_active())


def _payload_suffix(context: RouterContext, prefix: str) -> str:
    payload = context.event.callback_payload or ""
    return payload[len(prefix):] if payload.startswith(prefix) else ""


def _map_meta(map_key: str) -> dict[str, str] | None:
    return {
        "yandex": {
            "label": "Яндекс Карты",
            "prompt_name": "Яндекс Карт",
            "url_field": "yandex_maps_url",
            "enabled_field": "yandex_maps_enabled",
            "screen": state.SETTINGS_CONTACTS_EDIT_YANDEX_MAPS_SCREEN,
        },
        "twogis": {
            "label": "2GIS",
            "prompt_name": "2GIS",
            "url_field": "twogis_url",
            "enabled_field": "twogis_enabled",
            "screen": state.SETTINGS_CONTACTS_EDIT_TWOGIS_SCREEN,
        },
        "google": {
            "label": "Google Maps",
            "prompt_name": "Google Maps",
            "url_field": "google_maps_url",
            "enabled_field": "google_maps_enabled",
            "screen": state.SETTINGS_CONTACTS_EDIT_GOOGLE_MAPS_SCREEN,
        },
    }.get(map_key)


def _map_enabled(raw: dict[str, object] | None, map_key: str) -> bool:
    meta = _map_meta(map_key)
    if meta is None:
        return True
    value = (raw or {}).get(meta["enabled_field"])
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _map_status(raw: dict[str, object] | None, map_key: str) -> str:
    return "включено" if _map_enabled(raw, map_key) else "выключено"


async def _show_contacts_map_editor(context: RouterContext, map_key: str) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    await _answer_callback_if_needed(context)
    override = YClientsSettingsRepository(_database_path()).get_contacts_override()
    enabled = _map_enabled(override, map_key)
    url = str(override.get(meta["url_field"]) or "").strip() or "—"
    text = (
        f"🗺 {meta['label']}\n\n"
        f"Ссылка: {url}\n"
        f"Статус: {'включено' if enabled else 'выключено'}"
    )
    state.set_current_screen(context.event.platform_user_id, _settings_state_chat_id(context), state.SETTINGS_CONTACTS_MAP_SCREEN)
    await context.send_text(text, keyboard=settings_contacts_map_keyboard(map_key=map_key, enabled=enabled))


async def _set_contacts_map_enabled(context: RouterContext, map_key: str, *, enabled: bool) -> None:
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    repo = YClientsSettingsRepository(_database_path())
    override = repo.get_contacts_override()
    override[meta["enabled_field"]] = enabled
    repo.set_contacts_override(override)
    await _show_contacts_map_editor(context, map_key)


async def _save_contact_map_url(context: RouterContext, map_key: str, value: str) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    meta = _map_meta(map_key)
    if meta is None:
        await _show_contacts_editor(context)
        return
    cleaned_value = value.strip()
    if not cleaned_value or not cleaned_value.startswith(("http://", "https://")):
        await context.send_text(
            "Введите корректную ссылку, которая начинается с http:// или https:// 🙏",
            keyboard=settings_contacts_input_keyboard(),
        )
        return
    repo = YClientsSettingsRepository(_database_path())
    override = repo.get_contacts_override()
    override[meta["url_field"]] = cleaned_value
    override[meta["enabled_field"]] = True
    repo.set_contacts_override(override)
    await _show_contacts_map_editor(context, map_key)


def _render_contacts_preview(contacts: ContactInfo) -> str:
    return (
        "📍 Контакты Барбершоп\n\n"
        f"🏠 Адрес: {contacts.address or '—'}\n"
        f"📞 Телефон: {contacts.phone or '—'}\n"
        f"⏰ Режим работы: {contacts.schedule or '—'}\n\n"
        "🗺 Карты:\n"
        f"Яндекс Карты: {_map_status(contacts.raw, 'yandex')}\n"
        f"2GIS: {_map_status(contacts.raw, 'twogis')}\n"
        f"Google Maps: {_map_status(contacts.raw, 'google')}"
    )


async def _start_contacts_edit(context: RouterContext, screen_id: str, prompt: str) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    state.set_current_screen(context.event.platform_user_id, _settings_state_chat_id(context), screen_id)
    await context.send_text(prompt, keyboard=settings_contacts_input_keyboard())


async def _save_contact_field(context: RouterContext, *, field: str, value: str) -> None:
    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return

    cleaned_value = value.strip()
    if not cleaned_value:
        await context.send_text(_contacts_field_empty_text(field), keyboard=settings_contacts_input_keyboard())
        return

    settings_repository = YClientsSettingsRepository(_database_path())
    settings_repository.update_contacts_override_field(field, cleaned_value)
    _audit(
        context,
        actor_role,
        action=_contacts_field_audit_action(field),
        section="contacts",
        metadata={"field": field},
    )
    await context.send_text(_contacts_field_success_text(field))
    await _show_contacts_editor(context)


def _contacts_field_audit_action(field: str) -> str:
    if field == "address":
        return "contacts_override_address_updated"
    if field == "phone":
        return "contacts_override_phone_updated"
    if field == "schedule":
        return "contacts_override_schedule_updated"
    return "contacts_override_updated"


def _contacts_field_empty_text(field: str) -> str:
    if field == "address":
        return "🏠 Адрес не может быть пустым. Введите новый адрес:"
    if field == "phone":
        return "📞 Телефон не может быть пустым. Введите новый телефон:"
    if field == "schedule":
        return "⏰ Режим работы не может быть пустым. Введите новый режим работы:"
    return "✍️ Значение не может быть пустым. Введите текст:"


def _contacts_field_success_text(field: str) -> str:
    if field == "address":
        return "✅ Адрес обновлён"
    if field == "phone":
        return "✅ Телефон обновлён"
    if field == "schedule":
        return "✅ Режим работы обновлён"
    return "✅ Контакты обновлены"


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return ROLE_USER
    db_role = StaffRolesRepository(_database_path()).get_highest_role(platform_user_id, platform=PLATFORM_MAX)
    return effective_role(
        db_role,
        platform_user_id=platform_user_id,
        dev_max_user_id=getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    chat_id = _settings_state_chat_id(context)
    current = state.get_current_screen(context.event.platform_user_id, chat_id)
    if current != screen_id:
        state.push_screen(context.event.platform_user_id, chat_id, current)
    state.set_current_screen(context.event.platform_user_id, chat_id, screen_id)


def _settings_state_chat_id(context: RouterContext) -> str | None:
    if context.event.chat_id is not None:
        return context.event.chat_id

    candidate_screens = (
        state.SETTINGS_CONTACTS_SCREEN,
        state.SETTINGS_CONTACTS_MAP_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_YANDEX_MAPS_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_TWOGIS_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_GOOGLE_MAPS_SCREEN,
        state.SETTINGS_MENU_SCREEN,
    )
    for screen_id in candidate_screens:
        chat_id = state.find_chat_id_for_current_screen(context.event.platform_user_id, screen_id)
        if chat_id is not None:
            return chat_id
    return None


def _audit(context: RouterContext, actor_role: str, *, action: str, section: str, target_platform_user_id: str | None = None, metadata: dict[str, object] | None = None) -> None:
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action=action,
        section=section,
        target_platform_user_id=target_platform_user_id,
        metadata=metadata,
    )


def _load_log_lines(limit: int = LOG_LINES_LIMIT) -> list[str] | None:
    try:
        rows = DiagnosticsRepository(_database_path()).get_recent_bot_logs(limit)
    except Exception:
        return None
    if not rows:
        return []
    ordered = list(reversed(rows))
    return [_format_log_line(row) for row in ordered]


def _format_log_line(row: dict[str, object]) -> str:
    ts = str(row.get("ts_utc") or "—").strip()
    level = str(row.get("level") or "INFO").strip()
    source = str(row.get("source") or "bot").strip()
    message = str(row.get("message") or "").strip()
    return sanitize_text(f"{ts} | {level} | {source} | {message}")


def _chunk_lines(lines: list[str], limit: int = LOG_CHUNK_LIMIT) -> list[str]:
    if not lines:
        return ["Логи пока пустые."]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        safe_line = line if line else " "
        line_len = len(safe_line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [safe_line]
            current_len = line_len
            continue
        if not current and line_len > limit:
            chunks.append(safe_line[:limit])
            continue
        current.append(safe_line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or ["Логи пока пустые."]


async def _send_logs_page(context: RouterContext, page_index: int) -> None:
    pages_value = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGES_KEY)
    pages = pages_value if isinstance(pages_value, list) else None
    if not pages:
        lines = _load_log_lines()
        if lines is None:
            await context.send_text("⚠️ Не удалось получить логи. Проверьте права/путь к логам.", keyboard=settings_diagnostics_keyboard())
            return
        pages = _chunk_lines(lines)
    total = len(pages)
    safe_page = min(max(page_index, 0), total - 1)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGES_KEY, pages)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, STATE_LOG_PAGE_INDEX_KEY, safe_page)
    await context.send_text(
        "🧾 Логи бота\n"
        f"Строки: последние {LOG_LINES_LIMIT}\n"
        f"Страница {safe_page + 1}/{total}\n\n"
        f"{str(pages[safe_page])}",
        keyboard=_logs_nav_keyboard(safe_page, total),
    )


def _logs_nav_keyboard(current_page: int, pages_count: int) -> MaxInlineKeyboard:
    prev_payload = DEV_DIAGNOSTICS_LOGS_PREV_PAYLOAD if current_page > 0 else DEV_DIAGNOSTICS_NOOP_PAYLOAD
    next_payload = DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD if current_page < pages_count - 1 else DEV_DIAGNOSTICS_NOOP_PAYLOAD
    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="◀️ Предыдущая" if current_page > 0 else "⏺", payload=prev_payload),
                MaxButton(text="▶️ Следующая" if current_page < pages_count - 1 else "⏺", payload=next_payload),
            ],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_DIAGNOSTICS_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def _build_logs_csv_text(lines: list[str]) -> str:
    rows = ["timestamp,level,message"]
    for line in lines:
        timestamp, level, message = _extract_timestamp_and_level(line)
        rows.append(",".join([timestamp, level, message]))
    return "\n".join(rows)


def _extract_timestamp_and_level(raw_line: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in raw_line.split("|", 3)]
    if len(parts) >= 4:
        return parts[0], parts[1], parts[3]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return "", "", raw_line


def _render_user_events(rows: list[dict[str, object]], *, summary: object | None = None) -> str:
    if not rows:
        return "Ничего не найдено 🙏"
    lines: list[str] = []
    if summary is not None:
        total_7d = getattr(summary, "total_7d", 0)
        last_activity = getattr(summary, "last_activity", None)
        top_buttons = getattr(summary, "top_buttons", [])
        lines.extend(
            [
                f"Всего событий за 7 дней: {total_7d}",
                f"Последняя активность: {last_activity or '—'}",
                "Топ действий: " + (", ".join(f"{name}×{count}" for name, count in top_buttons) or "—"),
                "",
            ]
        )
    for row in rows[:20]:
        lines.append(
            "• "
            f"{row.get('ts_utc') or '—'} | {row.get('event_type') or '—'} | "
            f"{row.get('event_name') or '—'} | screen={row.get('screen') or '—'} | "
            f"user={row.get('platform_user_id') or '—'} @{row.get('username') or '—'}"
        )
    if len(rows) > 20:
        lines.append(f"\nПоказаны 20 из {len(rows)} событий.")
    return _short("\n".join(lines), 3300)


async def _send_file_to_current_chat(context: RouterContext, content: bytes, *, filename: str, caption: str) -> bool:
    try:
        chat_id = _int_or_none(context.event.chat_id)
        if chat_id is not None:
            result = await context.sender.send_file_bytes_to_chat(
                chat_id,
                content,
                filename=filename,
                text=caption,
                keyboard=settings_diagnostics_keyboard(),
            )
            return result.ok
        user_id = _int_or_none(context.event.max_user_id or context.event.platform_user_id)
        if user_id is not None:
            result = await context.sender.send_file_bytes_to_user(
                user_id,
                content,
                filename=filename,
                text=caption,
                keyboard=settings_diagnostics_keyboard(),
            )
            return result.ok
    except Exception:
        return False
    return False


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _short(text: str, limit: int) -> str:
    safe = sanitize_text(text)
    return safe if len(safe) <= limit else safe[:limit] + "…"


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, SETTINGS_NO_ACCESS_TEXT)
    await context.send_text(SETTINGS_NO_ACCESS_TEXT)


async def _send_dev_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, DEV_DIAGNOSTICS_NO_ACCESS_TEXT)
    await context.send_text(DEV_DIAGNOSTICS_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str | None = None) -> None:
    if context.event.callback_id:
        await context.answer_callback()


def _bool_env(name: str, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


def _is_protected_developer(context: RouterContext) -> bool:
    return is_protected_developer(
        context.event.platform_user_id,
        getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )
