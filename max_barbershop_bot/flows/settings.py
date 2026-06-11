"""Admin settings hub for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH, DEFAULT_REMINDERS_ENABLED, DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS
from max_barbershop_bot.core.permissions import (
    ROLE_USER,
    can_manage_roles,
    can_view_contacts_settings,
    can_view_diagnostics_settings,
    can_view_notification_settings,
    can_view_settings,
    can_view_yclients_settings,
)
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows.notification_history import handle_notification_history
from max_barbershop_bot.flows.staff import handle_staff_menu
from max_barbershop_bot.flows.yclients_settings import handle_connection_check, handle_yclients_menu
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import has_useful_override
from max_barbershop_bot.services.navigation import go_back, show_home
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.services.yclients_context import load_active_yclients_settings
from max_barbershop_bot.ui.buttons import (
    ADMIN_SETTINGS_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    SETTINGS_CONTACTS_PAYLOAD,
    SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD,
    SETTINGS_DIAGNOSTICS_PAYLOAD,
    SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD,
    SETTINGS_HOME_PAYLOAD,
    SETTINGS_NOTIFICATIONS_PAYLOAD,
    SETTINGS_ROLES_PAYLOAD,
    SETTINGS_YCLIENTS_PAYLOAD,
    settings_diagnostics_keyboard,
    settings_menu_keyboard,
    settings_notifications_keyboard,
    settings_status_keyboard,
)
from max_barbershop_bot.ui.texts import (
    SETTINGS_CONTACTS_EDIT_SOON_TEXT,
    SETTINGS_MENU_TEXT,
    SETTINGS_NO_ACCESS_TEXT,
    SETTINGS_NOTIFICATIONS_EDIT_SOON_TEXT,
)


def register_settings_routes(router: Router) -> None:
    """Register the settings hub and its lightweight subsections."""

    router.on_callback(ADMIN_SETTINGS_PAYLOAD, handle_settings_menu)
    router.on_callback(SETTINGS_YCLIENTS_PAYLOAD, handle_settings_yclients)
    router.on_callback(SETTINGS_CONTACTS_PAYLOAD, handle_settings_contacts)
    router.on_callback(SETTINGS_NOTIFICATIONS_PAYLOAD, handle_settings_notifications)
    router.on_callback(SETTINGS_ROLES_PAYLOAD, handle_settings_roles)
    router.on_callback(SETTINGS_DIAGNOSTICS_PAYLOAD, handle_settings_diagnostics)
    router.on_callback(SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD, handle_settings_notification_history)
    router.on_callback(SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD, handle_settings_yclients_check)
    router.on_callback(SETTINGS_BACK_PAYLOAD, handle_settings_back)
    router.on_callback(SETTINGS_HOME_PAYLOAD, handle_settings_home)


async def handle_settings_menu(context: RouterContext) -> None:
    """Open role-based settings hub."""

    actor_role = _actor_role(context)
    if not can_view_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем настройки ⚙️")
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
    """Show contacts override status without duplicating contacts editing."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем контакты 📍")
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_CONTACTS_SCREEN)
    settings_repository = YClientsSettingsRepository(_database_path())
    override = settings_repository.get_contacts_override()
    manual_override = has_useful_override(override)
    active_settings = load_active_yclients_settings(settings_repository, operation="settings_contacts_status")
    effective_source = _contacts_effective_source(active_settings, manual_override)
    text = (
        "📍 Контакты\n\n"
        f"Источник сейчас: {_contacts_source_label(effective_source)}\n"
        f"Ручная замена: {'✅ настроена' if manual_override else '— не настроена'}\n"
        f"YClients: {'✅ настроен' if _yclients_contacts_ready(active_settings) else '— не настроен'}\n\n"
        "Текущий экран контактов можно открыть отдельной кнопкой ниже.\n\n"
        f"{SETTINGS_CONTACTS_EDIT_SOON_TEXT}"
    )
    _audit(
        context,
        actor_role,
        action="settings_section_opened",
        section="contacts",
        metadata={"manual_override_present": manual_override, "effective_source": effective_source},
    )
    await context.send_text(text, keyboard=settings_status_keyboard(include_contacts=True))


async def handle_settings_notifications(context: RouterContext) -> None:
    """Show notification settings status and link existing history."""

    actor_role = _actor_role(context)
    if not can_view_notification_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем уведомления 🔔")
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
    """Show compact diagnostics entry points."""

    actor_role = _actor_role(context)
    if not can_view_diagnostics_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем диагностику 🛠")
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_DIAGNOSTICS_SCREEN)
    text = (
        "🛠 Диагностика\n\n"
        "Базовый статус: бот запущен ✅\n\n"
        "Доступно:\n"
        "• 🔔 История уведомлений\n"
        "• 🧩 Проверка подключения YClients"
    )
    _audit(context, actor_role, action="settings_section_opened", section="diagnostics")
    await context.send_text(text, keyboard=settings_diagnostics_keyboard())


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
    if not can_view_diagnostics_settings(actor_role):
        await _send_no_access(context)
        return
    _audit(context, actor_role, action="diagnostics_yclients_check_started", section="diagnostics")
    await handle_connection_check(context)


async def handle_settings_back(context: RouterContext) -> None:
    """Return from a settings subsection to the hub, or from hub to previous screen."""

    actor_role = _actor_role(context)
    if not can_view_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Возвращаемся назад ⬅️")
    current = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current in {state.SETTINGS_CONTACTS_SCREEN, state.SETTINGS_NOTIFICATIONS_SCREEN, state.SETTINGS_DIAGNOSTICS_SCREEN}:
        await _show_settings_menu(context, actor_role)
        return
    await go_back(context)


async def handle_settings_home(context: RouterContext) -> None:
    """Return to role-based home menu."""

    await _answer_callback_if_needed(context, "Открываем главное меню 🏠")
    await show_home(context)


async def _show_settings_menu(context: RouterContext, actor_role: str) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MENU_SCREEN)
    await context.send_text(SETTINGS_MENU_TEXT, keyboard=settings_menu_keyboard(actor_role))


def _contacts_effective_source(active_settings: object | None, manual_override: bool) -> str:
    if manual_override:
        return "override"
    if _yclients_contacts_ready(active_settings):
        return "yclients"
    return "fallback"


def _contacts_source_label(source: str) -> str:
    if source == "override":
        return "ручная замена"
    if source == "yclients":
        return "YClients"
    return "не настроено / запасной текст"


def _yclients_contacts_ready(active_settings: object | None) -> bool:
    return bool(active_settings is not None and getattr(active_settings, "company_id", None) and getattr(active_settings, "partner_token", None))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return ROLE_USER
    return StaffRolesRepository(_database_path()).get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current != screen_id:
        state.push_screen(context.event.platform_user_id, context.event.chat_id, current)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, screen_id)


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


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


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
