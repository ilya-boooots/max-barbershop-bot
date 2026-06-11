"""Settings flow for YClients master photos in MAX."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_contacts_settings
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.master_photos import MasterPhotosRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.master_photos import (
    MASTER_PHOTO_NON_PHOTO_TEXT,
    MASTER_PHOTOS_EMPTY_TEXT,
    MASTER_PHOTOS_ROOT_TEXT,
    MasterPhotosError,
    MasterPhotosService,
    MasterPhotoStaff,
)
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.ui.buttons import (
    MASTER_PHOTOS_BACK_PAYLOAD,
    MASTER_PHOTOS_DELETE_CONFIRM_PAYLOAD,
    MASTER_PHOTOS_DELETE_PAYLOAD,
    MASTER_PHOTOS_HOME_PAYLOAD,
    MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX,
    MASTER_PHOTOS_UPLOAD_PAYLOAD,
    SETTINGS_MASTER_PHOTOS_PAYLOAD,
    master_photo_delete_confirm_keyboard,
    master_photo_detail_keyboard,
    master_photo_wait_keyboard,
    master_photos_list_keyboard,
)
from max_barbershop_bot.ui.texts import SETTINGS_NO_ACCESS_TEXT

logger = logging.getLogger(__name__)

_MASTER_PHOTOS_STATE_KEY = "settings_master_photos_items"
_SELECTED_INDEX_STATE_KEY = "settings_master_photo_selected_index"
_SELECTED_STAFF_ID_STATE_KEY = "settings_master_photo_staff_id"
_SELECTED_MASTER_NAME_STATE_KEY = "settings_master_photo_master_name"


def register_master_photos_routes(router: Router) -> None:
    """Register master photo settings callbacks and upload handler."""

    router.on_callback(SETTINGS_MASTER_PHOTOS_PAYLOAD, handle_master_photos_menu)
    for index in range(20):
        router.on_callback(f"{MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX}{index}", handle_master_photo_select)
    router.on_callback(MASTER_PHOTOS_UPLOAD_PAYLOAD, handle_master_photo_upload_start)
    router.on_callback(MASTER_PHOTOS_DELETE_PAYLOAD, handle_master_photo_delete_start)
    router.on_callback(MASTER_PHOTOS_DELETE_CONFIRM_PAYLOAD, handle_master_photo_delete_confirm)
    router.on_callback(MASTER_PHOTOS_BACK_PAYLOAD, handle_master_photos_back)
    router.on_callback(MASTER_PHOTOS_HOME_PAYLOAD, handle_master_photos_home)
    router.on_screen_text(state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN, handle_master_photo_upload_receive)


async def handle_master_photos_menu(context: RouterContext) -> None:
    """Open the YClients master list for photo editing."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем фото мастеров 🖼️")
    await _show_master_photos_list(context, push_current=True)


async def handle_master_photo_select(context: RouterContext) -> None:
    """Show selected master photo status and actions."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context, "Открываем мастера 🖼️")
    index = _payload_index(context.event.callback_payload)
    masters = _masters_from_state(context)
    if index is None or index >= len(masters):
        await _show_master_photos_list(context, push_current=False)
        return
    master = masters[index]
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_INDEX_STATE_KEY, index)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_STAFF_ID_STATE_KEY, master.yclients_staff_id)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_MASTER_NAME_STATE_KEY, master.name)
    await _show_master_photo_detail(context, master, push_current=True)


async def handle_master_photo_upload_start(context: RouterContext) -> None:
    """Ask admin to send one MAX image for the selected master."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    master = _selected_master(context)
    if master is None:
        await _show_master_photos_list(context, push_current=False)
        return
    await _answer_callback_if_needed(context, "Ждём фото 📸")
    _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN)
    await context.send_text(f"📸 Отправьте одно фото для мастера {master.name} 😊", keyboard=master_photo_wait_keyboard())


async def handle_master_photo_upload_receive(context: RouterContext) -> None:
    """Save incoming MAX image token/url for the selected YClients staff id."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    master = _selected_master(context)
    if master is None:
        await context.send_text("⚠️ Не удалось определить мастера. Попробуйте заново 🙂")
        await _show_master_photos_list(context, push_current=False)
        return

    photo_service = _master_photos_service()
    photo_file_id, photo_url, photo_attachment_json = photo_service.extract_photo_reference(context.event.attachments)
    if not (photo_file_id or photo_url or photo_attachment_json):
        await context.send_text(MASTER_PHOTO_NON_PHOTO_TEXT, keyboard=master_photo_wait_keyboard())
        return

    previous = _photo_repository().get_by_staff_id(master.yclients_staff_id)
    _photo_repository().upsert_photo(
        master.yclients_staff_id,
        master.name,
        photo_file_id=photo_file_id,
        photo_url=photo_url,
        photo_attachment_json=photo_attachment_json,
        actor_platform_user_id=context.event.platform_user_id,
    )
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="master_photo_updated" if previous else "master_photo_added",
        section="master_photos",
        metadata={"yclients_staff_id": master.yclients_staff_id, "master_name": master.name},
    )
    await context.send_text("✅ Фото мастера обновлено")
    updated = MasterPhotoStaff(master.yclients_staff_id, master.name, master.specialization, has_photo=True)
    _replace_master_in_state(context, updated)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    await _show_master_photo_detail(context, updated, push_current=False)


async def handle_master_photo_delete_start(context: RouterContext) -> None:
    """Ask for explicit confirmation before deactivating a master photo."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    master = _selected_master(context)
    if master is None:
        await _show_master_photos_list(context, push_current=False)
        return
    await _answer_callback_if_needed(context, "Подтвердите удаление 🗑️")
    _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_DELETE_CONFIRM_SCREEN)
    await context.send_text(
        f"🗑️ Удалить фото мастера {master.name}?",
        keyboard=master_photo_delete_confirm_keyboard(),
    )


async def handle_master_photo_delete_confirm(context: RouterContext) -> None:
    """Deactivate selected master photo and return to detail card."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    master = _selected_master(context)
    if master is None:
        await _show_master_photos_list(context, push_current=False)
        return
    _photo_repository().delete_photo(master.yclients_staff_id, actor_platform_user_id=context.event.platform_user_id)
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="master_photo_deleted",
        section="master_photos",
        metadata={"yclients_staff_id": master.yclients_staff_id, "master_name": master.name},
    )
    await _answer_callback_if_needed(context, "Фото удалено 🗑️")
    await context.send_text("🗑️ Фото мастера удалено")
    updated = MasterPhotoStaff(master.yclients_staff_id, master.name, master.specialization, has_photo=False)
    _replace_master_in_state(context, updated)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    await _show_master_photo_detail(context, updated, push_current=False)


async def handle_master_photos_back(context: RouterContext) -> None:
    """Back navigation for master photo settings."""

    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen == state.SETTINGS_MASTER_PHOTOS_SCREEN:
        from max_barbershop_bot.flows.settings import handle_settings_menu

        await handle_settings_menu(context)
        return
    await _answer_callback_if_needed(context, "Возвращаемся назад ⬅️")
    if current_screen in {state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN, state.SETTINGS_MASTER_PHOTO_DELETE_CONFIRM_SCREEN}:
        master = _selected_master(context)
        if master is not None:
            await _show_master_photo_detail(context, master, push_current=False)
            return
    if current_screen == state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN:
        await _show_master_photos_list(context, push_current=False)
        return
    from max_barbershop_bot.flows.settings import handle_settings_menu

    await handle_settings_menu(context)


async def handle_master_photos_home(context: RouterContext) -> None:
    """Return to role-based home menu."""

    await _answer_callback_if_needed(context, "Главное меню 🏠")
    await show_home(context)


async def _show_master_photos_list(context: RouterContext, *, push_current: bool) -> None:
    actor_role = _actor_role(context)
    try:
        masters = await _master_photos_service().list_yclients_masters()
    except MasterPhotosError as exc:
        if push_current:
            _push_current_screen(context, state.SETTINGS_MASTER_PHOTOS_SCREEN)
        else:
            state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTOS_SCREEN)
        await context.send_text(exc.user_message, keyboard=master_photos_list_keyboard([]))
        return
    if push_current:
        _push_current_screen(context, state.SETTINGS_MASTER_PHOTOS_SCREEN)
    else:
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTOS_SCREEN)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _MASTER_PHOTOS_STATE_KEY, masters)
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="settings_section_opened",
        section="master_photos",
    )
    text = MASTER_PHOTOS_ROOT_TEXT if masters else MASTER_PHOTOS_EMPTY_TEXT
    await context.send_text(text, keyboard=master_photos_list_keyboard(masters))


async def _show_master_photo_detail(context: RouterContext, master: MasterPhotoStaff, *, push_current: bool) -> None:
    photo_service = _master_photos_service()
    photo = photo_service.get_photo(master.yclients_staff_id)
    attachment = photo_service.prepare_photo_attachment(photo)
    has_photo = attachment is not None
    text = photo_service.format_master_card_text(master.name, has_photo=has_photo)
    if push_current:
        _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    else:
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    keyboard = master_photo_detail_keyboard(has_photo=has_photo)
    if attachment is not None:
        await context.send_text(text, keyboard=keyboard, attachments=[attachment])
        return
    await context.send_text(text, keyboard=keyboard)


def _selected_master(context: RouterContext) -> MasterPhotoStaff | None:
    staff_id = _state_text(context, _SELECTED_STAFF_ID_STATE_KEY)
    master_name = _state_text(context, _SELECTED_MASTER_NAME_STATE_KEY)
    if not staff_id or not master_name:
        return None
    masters = _masters_from_state(context)
    existing = next((item for item in masters if item.yclients_staff_id == staff_id), None)
    if existing is not None:
        return existing
    return MasterPhotoStaff(staff_id, master_name, has_photo=_photo_repository().has_photo(staff_id))


def _masters_from_state(context: RouterContext) -> list[MasterPhotoStaff]:
    value = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _MASTER_PHOTOS_STATE_KEY)
    if isinstance(value, list) and all(isinstance(item, MasterPhotoStaff) for item in value):
        return value
    return []


def _replace_master_in_state(context: RouterContext, updated: MasterPhotoStaff) -> None:
    masters = [updated if item.yclients_staff_id == updated.yclients_staff_id else item for item in _masters_from_state(context)]
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _MASTER_PHOTOS_STATE_KEY, masters)


def _payload_index(payload: str | None) -> int | None:
    if not payload or not payload.startswith(MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX):
        return None
    try:
        return int(payload.removeprefix(MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX))
    except ValueError:
        return None


def _state_text(context: RouterContext, key: str) -> str | None:
    value = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, key)
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _push_current_screen(context: RouterContext, next_screen: str) -> None:
    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen != next_screen:
        state.push_screen(context.event.platform_user_id, context.event.chat_id, current_screen)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, next_screen)


def _actor_role(context: RouterContext) -> str:
    return _staff_repository().get_highest_role(context.event.platform_user_id, platform=PLATFORM_MAX)


def _staff_repository() -> StaffRolesRepository:
    return StaffRolesRepository(_database_path())


def _photo_repository() -> MasterPhotosRepository:
    return MasterPhotosRepository(_database_path())


def _master_photos_service() -> MasterPhotosService:
    return MasterPhotosService(_photo_repository(), YClientsSettingsRepository(_database_path()))


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, SETTINGS_NO_ACCESS_TEXT)
    await context.send_text(SETTINGS_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str) -> None:
    if context.event.callback_id:
        await context.answer_callback(notification)
