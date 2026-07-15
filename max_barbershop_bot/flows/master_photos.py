"""Settings flow for YClients master photos in MAX."""

from __future__ import annotations

import logging
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import can_view_contacts_settings, effective_role
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
from max_barbershop_bot.services.settings_audit import log_settings_action
from max_barbershop_bot.ui.buttons import (
    MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX,
    MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX,
    MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX,
    MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX,
    NAV_HOME_PAYLOAD,
    SETTINGS_MASTER_PHOTOS_PAYLOAD,
    master_photo_detail_keyboard,
    master_photos_list_keyboard,
    settings_menu_keyboard,
)
from max_barbershop_bot.ui.texts import SETTINGS_NO_ACCESS_TEXT

logger = logging.getLogger(__name__)

_MASTER_PHOTOS_STATE_KEY = "settings_master_photos_items"
_MASTER_PHOTOS_PAGE_STATE_KEY = "settings_master_photos_page"
_SELECTED_STAFF_ID_STATE_KEY = "settings_master_photo_staff_id"
_SELECTED_MASTER_NAME_STATE_KEY = "settings_master_photo_master_name"


def register_master_photos_routes(router: Router) -> None:
    """Register master photo settings callbacks and upload handler."""

    router.on_callback(SETTINGS_MASTER_PHOTOS_PAYLOAD, handle_master_photos_menu)
    router.on_callback_prefix(MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX, handle_master_photos_page)
    router.on_callback_prefix(MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX, handle_master_photo_select)
    router.on_callback_prefix(MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX, handle_master_photo_upload_start)
    router.on_callback_prefix(MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX, handle_master_photo_delete_start)
    router.on_screen_text(state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN, handle_master_photo_upload_receive)


async def handle_master_photos_menu(context: RouterContext) -> None:
    """Open the YClients master list for photo editing."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _show_master_photos_list(context, push_current=True, page=0)


async def handle_master_photos_page(context: RouterContext) -> None:
    """Render an approved overflow page without dropping YClients masters."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    page = _payload_page(context.event.callback_payload)
    masters = _masters_from_state(context)
    if page is None or not masters or page > _last_page(masters):
        await context.send_text("Не удалось найти мастера 🙂")
        if masters:
            current_page = min(_current_page(context), _last_page(masters))
            await context.send_text(
                MASTER_PHOTOS_ROOT_TEXT,
                keyboard=master_photos_list_keyboard(masters, page=current_page),
            )
        else:
            await _show_master_photos_list(context, push_current=False, page=0)
        return
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.SETTINGS_MASTER_PHOTOS_SCREEN,
    )
    state.set_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _MASTER_PHOTOS_PAGE_STATE_KEY,
        page,
    )
    await context.send_text(MASTER_PHOTOS_ROOT_TEXT, keyboard=master_photos_list_keyboard(masters, page=page))


async def handle_master_photo_select(context: RouterContext) -> None:
    """Show selected master photo status and actions."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    staff_id = _payload_staff_id(context.event.callback_payload, MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX)
    masters = _masters_from_state(context)
    master = next((item for item in masters if item.yclients_staff_id == staff_id), None)
    if master is None:
        await context.send_text("Не удалось найти мастера 🙂")
        await _show_master_photos_list(context, push_current=False, page=_current_page(context))
        return
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_STAFF_ID_STATE_KEY, master.yclients_staff_id)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_MASTER_NAME_STATE_KEY, master.name)
    await _show_master_photo_detail(context, master, push_current=True)


async def handle_master_photo_upload_start(context: RouterContext) -> None:
    """Ask admin to send one MAX image for the selected master."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    staff_id = _payload_staff_id(context.event.callback_payload, MASTER_PHOTOS_UPLOAD_PAYLOAD_PREFIX)
    master = _selected_master(context)
    if master is None or master.yclients_staff_id != staff_id:
        await context.send_text("Сначала выберите мастера 🙂")
        return
    _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_WAIT_PHOTO_SCREEN)
    await context.send_text(f"📸 Отправьте одно фото для мастера {master.name} 😊")


async def handle_master_photo_upload_receive(context: RouterContext) -> None:
    """Save incoming MAX image token/url for the selected YClients staff id."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    master = _selected_master(context)
    if master is None:
        await context.send_text("⚠️ Не удалось определить мастера. Попробуйте заново 🙂")
        await _show_master_photos_list(context, push_current=False, page=_current_page(context))
        return

    photo_service = _master_photos_service()
    photo_file_id, photo_url, photo_attachment_json = photo_service.extract_photo_reference(context.event.attachments)
    if not (photo_file_id or photo_url or photo_attachment_json):
        await context.send_text(MASTER_PHOTO_NON_PHOTO_TEXT)
        return

    try:
        previous = _photo_repository().get_by_staff_id(master.yclients_staff_id)
        changed = (
            previous is None
            or previous.master_name != master.name
            or previous.photo_file_id != photo_file_id
            or previous.photo_url != photo_url
            or previous.photo_attachment_json != photo_attachment_json
        )
        _photo_repository().upsert_photo(
            master.yclients_staff_id,
            master.name,
            photo_file_id=photo_file_id,
            photo_url=photo_url,
            photo_attachment_json=photo_attachment_json,
            actor_platform_user_id=context.event.platform_user_id,
        )
    except Exception:  # noqa: BLE001 - storage diagnostics must not leak into user-facing text.
        await context.send_text("⚠️ Не удалось сохранить фото мастера. Попробуйте ещё раз.")
        return
    if changed:
        log_settings_action(
            actor_platform_user_id=context.event.platform_user_id,
            actor_role=actor_role,
            action="master_photo_changed",
            section="master_photos",
            metadata={"yclients_staff_id": master.yclients_staff_id, "master_name": master.name},
        )
    await context.send_text("✅ Фото мастера обновлено")
    updated = MasterPhotoStaff(master.yclients_staff_id, master.name, master.specialization, has_photo=True)
    _replace_master_in_state(context, updated)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    await _show_master_photo_detail(context, updated, push_current=False)


async def handle_master_photo_delete_start(context: RouterContext) -> None:
    """Delete the exact master photo immediately, matching Telegram."""

    actor_role = _actor_role(context)
    if not can_view_contacts_settings(actor_role):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    staff_id = _payload_staff_id(context.event.callback_payload, MASTER_PHOTOS_DELETE_PAYLOAD_PREFIX)
    master = next((item for item in _masters_from_state(context) if item.yclients_staff_id == staff_id), None)
    if master is None:
        await context.send_text("Не удалось найти мастера 🙂")
        await _show_master_photos_list(context, push_current=False, page=_current_page(context))
        return
    try:
        changed = _photo_repository().delete_photo(
            master.yclients_staff_id,
            actor_platform_user_id=context.event.platform_user_id,
        )
    except Exception:  # noqa: BLE001 - storage diagnostics must not leak into user-facing text.
        await context.send_text("⚠️ Не удалось удалить фото мастера. Попробуйте ещё раз.")
        return
    if changed:
        log_settings_action(
            actor_platform_user_id=context.event.platform_user_id,
            actor_role=actor_role,
            action="master_photo_deleted",
            section="master_photos",
            metadata={"yclients_staff_id": master.yclients_staff_id, "master_name": master.name},
        )
    await context.send_text("🗑️ Фото мастера удалено")
    updated = MasterPhotoStaff(master.yclients_staff_id, master.name, master.specialization, has_photo=False)
    _replace_master_in_state(context, updated)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_STAFF_ID_STATE_KEY, master.yclients_staff_id)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _SELECTED_MASTER_NAME_STATE_KEY, master.name)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    await _show_master_photo_detail(context, updated, push_current=False)


async def _show_master_photos_list(context: RouterContext, *, push_current: bool, page: int = 0) -> None:
    actor_role = _actor_role(context)
    try:
        masters = await _master_photos_service().list_yclients_masters()
    except MasterPhotosError as exc:
        if push_current:
            _push_current_screen(context, state.SETTINGS_MASTER_PHOTOS_SCREEN)
        else:
            state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTOS_SCREEN)
        await context.send_text(
            exc.user_message,
            keyboard=settings_menu_keyboard(actor_role, home_payload=NAV_HOME_PAYLOAD),
        )
        return
    if push_current:
        _push_current_screen(context, state.SETTINGS_MASTER_PHOTOS_SCREEN)
    else:
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTOS_SCREEN)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _MASTER_PHOTOS_STATE_KEY, masters)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _MASTER_PHOTOS_PAGE_STATE_KEY, page)
    log_settings_action(
        actor_platform_user_id=context.event.platform_user_id,
        actor_role=actor_role,
        action="settings_section_opened",
        section="master_photos",
    )
    text = MASTER_PHOTOS_ROOT_TEXT if masters else MASTER_PHOTOS_EMPTY_TEXT
    keyboard = (
        master_photos_list_keyboard(masters, page=page)
        if masters
        else settings_menu_keyboard(actor_role, home_payload=NAV_HOME_PAYLOAD)
    )
    await context.send_text(text, keyboard=keyboard)


async def _show_master_photo_detail(context: RouterContext, master: MasterPhotoStaff, *, push_current: bool) -> None:
    photo_service = _master_photos_service()
    try:
        photo = photo_service.get_photo(master.yclients_staff_id)
        attachment = photo_service.prepare_photo_attachment(photo)
    except Exception:  # noqa: BLE001 - storage diagnostics must not leak into user-facing text.
        if push_current:
            _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
        else:
            state.set_current_screen(
                context.event.platform_user_id,
                context.event.chat_id,
                state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN,
            )
        await context.send_text(
            "⚠️ Не удалось загрузить фото мастера. Попробуйте ещё раз.",
            keyboard=master_photo_detail_keyboard(yclients_staff_id=master.yclients_staff_id),
        )
        return
    has_photo = attachment is not None
    text = photo_service.format_master_card_text(master.name, has_photo=has_photo)
    if push_current:
        _push_current_screen(context, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    else:
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.SETTINGS_MASTER_PHOTO_DETAIL_SCREEN)
    keyboard = master_photo_detail_keyboard(yclients_staff_id=master.yclients_staff_id)
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


def _payload_staff_id(payload: str | None, prefix: str) -> str | None:
    if not payload or not payload.startswith(prefix):
        return None
    value = payload.removeprefix(prefix).strip()
    return value if value and len(value.encode("utf-8")) <= 45 else None


def _payload_page(payload: str | None) -> int | None:
    if not payload or not payload.startswith(MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX):
        return None
    value = payload.removeprefix(MASTER_PHOTOS_PAGE_PAYLOAD_PREFIX)
    return int(value) if value.isdigit() else None


def _current_page(context: RouterContext) -> int:
    value = state.get_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _MASTER_PHOTOS_PAGE_STATE_KEY,
    )
    return value if isinstance(value, int) and value >= 0 else 0


def _last_page(masters: list[MasterPhotoStaff]) -> int:
    page_size = 28 if len(masters) <= 28 else 27
    return max(0, (len(masters) - 1) // page_size)


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
    db_role = _staff_repository().get_highest_role(context.event.platform_user_id, platform=PLATFORM_MAX)
    return effective_role(
        db_role,
        platform_user_id=context.event.platform_user_id,
        dev_max_user_id=getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    )


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


async def _answer_callback_if_needed(context: RouterContext, notification: str | None = None) -> None:
    if context.event.callback_id:
        await context.answer_callback()
