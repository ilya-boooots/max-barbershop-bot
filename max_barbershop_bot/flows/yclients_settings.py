"""YClients integration settings flow for the MAX bot."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import ROLE_USER, can_view_yclients
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import DEFAULT_BRANCH_TIMEZONE, YClientsSettings, YClientsSettingsRepository
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.services.yclients_settings import (
    check_yclients_connection,
    is_configured,
    normalize_optional_text,
    normalize_required_text,
    normalize_support_timezone,
)
from max_barbershop_bot.ui.buttons import (
    ADMIN_YCLIENTS_PAYLOAD,
    YCLIENTS_BACK_PAYLOAD,
    YCLIENTS_CHECK_PAYLOAD,
    YCLIENTS_HOME_PAYLOAD,
    YCLIENTS_SAVE_PAYLOAD,
    YCLIENTS_SETUP_PAYLOAD,
    YCLIENTS_SKIP_BRANCH_TITLE_PAYLOAD,
    yclients_confirm_keyboard,
    yclients_settings_keyboard,
    yclients_setup_navigation_keyboard,
)
from max_barbershop_bot.ui.texts import (
    YCLIENTS_BRANCH_TITLE_TEXT,
    YCLIENTS_CHECK_FAILURE_TEXT,
    YCLIENTS_CHECK_SUCCESS_TEXT,
    YCLIENTS_COMPANY_ID_TEXT,
    YCLIENTS_CONFIRM_TEXT,
    YCLIENTS_INVALID_REQUIRED_TEXT,
    YCLIENTS_INVALID_TIMEZONE_TEXT,
    YCLIENTS_NO_ACCESS_TEXT,
    YCLIENTS_NOT_CONFIGURED_TEXT,
    YCLIENTS_PARTNER_TOKEN_TEXT,
    YCLIENTS_SETTINGS_SAVED_TEXT,
    YCLIENTS_TIMEZONE_TEXT,
    YCLIENTS_USER_TOKEN_TEXT,
)

logger = logging.getLogger(__name__)

_COMPANY_ID_KEY = "yclients_company_id"
_PARTNER_TOKEN_KEY = "yclients_partner_token"
_USER_TOKEN_KEY = "yclients_user_token"
_BRANCH_TIMEZONE_KEY = "yclients_branch_timezone"
_BRANCH_TITLE_KEY = "yclients_branch_title"

_SETUP_PREVIOUS_SCREEN = {
    state.YCLIENTS_SETUP_COMPANY_ID_SCREEN: state.YCLIENTS_SETTINGS_MENU_SCREEN,
    state.YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN: state.YCLIENTS_SETUP_COMPANY_ID_SCREEN,
    state.YCLIENTS_SETUP_USER_TOKEN_SCREEN: state.YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN,
    state.YCLIENTS_SETUP_TIMEZONE_SCREEN: state.YCLIENTS_SETUP_USER_TOKEN_SCREEN,
    state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN: state.YCLIENTS_SETUP_TIMEZONE_SCREEN,
    state.YCLIENTS_SETUP_CONFIRM_SCREEN: state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN,
}


def register_yclients_settings_routes(router: Router) -> None:
    """Register YClients settings callbacks and text wizard steps."""

    router.on_callback(ADMIN_YCLIENTS_PAYLOAD, handle_yclients_menu)
    router.on_callback(YCLIENTS_SETUP_PAYLOAD, handle_setup_start)
    router.on_callback(YCLIENTS_CHECK_PAYLOAD, handle_connection_check)
    router.on_callback(YCLIENTS_SAVE_PAYLOAD, handle_save_settings)
    router.on_callback(YCLIENTS_SKIP_BRANCH_TITLE_PAYLOAD, handle_skip_branch_title)
    router.on_callback(YCLIENTS_BACK_PAYLOAD, handle_yclients_back)
    router.on_callback(YCLIENTS_HOME_PAYLOAD, handle_yclients_home)
    router.on_screen_text(state.YCLIENTS_SETUP_COMPANY_ID_SCREEN, handle_company_id_input)
    router.on_screen_text(state.YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN, handle_partner_token_input)
    router.on_screen_text(state.YCLIENTS_SETUP_USER_TOKEN_SCREEN, handle_user_token_input)
    router.on_screen_text(state.YCLIENTS_SETUP_TIMEZONE_SCREEN, handle_timezone_input)
    router.on_screen_text(state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN, handle_branch_title_input)


async def handle_yclients_menu(context: RouterContext) -> None:
    """Open the YClients integration settings screen."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем YClients 🧩")
    _push_current_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await _show_settings_menu(context)


async def handle_setup_start(context: RouterContext) -> None:
    """Start the credentials setup wizard."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Настраиваем подключение ⚙️")
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    _set_screen(context, state.YCLIENTS_SETUP_COMPANY_ID_SCREEN)
    await context.send_text(YCLIENTS_COMPANY_ID_TEXT, keyboard=yclients_setup_navigation_keyboard())


async def handle_company_id_input(context: RouterContext) -> None:
    """Store company_id and ask for partner token."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    value = normalize_required_text(context.event.text)
    if value is None:
        await context.send_text(YCLIENTS_INVALID_REQUIRED_TEXT, keyboard=yclients_setup_navigation_keyboard())
        return
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _COMPANY_ID_KEY, value)
    _set_screen(context, state.YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN)
    await context.send_text(YCLIENTS_PARTNER_TOKEN_TEXT, keyboard=yclients_setup_navigation_keyboard())


async def handle_partner_token_input(context: RouterContext) -> None:
    """Store partner token in temporary memory and ask for user token."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    value = normalize_required_text(context.event.text)
    if value is None:
        await context.send_text(YCLIENTS_INVALID_REQUIRED_TEXT, keyboard=yclients_setup_navigation_keyboard())
        return
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _PARTNER_TOKEN_KEY, value)
    _set_screen(context, state.YCLIENTS_SETUP_USER_TOKEN_SCREEN)
    await context.send_text(YCLIENTS_USER_TOKEN_TEXT, keyboard=yclients_setup_navigation_keyboard())


async def handle_user_token_input(context: RouterContext) -> None:
    """Store user token in temporary memory and ask for branch timezone."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    value = normalize_required_text(context.event.text)
    if value is None:
        await context.send_text(YCLIENTS_INVALID_REQUIRED_TEXT, keyboard=yclients_setup_navigation_keyboard())
        return
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _USER_TOKEN_KEY, value)
    _set_screen(context, state.YCLIENTS_SETUP_TIMEZONE_SCREEN)
    await context.send_text(YCLIENTS_TIMEZONE_TEXT, keyboard=yclients_setup_navigation_keyboard())


async def handle_timezone_input(context: RouterContext) -> None:
    """Validate timezone and ask for optional branch title."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    try:
        branch_timezone = normalize_support_timezone(context.event.text)
    except ValueError:
        await context.send_text(YCLIENTS_INVALID_TIMEZONE_TEXT, keyboard=yclients_setup_navigation_keyboard())
        return
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _BRANCH_TIMEZONE_KEY, branch_timezone)
    _set_screen(context, state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN)
    await context.send_text(YCLIENTS_BRANCH_TITLE_TEXT, keyboard=yclients_setup_navigation_keyboard(include_skip=True))


async def handle_branch_title_input(context: RouterContext) -> None:
    """Store optional branch title and show final summary."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _BRANCH_TITLE_KEY,
        normalize_optional_text(context.event.text),
    )
    await _show_confirm(context)


async def handle_skip_branch_title(context: RouterContext) -> None:
    """Skip optional branch title and show final summary."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Пропускаем название филиала ⏭️")
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _BRANCH_TITLE_KEY, None)
    await _show_confirm(context)


async def handle_save_settings(context: RouterContext) -> None:
    """Persist YClients settings only after final confirmation."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    draft = _draft_from_state(context)
    if draft is None:
        await _answer_callback_if_needed(context, "Не хватает данных 🙏")
        state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
        _set_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
        await context.send_text("Не хватает данных подключения 🙏\n\nНачните настройку заново.", keyboard=yclients_settings_keyboard())
        return

    _settings_repository().upsert_active_settings(
        company_id=draft["company_id"],
        partner_token=draft["partner_token"],
        user_token=draft["user_token"],
        branch_timezone=draft["branch_timezone"],
        branch_title=draft["branch_title"],
        is_active=True,
    )
    logger.info(
        "YClients settings saved: operation=save_yclients_settings company_id=%s branch_timezone=%s branch_title_present=%s",
        draft["company_id"],
        draft["branch_timezone"],
        bool(draft["branch_title"]),
    )
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await _answer_callback_if_needed(context, YCLIENTS_SETTINGS_SAVED_TEXT)
    await context.send_text(YCLIENTS_SETTINGS_SAVED_TEXT)
    _set_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
    await _show_settings_menu(context, push=False)


async def handle_connection_check(context: RouterContext) -> None:
    """Run a safe read-only YClients connection check."""

    if not _can_access(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Проверяем подключение 🔍")
    settings = _settings_repository().get_active()
    if not is_configured(settings):
        await context.send_text(YCLIENTS_NOT_CONFIGURED_TEXT, keyboard=yclients_settings_keyboard())
        return

    result = await check_yclients_connection(settings)
    if result.ok:
        await context.send_text(
            YCLIENTS_CHECK_SUCCESS_TEXT.format(branch_title_or_company_id=_branch_title_or_company_id(settings)),
            keyboard=yclients_settings_keyboard(),
        )
        return

    logger.warning(
        "YClients settings check failed: operation=check_yclients_connection company_id=%s error_class=%s status_code=%s",
        settings.company_id if settings else None,
        result.short_message,
        result.status_code,
    )
    await context.send_text(YCLIENTS_CHECK_FAILURE_TEXT, keyboard=yclients_settings_keyboard())


async def handle_yclients_back(context: RouterContext) -> None:
    """Navigate backward inside the YClients settings flow."""

    await _answer_callback_if_needed(context, "Возвращаемся назад ⬅️")
    current = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current in _SETUP_PREVIOUS_SCREEN:
        previous = _SETUP_PREVIOUS_SCREEN[current]
        if previous == state.YCLIENTS_SETTINGS_MENU_SCREEN:
            state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
            _set_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
            await _show_settings_menu(context, push=False)
            return
        _set_screen(context, previous)
        await _show_setup_step(context, previous)
        return

    previous_screen = state.pop_previous_screen(context.event.platform_user_id, context.event.chat_id)
    if previous_screen and previous_screen != state.YCLIENTS_SETTINGS_MENU_SCREEN:
        from max_barbershop_bot.services.navigation import render_screen

        await render_screen(context, previous_screen)
        return
    await show_home(context)


async def handle_yclients_home(context: RouterContext) -> None:
    """Discard temporary setup data and return to the role-based main menu."""

    await _answer_callback_if_needed(context, "Открываем главное меню 🏠")
    await show_home(context)


async def _show_settings_menu(context: RouterContext, *, push: bool = False) -> None:
    if push:
        _push_current_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
    else:
        _set_screen(context, state.YCLIENTS_SETTINGS_MENU_SCREEN)
    settings = _settings_repository().get_active()
    await context.send_text(_settings_status_text(settings), keyboard=yclients_settings_keyboard())


async def _show_setup_step(context: RouterContext, screen_id: str) -> None:
    text = {
        state.YCLIENTS_SETUP_COMPANY_ID_SCREEN: YCLIENTS_COMPANY_ID_TEXT,
        state.YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN: YCLIENTS_PARTNER_TOKEN_TEXT,
        state.YCLIENTS_SETUP_USER_TOKEN_SCREEN: YCLIENTS_USER_TOKEN_TEXT,
        state.YCLIENTS_SETUP_TIMEZONE_SCREEN: YCLIENTS_TIMEZONE_TEXT,
        state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN: YCLIENTS_BRANCH_TITLE_TEXT,
    }.get(screen_id)
    if text is None:
        await _show_confirm(context)
        return
    await context.send_text(
        text,
        keyboard=yclients_setup_navigation_keyboard(include_skip=screen_id == state.YCLIENTS_SETUP_BRANCH_TITLE_SCREEN),
    )


async def _show_confirm(context: RouterContext) -> None:
    draft = _draft_from_state(context)
    if draft is None:
        _set_screen(context, state.YCLIENTS_SETUP_COMPANY_ID_SCREEN)
        await context.send_text("Не хватает данных подключения 🙏\n\nНачните настройку заново.", keyboard=yclients_setup_navigation_keyboard())
        return
    _set_screen(context, state.YCLIENTS_SETUP_CONFIRM_SCREEN)
    await context.send_text(
        YCLIENTS_CONFIRM_TEXT.format(
            company_id=draft["company_id"],
            branch_title=draft["branch_title"] or "—",
            branch_timezone=draft["branch_timezone"],
        ),
        keyboard=yclients_confirm_keyboard(),
    )


def _draft_from_state(context: RouterContext) -> dict[str, str | None] | None:
    company_id = _state_text(context, _COMPANY_ID_KEY)
    partner_token = _state_text(context, _PARTNER_TOKEN_KEY)
    user_token = _state_text(context, _USER_TOKEN_KEY)
    branch_timezone = _state_text(context, _BRANCH_TIMEZONE_KEY) or DEFAULT_BRANCH_TIMEZONE
    branch_title = _state_text(context, _BRANCH_TITLE_KEY)
    if not company_id or not partner_token or not user_token:
        return None
    try:
        branch_timezone = normalize_support_timezone(branch_timezone)
    except ValueError:
        branch_timezone = DEFAULT_BRANCH_TIMEZONE
    return {
        "company_id": company_id,
        "partner_token": partner_token,
        "user_token": user_token,
        "branch_timezone": branch_timezone,
        "branch_title": branch_title,
    }


def _state_text(context: RouterContext, key: str) -> str | None:
    value = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, key)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _settings_status_text(settings: YClientsSettings | None) -> str:
    if not is_configured(settings):
        return "🧩 YClients\n\nСтатус подключения: не настроено\n\nВыберите действие:"
    return (
        "🧩 YClients\n\n"
        "Статус подключения: настроено ✅\n"
        f"Филиал: {_branch_title_or_company_id(settings)}\n"
        f"Company ID: {settings.company_id}\n"
        f"Часовой пояс: {settings.branch_timezone or DEFAULT_BRANCH_TIMEZONE}\n\n"
        "Токены сохранены и скрыты 🔐"
    )


def _branch_title_or_company_id(settings: YClientsSettings | None) -> str:
    if settings is None:
        return "—"
    return settings.branch_title or settings.company_id or "—"


def _can_access(context: RouterContext) -> bool:
    return can_view_yclients(_actor_role(context))


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


def _set_screen(context: RouterContext, screen_id: str) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, screen_id)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, YCLIENTS_NO_ACCESS_TEXT)
    await context.send_text(YCLIENTS_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


def _settings_repository() -> YClientsSettingsRepository:
    return YClientsSettingsRepository(_database_path())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
