"""Staff management flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGER,
    ROLE_USER,
    can_assign_role,
    can_manage_roles,
    can_remove_role,
    can_view_staff,
    is_protected_developer,
    normalize_role,
)
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.staff_roles import StaffRole, StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, User, UsersRepository
from max_barbershop_bot.services.registration import mask_phone, normalize_phone
from max_barbershop_bot.services.role_onboarding import notify_role_assigned, notify_role_removed
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.ui.buttons import (
    ADMIN_STAFF_PAYLOAD,
    STAFF_ASSIGN_ADMIN_PAYLOAD,
    STAFF_ASSIGN_DEVELOPER_PAYLOAD,
    STAFF_ASSIGN_MANAGER_PAYLOAD,
    STAFF_ASSIGN_START_PAYLOAD,
    STAFF_LIST_PAYLOAD,
    STAFF_REMOVE_ADMIN_PAYLOAD,
    STAFF_REMOVE_DEVELOPER_PAYLOAD,
    STAFF_REMOVE_MANAGER_PAYLOAD,
    STAFF_REMOVE_START_PAYLOAD,
    navigation_keyboard,
    staff_role_assign_keyboard,
    staff_role_remove_keyboard,
)
from max_barbershop_bot.ui.screens import staff_menu_screen
from max_barbershop_bot.ui.texts import (
    STAFF_ASSIGN_IDENTIFIER_TEXT,
    STAFF_ASSIGN_ROLE_TEXT,
    STAFF_LIST_EMPTY_TEXT,
    STAFF_NO_ACCESS_TEXT,
    STAFF_NO_EXTRA_ROLES_TEXT,
    STAFF_REMOVE_IDENTIFIER_TEXT,
    STAFF_ROLE_ASSIGNED_TEXT,
    STAFF_ROLE_REMOVED_TEXT,
    STAFF_USER_NOT_FOUND_TEXT,
)

_TARGET_PLATFORM_USER_ID_KEY = "target_platform_user_id"
_TARGET_DISPLAY_NAME_KEY = "target_display_name"
_ASSIGN_PAYLOAD_ROLES = {
    STAFF_ASSIGN_MANAGER_PAYLOAD: ROLE_MANAGER,
    STAFF_ASSIGN_ADMIN_PAYLOAD: ROLE_ADMIN,
    STAFF_ASSIGN_DEVELOPER_PAYLOAD: ROLE_DEVELOPER,
}
_REMOVE_PAYLOAD_ROLES = {
    STAFF_REMOVE_MANAGER_PAYLOAD: ROLE_MANAGER,
    STAFF_REMOVE_ADMIN_PAYLOAD: ROLE_ADMIN,
    STAFF_REMOVE_DEVELOPER_PAYLOAD: ROLE_DEVELOPER,
}
_ROLE_EMOJI = {
    ROLE_DEVELOPER: "👑",
    ROLE_ADMIN: "🛡",
    ROLE_MANAGER: "👔",
    ROLE_USER: "👤",
}


def register_staff_routes(router: Router) -> None:
    """Register staff management callbacks and text steps."""

    router.on_callback(ADMIN_STAFF_PAYLOAD, handle_staff_menu)
    router.on_callback(STAFF_LIST_PAYLOAD, handle_staff_list)
    router.on_callback(STAFF_ASSIGN_START_PAYLOAD, handle_assign_start)
    router.on_callback(STAFF_REMOVE_START_PAYLOAD, handle_remove_start)
    for payload in _ASSIGN_PAYLOAD_ROLES:
        router.on_callback(payload, handle_assign_role)
    for payload in _REMOVE_PAYLOAD_ROLES:
        router.on_callback(payload, handle_remove_role)
    router.on_screen_text(state.STAFF_ASSIGN_IDENTIFIER_SCREEN, handle_assign_identifier)
    router.on_screen_text(state.STAFF_REMOVE_IDENTIFIER_SCREEN, handle_remove_identifier)


async def handle_staff_menu(context: RouterContext) -> None:
    """Open staff management menu when the actor can view it."""

    actor_role = _actor_role(context)
    if not can_view_staff(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем раздел персонала 👥")
    _push_current_screen(context, state.STAFF_MENU_SCREEN)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await _show_staff_menu(context)


async def handle_staff_list(context: RouterContext) -> None:
    """Render current staff role assignments."""

    actor_role = _actor_role(context)
    if not can_view_staff(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Показываем список сотрудников 📋")
    _push_current_screen(context, state.STAFF_LIST_SCREEN)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_LIST_SCREEN)
    await context.send_text(_build_staff_list_text(), keyboard=navigation_keyboard())


async def handle_assign_start(context: RouterContext) -> None:
    """Ask for target user identifier before assigning a role."""

    actor_role = _actor_role(context)
    if not can_manage_roles(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Введите пользователя 👇")
    _push_current_screen(context, state.STAFF_ASSIGN_IDENTIFIER_SCREEN)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(STAFF_ASSIGN_IDENTIFIER_TEXT, keyboard=navigation_keyboard())


async def handle_assign_identifier(context: RouterContext) -> None:
    """Resolve target user and ask which role should be assigned."""

    actor_role = _actor_role(context)
    if not can_manage_roles(actor_role):
        await _send_no_access(context)
        return
    target = _find_user(context.event.text)
    if target is None:
        await context.send_text(STAFF_USER_NOT_FOUND_TEXT, keyboard=navigation_keyboard())
        return
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _TARGET_PLATFORM_USER_ID_KEY,
        target.platform_user_id,
    )
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _TARGET_DISPLAY_NAME_KEY,
        _display_name(target),
    )
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_ASSIGN_ROLE_SCREEN)
    await context.send_text(STAFF_ASSIGN_ROLE_TEXT, keyboard=staff_role_assign_keyboard(actor_role))


async def handle_assign_role(context: RouterContext) -> None:
    """Assign the selected role and notify the target user."""

    new_role = _ASSIGN_PAYLOAD_ROLES.get(context.event.callback_payload or "")
    if new_role is None:
        return
    actor_role = _actor_role(context)
    target = _target_from_state(context)
    if target is None:
        await _answer_callback_if_needed(context, "Данные потеряны")
        await context.send_text(STAFF_USER_NOT_FOUND_TEXT, keyboard=navigation_keyboard())
        return
    if not can_assign_role(actor_role, new_role):
        await _send_no_access(context)
        return
    if _is_protected_target(target) and new_role != ROLE_DEVELOPER:
        await _send_no_access(context)
        return

    _staff_repository().assign_role(
        target.platform_user_id,
        new_role,
        assigned_by_platform_user_id=context.event.platform_user_id,
        platform=PLATFORM_MAX,
    )
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="role_assigned",
        section="roles",
        target_platform_user_id=target.platform_user_id,
        metadata={"role": new_role},
    )
    await _answer_callback_if_needed(context, STAFF_ROLE_ASSIGNED_TEXT)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_MENU_SCREEN)
    await context.send_text(STAFF_ROLE_ASSIGNED_TEXT, keyboard=staff_menu_screen(actor_role).keyboard)
    await notify_role_assigned(context.sender, target, new_role)


async def handle_remove_start(context: RouterContext) -> None:
    """Ask for target user identifier before removing a role."""

    actor_role = _actor_role(context)
    if not can_manage_roles(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Введите пользователя 👇")
    _push_current_screen(context, state.STAFF_REMOVE_IDENTIFIER_SCREEN)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(STAFF_REMOVE_IDENTIFIER_TEXT, keyboard=navigation_keyboard())


async def handle_remove_identifier(context: RouterContext) -> None:
    """Resolve target user and show removable role buttons."""

    actor_role = _actor_role(context)
    if not can_manage_roles(actor_role):
        await _send_no_access(context)
        return
    target = _find_user(context.event.text)
    if target is None:
        await context.send_text(STAFF_USER_NOT_FOUND_TEXT, keyboard=navigation_keyboard())
        return
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _TARGET_PLATFORM_USER_ID_KEY,
        target.platform_user_id,
    )
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _TARGET_DISPLAY_NAME_KEY,
        _display_name(target),
    )
    roles = _removable_roles(actor_role, target)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_REMOVE_ROLE_SCREEN)
    if not roles:
        await context.send_text(STAFF_NO_EXTRA_ROLES_TEXT, keyboard=navigation_keyboard())
        return
    await context.send_text(
        "Какую роль снять у пользователя?",
        keyboard=staff_role_remove_keyboard(roles),
    )


async def handle_remove_role(context: RouterContext) -> None:
    """Remove the selected staff role and notify the target user."""

    removed_role = _REMOVE_PAYLOAD_ROLES.get(context.event.callback_payload or "")
    if removed_role is None:
        return
    actor_role = _actor_role(context)
    target = _target_from_state(context)
    if target is None:
        await _answer_callback_if_needed(context, "Данные потеряны")
        await context.send_text(STAFF_USER_NOT_FOUND_TEXT, keyboard=navigation_keyboard())
        return
    if _is_protected_target(target) and removed_role == ROLE_DEVELOPER:
        await _send_no_access(context)
        return
    if not can_remove_role(actor_role, removed_role):
        await _send_no_access(context)
        return

    removed = _staff_repository().remove_role(target.platform_user_id, removed_role, platform=PLATFORM_MAX)
    if not removed:
        await context.send_text(STAFF_NO_EXTRA_ROLES_TEXT, keyboard=navigation_keyboard())
        return
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="role_removed",
        section="roles",
        target_platform_user_id=target.platform_user_id,
        metadata={"role": removed_role},
    )
    await _answer_callback_if_needed(context, STAFF_ROLE_REMOVED_TEXT)
    state.clear_state_data(context.event.platform_user_id, context.event.chat_id)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_MENU_SCREEN)
    await context.send_text(STAFF_ROLE_REMOVED_TEXT, keyboard=staff_menu_screen(actor_role).keyboard)
    await notify_role_removed(context.sender, target, removed_role)


async def _show_staff_menu(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.STAFF_MENU_SCREEN)
    screen = staff_menu_screen(_actor_role(context))
    await context.send_text(screen.text, keyboard=screen.keyboard)


def _build_staff_list_text() -> str:
    staff = _staff_repository().list_staff(platform=PLATFORM_MAX)
    if not staff:
        return STAFF_LIST_EMPTY_TEXT
    users = _users_repository()
    lines = ["📋 Сотрудники", ""]
    for staff_role in staff:
        user = users.find_by_platform_user_id(staff_role.platform_user_id, platform=PLATFORM_MAX)
        lines.extend(_staff_role_lines(staff_role, user))
        lines.append("")
    return "\n".join(lines).strip()


def _staff_role_lines(staff_role: StaffRole, user: User | None) -> list[str]:
    title = f"{_ROLE_EMOJI.get(staff_role.role, '👤')} {staff_role.role}"
    name = _display_name(user) if user is not None else None
    if name:
        title = f"{title} — {name}"
    lines = [title, f"ID: {staff_role.platform_user_id}"]
    if user is not None and user.username:
        lines.append(f"@{user.username.lstrip('@')}")
    if user is not None and user.phone:
        lines.append(f"Телефон: {mask_phone(user.phone)}")
    return lines


def _find_user(identifier: str | None) -> User | None:
    if identifier is None:
        return None
    clean = identifier.strip()
    if not clean:
        return None
    normalized_phone = normalize_phone(clean)
    users = _users_repository()
    return users.find_by_identifier(normalized_phone or clean, platform=PLATFORM_MAX)


def _target_from_state(context: RouterContext) -> User | None:
    target_platform_user_id = state.get_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _TARGET_PLATFORM_USER_ID_KEY,
    )
    if not isinstance(target_platform_user_id, str) or not target_platform_user_id.strip():
        return None
    return _users_repository().find_by_platform_user_id(target_platform_user_id, platform=PLATFORM_MAX)


def _removable_roles(actor_role: str, target: User) -> list[str]:
    roles = _staff_repository().get_roles(target.platform_user_id, platform=PLATFORM_MAX)
    removable: list[str] = []
    for role in roles:
        if _is_protected_target(target) and role == ROLE_DEVELOPER:
            continue
        if can_remove_role(actor_role, role):
            removable.append(role)
    return removable


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return ROLE_USER
    return _staff_repository().get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _display_name(user: User | None) -> str | None:
    if user is None:
        return None
    for value in (user.display_name, user.first_name, user.username):
        if value and value.strip():
            return value.strip()
    return None


def _is_protected_target(user: User) -> bool:
    return is_protected_developer(user.platform_user_id, _dev_max_user_id(), max_user_id=user.max_user_id)


def _push_current_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current != screen_id:
        state.push_screen(context.event.platform_user_id, context.event.chat_id, current)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, screen_id)


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, STAFF_NO_ACCESS_TEXT)
    await context.send_text(STAFF_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)


def _staff_repository() -> StaffRolesRepository:
    return StaffRolesRepository(_database_path())


def _users_repository() -> UsersRepository:
    return UsersRepository(_database_path())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


def _dev_max_user_id() -> str | None:
    return getenv("DEV_MAX_USER_ID", "").strip() or None
