"""Admin settings hub for the MAX bot."""

from __future__ import annotations

from os import getenv

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
from max_barbershop_bot.flows.staff import handle_staff_menu
from max_barbershop_bot.flows.yclients_settings import handle_connection_check, handle_yclients_menu
from max_barbershop_bot.flows.support import render_support_message
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.support_settings import SupportSettingsRepository, build_support_url, display_support_username, effective_support_settings
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactInfo, ContactsService
from max_barbershop_bot.services.navigation import go_back, show_home
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.ui.buttons import (
    ADMIN_SETTINGS_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    DEV_DIAGNOSTICS_FAILED_NOTIFICATIONS_PAYLOAD,
    DEV_DIAGNOSTICS_REFRESH_PAYLOAD,
    SETTINGS_CONTACTS_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD,
    SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD,
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
    SETTINGS_ROLES_PAYLOAD,
    SETTINGS_YCLIENTS_PAYLOAD,
    settings_contacts_input_keyboard,
    settings_contacts_keyboard,
    settings_diagnostics_keyboard,
    settings_menu_keyboard,
    settings_notifications_keyboard,
    settings_status_keyboard,
    settings_support_input_keyboard,
    settings_support_keyboard,
)
from max_barbershop_bot.ui.texts import (
    SETTINGS_MENU_TEXT,
    SETTINGS_NO_ACCESS_TEXT,
    SETTINGS_NOTIFICATIONS_EDIT_SOON_TEXT,
)
from max_barbershop_bot.services.developer_diagnostics import (
    NO_ACCESS_TEXT as DEV_DIAGNOSTICS_NO_ACCESS_TEXT,
    build_developer_diagnostics_text,
)


def register_settings_routes(router: Router) -> None:
    """Register the settings hub and its lightweight subsections."""

    router.on_callback(ADMIN_SETTINGS_PAYLOAD, handle_settings_menu)
    router.on_callback(SETTINGS_YCLIENTS_PAYLOAD, handle_settings_yclients)
    router.on_callback(SETTINGS_CONTACTS_PAYLOAD, handle_settings_contacts)
    router.on_callback(SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD, handle_settings_contacts_edit_address)
    router.on_callback(SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD, handle_settings_contacts_edit_phone)
    router.on_callback(SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD, handle_settings_contacts_edit_schedule)
    router.on_callback(SETTINGS_CONTACTS_PREVIEW_PAYLOAD, handle_settings_contacts_preview)
    router.on_callback(SETTINGS_CONTACTS_RESET_PAYLOAD, handle_settings_contacts_reset)
    router.on_callback(SETTINGS_SUPPORT_PAYLOAD, handle_settings_support)
    router.on_callback(SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD, handle_settings_support_edit_username)
    router.on_callback(SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD, handle_settings_support_edit_description)
    router.on_callback(SETTINGS_SUPPORT_PREVIEW_PAYLOAD, handle_settings_support_preview)
    router.on_callback(SETTINGS_NOTIFICATIONS_PAYLOAD, handle_settings_notifications)
    router.on_callback(SETTINGS_ROLES_PAYLOAD, handle_settings_roles)
    router.on_callback(SETTINGS_DIAGNOSTICS_PAYLOAD, handle_settings_diagnostics)
    router.on_callback(DEV_DIAGNOSTICS_REFRESH_PAYLOAD, handle_settings_diagnostics_refresh)
    router.on_callback(DEV_DIAGNOSTICS_FAILED_NOTIFICATIONS_PAYLOAD, handle_settings_diagnostics_failed_notifications)
    router.on_callback(SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD, handle_settings_notification_history)
    router.on_callback(SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD, handle_settings_yclients_check)
    router.on_callback(SETTINGS_BACK_PAYLOAD, handle_settings_back)
    router.on_callback(SETTINGS_HOME_PAYLOAD, handle_settings_home)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN, handle_settings_contacts_address_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN, handle_settings_contacts_phone_input)
    router.on_screen_text(state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN, handle_settings_contacts_schedule_input)
    router.on_screen_text(state.SETTINGS_SUPPORT_EDIT_USERNAME_SCREEN, handle_settings_support_username_input)
    router.on_screen_text(state.SETTINGS_SUPPORT_EDIT_DESCRIPTION_SCREEN, handle_settings_support_description_input)


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
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_NOTIFICATIONS_SCREEN)
    reminders_enabled = _bool_env("REMINDERS_ENABLED", DEFAULT_REMINDERS_ENABLED)
    poll_interval_seconds = _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    enabled_label = "✅ Включены" if reminders_enabled else "❌ Выключены"
    text = (
        "🔔 Уведомления\n\n"
        f"Текущий статус: {enabled_label}\n"
        f"Интервал проверки: {poll_interval_seconds} сек.\n\n"
        "Поддерживаемые уведомления:\n"
        "• ✅ Подтверждение записи сразу\n"
        "• ⏰ Напоминание за 48 часов\n"
        "• ⏰ Напоминание за 6 часов\n"
        "• ⏰ Напоминание о записи (2 часа)\n\n"
        f"{SETTINGS_NOTIFICATIONS_EDIT_SOON_TEXT}"
    )
    _audit(
        context,
        actor_role,
        action="settings_section_opened",
        section="notifications",
        metadata={"reminders_enabled": reminders_enabled, "poll_interval_seconds": poll_interval_seconds},
    )
    await context.send_text(text, keyboard=settings_notifications_keyboard())


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
    if current in {state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN, state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN, state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN}:
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
        f"⏰ Режим работы: {contacts.schedule or '—'}"
    )
    state.set_current_screen(context.event.platform_user_id, _settings_state_chat_id(context), state.SETTINGS_CONTACTS_SCREEN)
    await context.send_text(text, keyboard=settings_contacts_keyboard())


async def _show_support_editor(context: RouterContext) -> None:
    settings = _support_settings()
    username = display_support_username(settings.support_username) or "—"
    support_url = build_support_url(settings.support_username) or "—"
    text = (
        "🆘 Редактирование поддержки\n\n"
        f"👤 Username: {username}\n"
        f"🔗 Ссылка: {support_url}\n"
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


def _render_contacts_preview(contacts: ContactInfo) -> str:
    return (
        "📍 Контакты Барбершоп\n\n"
        f"🏠 Адрес: {contacts.address or '—'}\n"
        f"📞 Телефон: {contacts.phone or '—'}\n"
        f"⏰ Режим работы: {contacts.schedule or '—'}"
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
        state.SETTINGS_CONTACTS_EDIT_ADDRESS_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_PHONE_SCREEN,
        state.SETTINGS_CONTACTS_EDIT_SCHEDULE_SCREEN,
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
