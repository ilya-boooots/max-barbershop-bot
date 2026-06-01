from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.core.navigation import back_handler, clear_state_preserving_navigation, home_handler, render_main_menu, reset_stack
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, has_any_role
from app.integrations.yclients.clients_sync import yclients_update_client
from app.integrations.yclients.endpoints import get_client_details, search_clients
from app.integrations.yclients.errors import YClientsError
from app.integrations.yclients import build_yclients_client, get_yclients_credentials
from app.integrations.yclients.endpoints import list_staff
from app.repositories.contacts_override import clear_contacts_override, upsert_contacts_override
from app.repositories.master_photos import delete_master_photo, get_master_photo, upsert_master_photo
from app.repositories.staff_action_logs import log_staff_action
from app.repositories.users import (
    find_other_user_by_phone_keys,
    find_other_user_by_yclients_client_id,
    get_user_by_tg_id,
    update_profile_name_phone,
    update_profile_phone_and_mapping,
)
from app.repositories.support_settings import normalize_support_username, reset_support_settings, upsert_support_settings
from app.services.contacts import render_contacts_block, resolve_contacts
from app.services.support import render_support_message, resolve_support_settings, support_screen_kb
from app.ui.buttons import BACK, HOME
from app.ui.callbacks import NAV_HOME
from app.utils.phone import normalize_phone
from app.utils.staff import display_name, role_label

router = Router()
logger = logging.getLogger(__name__)

CB_SETTINGS_ROOT = "settings:root"
CB_PROFILE_ROOT = "settings:profile"
CB_PROFILE_EDIT_NAME = "settings:profile:edit_name"
CB_PROFILE_EDIT_PHONE = "settings:profile:edit_phone"
CB_PROFILE_SAVE_NAME = "settings:profile:save_name"
CB_PROFILE_SAVE_PHONE = "settings:profile:save_phone"
CB_PROFILE_RETRY_NAME = "settings:profile:retry_name"
CB_PROFILE_RETRY_PHONE = "settings:profile:retry_phone"
CB_PROFILE_BACK = "settings:profile:back"
CB_PROFILE_RELINK_CONFIRM = "settings:profile:relink_confirm"
CB_PROFILE_PHONE_CONTACT = "📞 Поделиться новым контактом"
CB_PHOTOS_ROOT = "settings:master_photos"
CB_PHOTO_SELECT = "settings:master_photos:select"
CB_PHOTO_UPLOAD = "settings:master_photos:upload"
CB_PHOTO_DELETE = "settings:master_photos:delete"
CB_PHOTO_BACK = "settings:master_photos:back"
CB_PHOTO_HOME = "settings:master_photos:home"
CB_CONTACTS_ROOT = "settings:contacts"
CB_CONTACTS_EDIT_ADDRESS = "settings:contacts:address"
CB_CONTACTS_EDIT_PHONE = "settings:contacts:phone"
CB_CONTACTS_EDIT_SCHEDULE = "settings:contacts:schedule"
CB_CONTACTS_RESET = "settings:contacts:reset"
CB_CONTACTS_PREVIEW = "settings:contacts:preview"
CB_CONTACTS_BACK = "settings:contacts:back"
CB_SUPPORT_ROOT = "settings:support"
CB_SUPPORT_EDIT_DESCRIPTION = "settings:support:description"
CB_SUPPORT_EDIT_USERNAME = "settings:support:username"
CB_SUPPORT_PREVIEW = "settings:support:preview"
CB_SUPPORT_RESET = "settings:support:reset"
CB_SUPPORT_BACK = "settings:support:back"
CB_BROADCAST_SETTINGS_ROOT = "broadcast:settings:root"

ALLOWED_ROLES = {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}


CONTACT_FIELD_ACTIONS = {
    "address": ("contacts_address_changed", "изменил адрес филиала"),
    "phone": ("contacts_phone_changed", "изменил контактный телефон филиала"),
    "schedule": ("contacts_schedule_changed", "изменил режим работы филиала"),
}


async def _log_settings_action(actor_tg_id: int, action_type: str, human_tail: str, **metadata: Any) -> None:
    actor = await get_user_by_tg_id(actor_tg_id)
    actor_name = display_name(actor or {})
    actor_role = (actor or {}).get("role") or ("developer" if actor_tg_id == 378881880 else None)
    role_text = role_label(actor_role).split(" ", 1)[-1]
    await log_staff_action(
        actor_tg_id=actor_tg_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action_type=action_type,
        human_text=f"{role_text} {actor_name} {human_tail}.",
        metadata={key: value for key, value in metadata.items() if value not in (None, "")},
    )


@dataclass(frozen=True)
class StaffLite:
    id: str
    name: str


class MasterPhotoStates(StatesGroup):
    CHOOSE_MASTER = State()
    CHOOSE_ACTION = State()
    WAIT_UPLOAD = State()


class ContactsEditStates(StatesGroup):
    CONTACTS_EDIT_MENU = State()
    CONTACTS_EDIT_ADDRESS = State()
    CONTACTS_EDIT_PHONE = State()
    CONTACTS_EDIT_SCHEDULE = State()


class SupportSettingsStates(StatesGroup):
    SUPPORT_SETTINGS_MENU = State()
    SUPPORT_EDIT_DESCRIPTION = State()
    SUPPORT_EDIT_USERNAME = State()


class ProfileSettingsStates(StatesGroup):
    PROFILE_MENU = State()
    PROFILE_EDIT_NAME = State()
    PROFILE_CONFIRM_NAME = State()
    PROFILE_EDIT_PHONE = State()
    PROFILE_CONFIRM_PHONE = State()
    PROFILE_CONFIRM_PHONE_RELINK = State()


class PhoneConflictType(str, Enum):
    NO_CONFLICT = "no_conflict"
    SAME_CLIENT = "same_client"
    RELINK_AVAILABLE = "relink_available"
    RELINK_BLOCKED = "relink_blocked"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class PhoneConflictResolution:
    conflict_type: PhoneConflictType
    found_client_id: int | None = None
    found_client_name: str | None = None
    linked_user_id: int | None = None


async def _is_allowed(user_id: int) -> bool:
    return await has_any_role(user_id, ALLOWED_ROLES)


def _extract_data_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


async def _load_staff_list() -> tuple[str, list[StaffLite]]:
    credentials, _ = await get_yclients_credentials()
    client, _ = await build_yclients_client()
    try:
        payload = await list_staff(client, company_id=credentials.company_id)
    finally:
        await client.close()

    staff: list[StaffLite] = []
    for item in _extract_data_rows(payload):
        staff_id = _safe_str(item.get("id") or item.get("staff_id"))
        name = _safe_str(item.get("name") or item.get("fullname") or item.get("title"))
        if not staff_id or not name:
            continue
        if item.get("is_deleted") is True:
            continue
        if item.get("active") is False:
            continue
        staff.append(StaffLite(id=staff_id, name=name))

    staff.sort(key=lambda x: x.name.lower())
    return credentials.company_id, staff


def _settings_root_kb(*, can_manage_admin_settings: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="👤 Мои данные", callback_data=CB_PROFILE_ROOT)],
    ]
    if can_manage_admin_settings:
        rows.extend(
            [
                [InlineKeyboardButton(text="🖼️ Редактировать фото мастеров", callback_data=CB_PHOTOS_ROOT)],
                [InlineKeyboardButton(text="✏️ Редактировать контакты", callback_data=CB_CONTACTS_ROOT)],
                [InlineKeyboardButton(text="🛠 Настройка раздела \"Поддержка\"", callback_data=CB_SUPPORT_ROOT)],
                [InlineKeyboardButton(text="⚙️ Настройки рассылок", callback_data=CB_BROADCAST_SETTINGS_ROOT)],
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text=BACK, callback_data=CB_PHOTO_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profile_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить имя", callback_data=CB_PROFILE_EDIT_NAME)],
            [InlineKeyboardButton(text="📱 Изменить телефон", callback_data=CB_PROFILE_EDIT_PHONE)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PROFILE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _profile_name_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сохранить", callback_data=CB_PROFILE_SAVE_NAME)],
            [InlineKeyboardButton(text="✏️ Ввести заново", callback_data=CB_PROFILE_RETRY_NAME)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PROFILE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _profile_phone_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сохранить", callback_data=CB_PROFILE_SAVE_PHONE)],
            [InlineKeyboardButton(text="📱 Ввести заново", callback_data=CB_PROFILE_RETRY_PHONE)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PROFILE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _profile_phone_relink_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Привязать этот номер", callback_data=CB_PROFILE_RELINK_CONFIRM)],
            [InlineKeyboardButton(text="📱 Ввести другой номер", callback_data=CB_PROFILE_RETRY_PHONE)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PROFILE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _profile_phone_conflict_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Ввести другой номер", callback_data=CB_PROFILE_RETRY_PHONE)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PROFILE_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _profile_phone_input_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CB_PROFILE_PHONE_CONTACT, request_contact=True)],
            [KeyboardButton(text=BACK)],
            [KeyboardButton(text=HOME)],
        ],
        resize_keyboard=True,
    )


async def _remove_contact_keyboard(target: Message | CallbackQuery) -> None:
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(" ", reply_markup=ReplyKeyboardRemove())
        return
    await target.answer(" ", reply_markup=ReplyKeyboardRemove())


def _contacts_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Изменить адрес", callback_data=CB_CONTACTS_EDIT_ADDRESS)],
            [InlineKeyboardButton(text="📞 Изменить телефон", callback_data=CB_CONTACTS_EDIT_PHONE)],
            [InlineKeyboardButton(text="⏰ Изменить режим работы", callback_data=CB_CONTACTS_EDIT_SCHEDULE)],
            [InlineKeyboardButton(text="♻️ Сбросить к данным YClients", callback_data=CB_CONTACTS_RESET)],
            [InlineKeyboardButton(text="👁️ Предпросмотр", callback_data=CB_CONTACTS_PREVIEW)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_CONTACTS_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


def _staff_list_kb(staff: list[StaffLite]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=item.name, callback_data=f"{CB_PHOTO_SELECT}:{item.id}")]
        for item in staff
    ]
    rows.append([InlineKeyboardButton(text=BACK, callback_data=CB_SETTINGS_ROOT)])
    rows.append([InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)




def _support_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить описание", callback_data=CB_SUPPORT_EDIT_DESCRIPTION)],
            [InlineKeyboardButton(text="👤 Изменить аккаунт поддержки", callback_data=CB_SUPPORT_EDIT_USERNAME)],
            [InlineKeyboardButton(text="👁️ Предпросмотр", callback_data=CB_SUPPORT_PREVIEW)],
            [InlineKeyboardButton(text="♻️ Сбросить к значениям по умолчанию", callback_data=CB_SUPPORT_RESET)],
            [InlineKeyboardButton(text=BACK, callback_data=CB_SUPPORT_BACK)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


async def _show_support_settings_editor(target: Message | CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(target.from_user.id):
        await _deny_access(target, state)
        return

    _, effective, _ = await resolve_support_settings()
    text = (
        "🛠 Настройка раздела \"Поддержка\"\n\n"
        f"📝 Текущее описание:\n{effective.description}\n\n"
        f"👤 Текущий аккаунт: @{effective.username}\n"
        f"🔗 Ссылка: https://t.me/{effective.username}"
    )
    await state.set_state(SupportSettingsStates.SUPPORT_SETTINGS_MENU)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text, reply_markup=_support_settings_kb())
        await target.answer()
        return
    await target.answer(text, reply_markup=_support_settings_kb())


def _mask_phone(phone: str | None) -> str:
    raw = (phone or "").strip()
    if len(raw) <= 5:
        return raw or "—"
    return f"{raw[:2]}***{raw[-3:]}"


def _extract_client_row(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    rows = _extract_data_rows(payload)
    return rows[0] if rows else None


def _extract_phone_from_client(client_row: dict[str, Any]) -> str | None:
    for key in ("phone", "tel"):
        value = client_row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    phones = client_row.get("phones")
    if isinstance(phones, list):
        for item in phones:
            if isinstance(item, dict):
                value = item.get("phone") or item.get("number")
                if value:
                    return str(value).strip()
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _extract_name_from_client(client_row: dict[str, Any]) -> str | None:
    for key in ("name", "fullname"):
        value = client_row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_birth_date_from_client(client_row: dict[str, Any]) -> str | None:
    for key in ("birth_date", "bdate"):
        value = client_row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_phone_match_keys_from_row(client_row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    raw_values: list[str] = []
    direct_phone = _extract_phone_from_client(client_row)
    if direct_phone:
        raw_values.append(direct_phone)
    phones_raw = client_row.get("phones")
    if isinstance(phones_raw, list):
        for item in phones_raw:
            if isinstance(item, dict):
                phone = _safe_str(item.get("phone") or item.get("number"))
                if phone:
                    raw_values.append(phone)
            elif isinstance(item, str):
                raw_values.append(item)
    for raw_phone in raw_values:
        normalized = normalize_phone(raw_phone, default_region="RU")
        keys.update(
            value
            for value in (
                normalized.canonical_e164,
                normalized.ru_11_with_7,
                normalized.ru_11_with_8,
                normalized.digits_only,
            )
            if value
        )
    return keys


async def find_yclients_client_by_phone(
    *,
    company_id: str,
    normalized_phone: str,
) -> tuple[dict[str, Any] | None, int]:
    bundle = normalize_phone(normalized_phone, default_region="RU")
    search_variants = [value for value in (bundle.canonical_e164, bundle.ru_11_with_7, bundle.ru_11_with_8) if value]
    target_keys = set(search_variants + [bundle.digits_only])
    if not search_variants:
        return None, 0
    found_by_id: dict[int, dict[str, Any]] = {}
    client, _ = await build_yclients_client()
    try:
        for variant in search_variants:
            payload = await search_clients(
                client,
                company_id=company_id,
                query=variant,
                by_phone=True,
                page=1,
                count=50,
            )
            for item in _extract_data_rows(payload):
                client_id_raw = _safe_str(item.get("id") or item.get("client_id"))
                if not client_id_raw.isdigit():
                    continue
                found_by_id[int(client_id_raw)] = item
    finally:
        await client.close()

    matched: list[dict[str, Any]] = []
    for item in found_by_id.values():
        if _extract_phone_match_keys_from_row(item) & target_keys:
            matched.append(item)
    if len(matched) != 1:
        return None, len(matched)
    return matched[0], len(matched)


async def resolve_phone_change_conflict(
    *,
    current_user_id: int,
    current_yclients_client_id: int,
    company_id: str,
    new_phone: str,
) -> PhoneConflictResolution:
    found_client, matched_count = await find_yclients_client_by_phone(
        company_id=company_id,
        normalized_phone=new_phone,
    )
    if matched_count > 1:
        return PhoneConflictResolution(conflict_type=PhoneConflictType.AMBIGUOUS)
    if not found_client:
        return PhoneConflictResolution(conflict_type=PhoneConflictType.NO_CONFLICT)

    found_client_id_raw = _safe_str(found_client.get("id") or found_client.get("client_id"))
    if not found_client_id_raw.isdigit():
        return PhoneConflictResolution(conflict_type=PhoneConflictType.NO_CONFLICT)
    found_client_id = int(found_client_id_raw)
    found_client_name = _safe_str(found_client.get("name") or found_client.get("fullname")) or None
    if found_client_id == current_yclients_client_id:
        return PhoneConflictResolution(
            conflict_type=PhoneConflictType.SAME_CLIENT,
            found_client_id=found_client_id,
            found_client_name=found_client_name,
        )

    linked_user = await find_other_user_by_yclients_client_id(
        current_user_id=current_user_id,
        yclients_client_id=found_client_id,
    )
    if linked_user:
        return PhoneConflictResolution(
            conflict_type=PhoneConflictType.RELINK_BLOCKED,
            found_client_id=found_client_id,
            found_client_name=found_client_name,
            linked_user_id=int(linked_user["user_id"]),
        )
    return PhoneConflictResolution(
        conflict_type=PhoneConflictType.RELINK_AVAILABLE,
        found_client_id=found_client_id,
        found_client_name=found_client_name,
    )


def build_yclients_client_update_payload(
    *,
    local_user: dict[str, Any],
    yclients_client_row: dict[str, Any] | None,
    new_name: str | None = None,
    new_phone: str | None = None,
) -> dict[str, Any]:
    """
    Build safe merged payload for PUT /api/v1/client/{company_id}/{client_id}.

    Required fields (name/phone) are always included from new values, YClients
    card values, or local DB fallback.
    """
    remote = yclients_client_row or {}
    payload: dict[str, Any] = {}

    merged_name = (
        _safe_str(new_name)
        or _safe_str(_extract_name_from_client(remote))
        or _safe_str(local_user.get("name"))
    )
    merged_phone = (
        _safe_str(new_phone)
        or _safe_str(_extract_phone_from_client(remote))
        or _safe_str(local_user.get("phone"))
    )
    if not merged_name:
        raise ValueError("missing_required_name")
    if not merged_phone:
        raise ValueError("missing_required_phone")
    payload["name"] = merged_name
    payload["phone"] = merged_phone

    birth_date = _safe_str(_extract_birth_date_from_client(remote) or local_user.get("birth_date"))
    if birth_date:
        payload["birth_date"] = birth_date
        payload["bdate"] = birth_date

    for optional_key in ("email", "comment"):
        value = _safe_str(remote.get(optional_key))
        if value:
            payload[optional_key] = value

    return payload


def _format_yclients_profile_update_error(*, action: str, exc: Exception) -> tuple[str, str]:
    action_label = "имя" if action == "name_update" else "телефон"
    fallback_message = (
        f"⚠️ Не удалось обновить {action_label} в YClients. "
        "Попробуйте позже или обратитесь в поддержку."
    )
    if not isinstance(exc, YClientsError):
        return fallback_message, "non_yclients_error"

    status = exc.status_code if exc.status_code is not None else "n/a"
    raw_snippet = (exc.response_snippet or "").strip()
    lower_snippet = raw_snippet.lower()
    if action == "phone_update":
        if "client with this phone already exists" in lower_snippet or "errors.phone" in lower_snippet:
            return (
                "⚠️ Этот номер уже используется в системе.\n"
                "Мы не можем изменить его автоматически. Обратитесь в поддержку.",
                f"status={status} duplicate_phone_conflict",
            )
        return (
            "⚠️ Не удалось обновить номер телефона.\n"
            "Попробуйте позже или обратитесь в поддержку.",
            f"status={status} phone_update_failed",
        )

    if "errors.phone" in lower_snippet or "обязательный параметр phone" in lower_snippet:
        return (
            "⚠️ YClients не принял данные: не передан телефон. "
            "Старые данные сохранены без изменений.",
            f"status={status} validation=missing_phone",
        )
    if "errors.name" in lower_snippet or "обязательный параметр name" in lower_snippet:
        return (
            "⚠️ YClients не принял данные: не передано имя. "
            "Старые данные сохранены без изменений.",
            f"status={status} validation=missing_name",
        )

    parsed_summary: str | None = None
    try:
        parsed = json.loads(raw_snippet) if raw_snippet else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        meta = parsed.get("meta")
        if isinstance(meta, dict) and meta.get("message"):
            parsed_summary = str(meta.get("message")).strip()
        elif parsed.get("message"):
            parsed_summary = str(parsed.get("message")).strip()

    if parsed_summary:
        return (
            fallback_message,
            f"status={status} validation={parsed_summary[:180]}",
        )

    return fallback_message, f"status={status} generic_error"


def _is_valid_name(value: str) -> bool:
    text = value.strip()
    if len(text) < 2 or len(text) > 60:
        return False
    if all(ch in "_-+=/\\|<>[]{}" for ch in text):
        return False
    return any(ch.isalpha() for ch in text)


async def _load_profile_snapshot(user_id: int) -> tuple[dict[str, Any] | None, str | None]:
    user = await get_user_by_tg_id(user_id)
    if user is None:
        return None, None
    warning: str | None = None
    yclients_client_id = _safe_str(user.get("yclients_client_id"))
    if yclients_client_id:
        try:
            credentials, _ = await get_yclients_credentials()
            client, _ = await build_yclients_client()
            try:
                payload = await get_client_details(
                    client,
                    company_id=credentials.company_id,
                    client_id=yclients_client_id,
                )
            finally:
                await client.close()
            remote = _extract_client_row(payload)
            if remote:
                remote_name = _safe_str(remote.get("name") or remote.get("fullname"))
                remote_phone = _safe_str(_extract_phone_from_client(remote))
                if remote_name:
                    user["name"] = remote_name
                if remote_phone:
                    user["phone"] = remote_phone
        except Exception:
            warning = "⚠️ Сейчас не удалось обновить данные из YClients. Показаны локальные данные."
    return user, warning


async def _show_profile_root(target: Message | CallbackQuery, state: FSMContext) -> None:
    user, warning = await _load_profile_snapshot(target.from_user.id)
    if user is None:
        if isinstance(target, CallbackQuery):
            if target.message:
                await target.message.answer("⛔ Раздел недоступен.")
            await target.answer()
        else:
            await target.answer("⛔ Раздел недоступен.")
        return
    logger.info("settings_profile_open user_id=%s", target.from_user.id)
    text = (
        "👤 Мои данные\n\n"
        f"Имя: {_safe_str(user.get('name')) or '—'}\n"
        f"Телефон: {_safe_str(user.get('phone')) or '—'}\n\n"
        "Эти данные используются для записи и синхронизации с YClients."
    )
    if warning:
        text = f"{text}\n\n{warning}"
    await state.set_state(ProfileSettingsStates.PROFILE_MENU)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text, reply_markup=_profile_root_kb())
        await target.answer()
        return
    await target.answer(text, reply_markup=_profile_root_kb())


def _master_actions_kb(staff_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Загрузить / заменить фото", callback_data=f"{CB_PHOTO_UPLOAD}:{staff_id}")],
            [InlineKeyboardButton(text="🗑️ Удалить фото", callback_data=f"{CB_PHOTO_DELETE}:{staff_id}")],
            [InlineKeyboardButton(text=BACK, callback_data=CB_PHOTOS_ROOT)],
            [InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)],
        ]
    )


async def _deny_access(target: Message | CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    logger.warning("settings_unauthorized_access user_id=%s", target.from_user.id)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer("⛔ Раздел недоступен.")
        await target.answer()
        return
    await target.answer("⛔ Раздел недоступен.")


async def _show_settings_root(target: Message | CallbackQuery, state: FSMContext) -> None:
    can_manage = await _is_allowed(target.from_user.id)
    logger.info("settings_open user_id=%s can_manage=%s", target.from_user.id, can_manage)
    await state.set_state(ProfileSettingsStates.PROFILE_MENU)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(
                "⚙️ Настройки\n\nВыберите раздел 👇",
                reply_markup=_settings_root_kb(can_manage_admin_settings=can_manage),
            )
        await target.answer()
        return
    await target.answer(
        "⚙️ Настройки\n\nВыберите раздел 👇",
        reply_markup=_settings_root_kb(can_manage_admin_settings=can_manage),
    )


async def _show_contacts_editor(target: Message | CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(target.from_user.id):
        await _deny_access(target, state)
        return
    data = await resolve_contacts()
    text = (
        "✏️ Редактирование контактов\n\n"
        f"🏠 Адрес: {data.resolved.address}\n"
        f"📞 Телефон: {data.resolved.phone}\n"
        f"⏰ Режим работы: {data.resolved.schedule}"
    )
    await state.set_state(ContactsEditStates.CONTACTS_EDIT_MENU)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.answer(text, reply_markup=_contacts_edit_kb())
        await target.answer()
        return
    await target.answer(text, reply_markup=_contacts_edit_kb())


async def open_settings_menu(target: Message | CallbackQuery, state: FSMContext) -> None:
    await _show_settings_root(target, state)


async def _show_staff_list(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    company_id, staff = await _load_staff_list()
    await state.set_state(MasterPhotoStates.CHOOSE_MASTER)
    await state.update_data(mp_company_id=company_id, mp_staff=[item.__dict__ for item in staff])
    text = "🖼️ Выберите мастера для редактирования фото 😊" if staff else "😕 Не удалось найти активных мастеров."
    if callback.message:
        await callback.message.answer(
            text,
            reply_markup=_staff_list_kb(staff) if staff else _settings_root_kb(can_manage_admin_settings=True),
        )
    await callback.answer()


async def _show_master_card(target: Message | CallbackQuery, state: FSMContext, staff_id: str) -> None:
    data = await state.get_data()
    company_id = _safe_str(data.get("mp_company_id"))
    staff_raw = [item for item in data.get("mp_staff") or [] if isinstance(item, dict)]
    selected = next((item for item in staff_raw if _safe_str(item.get("id")) == staff_id), None)
    if not selected or not company_id:
        if isinstance(target, CallbackQuery):
            await target.answer("Не удалось найти мастера 🙂", show_alert=True)
            await _show_staff_list(target, state)
        else:
            await target.answer("Не удалось найти мастера 🙂")
        return

    staff_name = _safe_str(selected.get("name"))
    await state.set_state(MasterPhotoStates.CHOOSE_ACTION)
    await state.update_data(mp_selected_staff_id=staff_id, mp_selected_staff_name=staff_name)

    photo = await get_master_photo(company_id, staff_id)
    keyboard = _master_actions_kb(staff_id)
    if isinstance(target, CallbackQuery):
        message = target.message
    else:
        message = target

    if photo and message:
        await message.answer_photo(
            photo=photo["telegram_file_id"],
            caption=f"🖼️ Фото мастера: {staff_name}\nМожно заменить или удалить фото",
            reply_markup=keyboard,
        )
    elif message:
        await message.answer(
            f"🖼️ Для мастера {staff_name} фото пока не загружено",
            reply_markup=keyboard,
        )
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.callback_query(F.data == CB_SETTINGS_ROOT)
async def settings_root_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_settings_root(callback, state)


@router.callback_query(F.data == CB_PROFILE_ROOT)
async def settings_profile_root_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_profile_root(callback, state)


@router.callback_query(F.data == CB_PROFILE_EDIT_NAME)
async def settings_profile_edit_name(callback: CallbackQuery, state: FSMContext) -> None:
    user = await get_user_by_tg_id(callback.from_user.id)
    current_name = _safe_str((user or {}).get("name")) or "—"
    await state.set_state(ProfileSettingsStates.PROFILE_EDIT_NAME)
    if callback.message:
        await callback.message.answer(f"👤 Текущее имя: {current_name}\n\nВведите новое имя:")
    await callback.answer()


@router.message(ProfileSettingsStates.PROFILE_EDIT_NAME)
async def settings_profile_receive_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not _is_valid_name(value):
        await message.answer("⚠️ Укажите корректное имя (от 2 до 60 символов).")
        return
    await state.update_data(profile_new_name=value)
    await state.set_state(ProfileSettingsStates.PROFILE_CONFIRM_NAME)
    await message.answer(
        f"Проверьте новое имя:\n{value}",
        reply_markup=_profile_name_confirm_kb(),
    )


@router.callback_query(F.data == CB_PROFILE_RETRY_NAME)
async def settings_profile_retry_name(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileSettingsStates.PROFILE_EDIT_NAME)
    if callback.message:
        await callback.message.answer("Введите новое имя:")
    await callback.answer()


@router.callback_query(F.data == CB_PROFILE_SAVE_NAME)
async def settings_profile_save_name(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    new_name = _safe_str(data.get("profile_new_name"))
    if not _is_valid_name(new_name):
        if callback.message:
            await callback.message.answer("⚠️ Имя не прошло проверку. Введите его заново.")
        await state.set_state(ProfileSettingsStates.PROFILE_EDIT_NAME)
        await callback.answer()
        return
    user = await get_user_by_tg_id(callback.from_user.id)
    if not user:
        await _deny_access(callback, state)
        return
    client_id = _safe_str(user.get("yclients_client_id"))
    if not client_id.isdigit():
        if callback.message:
            await callback.message.answer("⚠️ Не найдена связка с YClients. Обратитесь к администратору.")
        await callback.answer()
        return

    logger.info("profile_name_update_attempt user_id=%s", callback.from_user.id)
    try:
        credentials, _ = await get_yclients_credentials()
        client, _ = await build_yclients_client()
        try:
            current_payload = await get_client_details(client, company_id=credentials.company_id, client_id=client_id)
            current_row = _extract_client_row(current_payload) or {}
        finally:
            await client.close()
        update_payload = build_yclients_client_update_payload(
            local_user=user,
            yclients_client_row=current_row,
            new_name=new_name,
        )
        await yclients_update_client(company_id=credentials.company_id, client_id=int(client_id), payload=update_payload)
        logger.info(
            "profile_name_update_yclients_success user_id=%s client_id=%s payload_keys=%s phone=%s",
            callback.from_user.id,
            client_id,
            sorted(update_payload.keys()),
            _mask_phone(update_payload.get("phone")),
        )
    except Exception as exc:
        user_error, safe_summary = _format_yclients_profile_update_error(action="name_update", exc=exc)
        logger.exception(
            "profile_name_update_yclients_failed user_id=%s client_id=%s action=name_update summary=%s",
            callback.from_user.id,
            client_id,
            safe_summary,
        )
        if callback.message:
            await callback.message.answer(user_error)
        await callback.answer()
        return

    try:
        await update_profile_name_phone(user_id=callback.from_user.id, name=new_name, match_source="settings_profile")
        logger.info("profile_name_update_db_success user_id=%s", callback.from_user.id)
    except Exception:
        logger.exception("profile_name_update_db_failed user_id=%s", callback.from_user.id)
        if callback.message:
            await callback.message.answer("⚠️ Имя обновлено в YClients, но локально возникла ошибка. Мы уже знаем о проблеме.")
        await callback.answer()
        return

    await clear_state_preserving_navigation(state)
    if callback.message:
        await callback.message.answer(f"✅ Имя обновлено, {new_name}")
    await _show_profile_root(callback, state)


@router.callback_query(F.data == CB_PROFILE_EDIT_PHONE)
async def settings_profile_edit_phone(callback: CallbackQuery, state: FSMContext) -> None:
    user = await get_user_by_tg_id(callback.from_user.id)
    current_phone = _safe_str((user or {}).get("phone")) or "—"
    await state.set_state(ProfileSettingsStates.PROFILE_EDIT_PHONE)
    if callback.message:
        await callback.message.answer(
            f"📱 Текущий телефон: {current_phone}\nОтправьте новый номер телефона или введите его вручную.",
            reply_markup=_profile_phone_input_kb(),
        )
    await callback.answer()


@router.message(ProfileSettingsStates.PROFILE_EDIT_PHONE, F.contact)
@router.message(ProfileSettingsStates.PROFILE_EDIT_PHONE, F.text)
async def settings_profile_receive_phone(message: Message, state: FSMContext) -> None:
    raw = message.contact.phone_number if message.contact else (message.text or "")
    bundle = normalize_phone(raw, default_region="RU")
    if not bundle.is_valid or not bundle.canonical_e164 or len(bundle.digits_only) < 10 or len(bundle.digits_only) > 15:
        await message.answer(
            "⚠️ Номер телефона указан некорректно.\n"
            "Проверьте номер и попробуйте ещё раз."
        )
        return
    duplicate = await find_other_user_by_phone_keys(
        current_user_id=message.from_user.id,
        phone_e164=bundle.canonical_e164,
        phone_ru_7=bundle.ru_11_with_7,
        phone_ru_8=bundle.ru_11_with_8,
    )
    if duplicate:
        await message.answer(
            "⚠️ Этот номер уже привязан к другому пользователю Telegram в системе.\n"
            "Пожалуйста, укажите другой телефон."
        )
        return
    current_user = await get_user_by_tg_id(message.from_user.id)
    current_phone = _safe_str((current_user or {}).get("phone")) or "—"
    await state.update_data(
        profile_new_phone=bundle.canonical_e164,
        profile_new_phone_raw=raw,
        profile_new_phone_digits=bundle.digits_only,
        profile_new_phone_ru7=bundle.ru_11_with_7,
        profile_new_phone_ru8=bundle.ru_11_with_8,
    )
    await state.set_state(ProfileSettingsStates.PROFILE_CONFIRM_PHONE)
    await message.answer(
        "Проверьте данные перед сохранением:\n"
        f"Текущий телефон: {current_phone}\n"
        f"Новый телефон: {bundle.canonical_e164}",
        reply_markup=_profile_phone_confirm_kb(),
    )


@router.callback_query(F.data == CB_PROFILE_RETRY_PHONE)
async def settings_profile_retry_phone(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileSettingsStates.PROFILE_EDIT_PHONE)
    if callback.message:
        await callback.message.answer(
            "Отправьте новый номер телефона или введите его вручную.",
            reply_markup=_profile_phone_input_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == CB_PROFILE_SAVE_PHONE)
async def settings_profile_save_phone(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    new_phone = _safe_str(data.get("profile_new_phone"))
    raw_phone = _safe_str(data.get("profile_new_phone_raw"))
    phone_digits = _safe_str(data.get("profile_new_phone_digits"))
    phone_ru7 = _safe_str(data.get("profile_new_phone_ru7"))
    phone_ru8 = _safe_str(data.get("profile_new_phone_ru8"))
    if not new_phone:
        if callback.message:
            await callback.message.answer("⚠️ Не удалось прочитать номер. Введите его заново.")
        await state.set_state(ProfileSettingsStates.PROFILE_EDIT_PHONE)
        await callback.answer()
        return
    user = await get_user_by_tg_id(callback.from_user.id)
    if not user:
        await _deny_access(callback, state)
        return
    client_id = _safe_str(user.get("yclients_client_id"))
    if not client_id.isdigit():
        if callback.message:
            await callback.message.answer("⚠️ Не найдена связка с YClients. Обратитесь к администратору.")
        await callback.answer()
        return
    current_client_id = int(client_id)
    credentials, _ = await get_yclients_credentials()
    conflict = await resolve_phone_change_conflict(
        current_user_id=callback.from_user.id,
        current_yclients_client_id=current_client_id,
        company_id=credentials.company_id,
        new_phone=new_phone,
    )
    if conflict.conflict_type == PhoneConflictType.AMBIGUOUS:
        logger.warning(
            "profile_phone_update_conflict_ambiguous user_id=%s phone=%s",
            callback.from_user.id,
            _mask_phone(new_phone),
        )
        if callback.message:
            await callback.message.answer(
                "⚠️ Мы нашли несколько карточек с похожим номером.\n"
                "Чтобы избежать ошибки, обратитесь в поддержку.",
                reply_markup=_profile_phone_conflict_kb(),
            )
        await callback.answer()
        return
    if conflict.conflict_type == PhoneConflictType.RELINK_BLOCKED:
        logger.warning(
            "profile_phone_relink_blocked user_id=%s target_client_id=%s linked_user_id=%s phone=%s",
            callback.from_user.id,
            conflict.found_client_id,
            conflict.linked_user_id,
            _mask_phone(new_phone),
        )
        if callback.message:
            await callback.message.answer(
                "⚠️ Этот номер уже привязан к другому клиенту в системе.\n"
                "Чтобы избежать путаницы, мы не можем изменить номер автоматически.\n"
                "Пожалуйста, обратитесь в поддержку.",
                reply_markup=_profile_phone_conflict_kb(),
            )
        await callback.answer()
        return
    if conflict.conflict_type == PhoneConflictType.RELINK_AVAILABLE:
        logger.info(
            "profile_phone_relink_offer user_id=%s current_client_id=%s target_client_id=%s phone=%s",
            callback.from_user.id,
            current_client_id,
            conflict.found_client_id,
            _mask_phone(new_phone),
        )
        await state.update_data(
            profile_relink_target_client_id=conflict.found_client_id,
            profile_relink_target_client_name=conflict.found_client_name,
        )
        await state.set_state(ProfileSettingsStates.PROFILE_CONFIRM_PHONE_RELINK)
        found_name_line = f"\nНайденная карточка: {conflict.found_client_name}" if conflict.found_client_name else ""
        if callback.message:
            await callback.message.answer(
                "⚠️ Этот номер уже есть в базе барбершопа.\n"
                "Похоже, для него уже существует карточка клиента.\n\n"
                "Мы можем привязать ваш аккаунт к уже существующей карточке с этим номером.\n\n"
                f"Номер: {new_phone}{found_name_line}\n\n"
                "Что сделать?",
                reply_markup=_profile_phone_relink_confirm_kb(),
            )
        await callback.answer()
        return

    logger.info("profile_phone_update_attempt user_id=%s phone=%s", callback.from_user.id, _mask_phone(new_phone))
    update_applied = False
    try:
        client, _ = await build_yclients_client()
        try:
            current_payload = await get_client_details(client, company_id=credentials.company_id, client_id=client_id)
            current_row = _extract_client_row(current_payload) or {}
        finally:
            await client.close()
        update_payload = build_yclients_client_update_payload(
            local_user=user,
            yclients_client_row=current_row,
            new_phone=new_phone,
        )
        await yclients_update_client(company_id=credentials.company_id, client_id=int(client_id), payload=update_payload)
        update_applied = True
        logger.info(
            "profile_phone_update_yclients_success user_id=%s client_id=%s payload_keys=%s phone=%s",
            callback.from_user.id,
            client_id,
            sorted(update_payload.keys()),
            _mask_phone(update_payload.get("phone")),
        )
    except Exception as exc:
        user_error, safe_summary = _format_yclients_profile_update_error(action="phone_update", exc=exc)
        logger.exception(
            "profile_phone_update_yclients_failed user_id=%s client_id=%s action=phone_update phone=%s summary=%s",
            callback.from_user.id,
            client_id,
            _mask_phone(new_phone),
            safe_summary,
        )
        if callback.message:
            await callback.message.answer(user_error)
        await callback.answer()
        return

    try:
        await update_profile_phone_and_mapping(
            user_id=callback.from_user.id,
            phone=new_phone,
            phone_raw=raw_phone,
            phone_digits=phone_digits,
            phone_e164=new_phone,
            phone_ru_7=phone_ru7,
            phone_ru_8=phone_ru8,
            yclients_client_id=current_client_id,
            match_source="settings_profile",
        )
        logger.info(
            "profile_phone_update_db_success user_id=%s phone=%s yclients_client_id=%s update_applied=%s",
            callback.from_user.id,
            _mask_phone(new_phone),
            current_client_id,
            update_applied,
        )
    except Exception:
        logger.exception("profile_phone_update_db_failed user_id=%s", callback.from_user.id)
        if callback.message:
            await callback.message.answer("⚠️ Телефон обновлён в YClients, но локально возникла ошибка. Мы уже знаем о проблеме.")
        await callback.answer()
        return

    await clear_state_preserving_navigation(state)
    if callback.message:
        profile_name = _safe_str(user.get("name"))
        success_text = f"✅ {profile_name}, телефон обновлён" if profile_name else "✅ Телефон обновлён"
        await callback.message.answer(success_text, reply_markup=ReplyKeyboardRemove())
    await _show_profile_root(callback, state)


@router.callback_query(F.data == CB_PROFILE_RELINK_CONFIRM)
async def settings_profile_relink_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    new_phone = _safe_str(data.get("profile_new_phone"))
    raw_phone = _safe_str(data.get("profile_new_phone_raw"))
    phone_digits = _safe_str(data.get("profile_new_phone_digits"))
    phone_ru7 = _safe_str(data.get("profile_new_phone_ru7"))
    phone_ru8 = _safe_str(data.get("profile_new_phone_ru8"))
    target_client_id_raw = _safe_str(data.get("profile_relink_target_client_id"))
    if not new_phone or not target_client_id_raw.isdigit():
        if callback.message:
            await callback.message.answer("⚠️ Не удалось завершить привязку. Введите номер заново.")
        await state.set_state(ProfileSettingsStates.PROFILE_EDIT_PHONE)
        await callback.answer()
        return
    target_client_id = int(target_client_id_raw)
    linked_user = await find_other_user_by_yclients_client_id(
        current_user_id=callback.from_user.id,
        yclients_client_id=target_client_id,
    )
    if linked_user:
        logger.warning(
            "profile_phone_relink_blocked_recheck user_id=%s target_client_id=%s linked_user_id=%s",
            callback.from_user.id,
            target_client_id,
            linked_user.get("user_id"),
        )
        if callback.message:
            await callback.message.answer(
                "⚠️ Этот номер уже используется в системе.\n"
                "Мы не можем изменить его автоматически. Обратитесь в поддержку.",
                reply_markup=_profile_phone_conflict_kb(),
            )
        await callback.answer()
        return
    try:
        await update_profile_phone_and_mapping(
            user_id=callback.from_user.id,
            phone=new_phone,
            phone_raw=raw_phone,
            phone_digits=phone_digits,
            phone_e164=new_phone,
            phone_ru_7=phone_ru7,
            phone_ru_8=phone_ru8,
            yclients_client_id=target_client_id,
            match_source="settings_profile_relink",
        )
        logger.info(
            "profile_phone_relink_success user_id=%s target_client_id=%s phone=%s",
            callback.from_user.id,
            target_client_id,
            _mask_phone(new_phone),
        )
    except Exception:
        logger.exception(
            "profile_phone_relink_db_failed user_id=%s target_client_id=%s",
            callback.from_user.id,
            target_client_id,
        )
        if callback.message:
            await callback.message.answer(
                "⚠️ Не удалось обновить номер телефона.\n"
                "Попробуйте позже или обратитесь в поддержку."
            )
        await callback.answer()
        return
    await clear_state_preserving_navigation(state)
    if callback.message:
        await callback.message.answer(
            "✅ Телефон обновлён\nВаш аккаунт привязан к существующей карточке клиента в YClients.",
            reply_markup=ReplyKeyboardRemove(),
        )
    await _show_profile_root(callback, state)


@router.callback_query(F.data == CB_PROFILE_BACK)
async def settings_profile_back(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state in {ProfileSettingsStates.PROFILE_CONFIRM_NAME.state, ProfileSettingsStates.PROFILE_EDIT_NAME.state}:
        await _show_profile_root(callback, state)
        return
    if current_state in {
        ProfileSettingsStates.PROFILE_CONFIRM_PHONE.state,
        ProfileSettingsStates.PROFILE_EDIT_PHONE.state,
        ProfileSettingsStates.PROFILE_CONFIRM_PHONE_RELINK.state,
    }:
        await _remove_contact_keyboard(callback)
        await _show_profile_root(callback, state)
        return
    await _show_settings_root(callback, state)


@router.message(
    ProfileSettingsStates.PROFILE_EDIT_PHONE,
    F.text.in_({BACK, HOME}),
)
@router.message(
    ProfileSettingsStates.PROFILE_CONFIRM_PHONE,
    F.text.in_({BACK, HOME}),
)
@router.message(
    ProfileSettingsStates.PROFILE_CONFIRM_PHONE_RELINK,
    F.text.in_({BACK, HOME}),
)
async def settings_profile_phone_message_nav(message: Message, state: FSMContext) -> None:
    await _remove_contact_keyboard(message)
    if (message.text or "").strip() == HOME:
        await clear_state_preserving_navigation(state)
        await reset_stack(state)
        await render_main_menu(message, message.from_user.id)
        return
    await _show_profile_root(message, state)

@router.callback_query(F.data == CB_PHOTOS_ROOT)
async def settings_photos_root_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_staff_list(callback, state)


@router.callback_query(F.data == CB_CONTACTS_ROOT)
async def settings_contacts_root_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_contacts_editor(callback, state)


@router.callback_query(F.data == CB_CONTACTS_BACK)
async def settings_contacts_back_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_settings_root(callback, state)


@router.callback_query(F.data == CB_CONTACTS_EDIT_ADDRESS)
async def settings_contacts_edit_address(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    await state.set_state(ContactsEditStates.CONTACTS_EDIT_ADDRESS)
    if callback.message:
        await callback.message.answer("🏠 Введите новый адрес:")
    await callback.answer()


@router.callback_query(F.data == CB_CONTACTS_EDIT_PHONE)
async def settings_contacts_edit_phone(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    await state.set_state(ContactsEditStates.CONTACTS_EDIT_PHONE)
    if callback.message:
        await callback.message.answer("📞 Введите новый телефон:")
    await callback.answer()


@router.callback_query(F.data == CB_CONTACTS_EDIT_SCHEDULE)
async def settings_contacts_edit_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    await state.set_state(ContactsEditStates.CONTACTS_EDIT_SCHEDULE)
    if callback.message:
        await callback.message.answer("⏰ Введите новый режим работы:")
    await callback.answer()


@router.callback_query(F.data == CB_CONTACTS_PREVIEW)
async def settings_contacts_preview(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    data = await resolve_contacts()
    if callback.message:
        await callback.message.answer(render_contacts_block(data.resolved), reply_markup=_contacts_edit_kb())
    await state.set_state(ContactsEditStates.CONTACTS_EDIT_MENU)
    await callback.answer()


@router.callback_query(F.data == CB_CONTACTS_RESET)
async def settings_contacts_reset(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    credentials, _ = await get_yclients_credentials()
    await clear_contacts_override(credentials.company_id)
    await _log_settings_action(callback.from_user.id, "contacts_reset", "сбросил контакты филиала к данным из YClients", company_id=credentials.company_id)
    if callback.message:
        await callback.message.answer(
            "♻️ Локальные правки контактов сброшены. Теперь используются данные из YClients."
        )
    await _show_contacts_editor(callback, state)


@router.callback_query(F.data == CB_SUPPORT_ROOT)
async def settings_support_root_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_support_settings_editor(callback, state)


@router.callback_query(F.data == CB_SUPPORT_BACK)
async def settings_support_back_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_settings_root(callback, state)


@router.callback_query(F.data == CB_SUPPORT_EDIT_DESCRIPTION)
async def settings_support_edit_description(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    await state.set_state(SupportSettingsStates.SUPPORT_EDIT_DESCRIPTION)
    if callback.message:
        await callback.message.answer("✏️ Введите новое описание для раздела поддержки:")
    await callback.answer()


@router.callback_query(F.data == CB_SUPPORT_EDIT_USERNAME)
async def settings_support_edit_username(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    await state.set_state(SupportSettingsStates.SUPPORT_EDIT_USERNAME)
    if callback.message:
        await callback.message.answer("👤 Введите username аккаунта поддержки (можно с @):")
    await callback.answer()


@router.callback_query(F.data == CB_SUPPORT_PREVIEW)
async def settings_support_preview(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    _, effective, _ = await resolve_support_settings()
    if callback.message:
        await callback.message.answer(
            render_support_message(effective.description),
            reply_markup=support_screen_kb(username=effective.username, include_home=True),
        )
    await state.set_state(SupportSettingsStates.SUPPORT_SETTINGS_MENU)
    await callback.answer()


@router.callback_query(F.data == CB_SUPPORT_RESET)
async def settings_support_reset(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    company_id, _, _ = await resolve_support_settings()
    await reset_support_settings(company_id, updated_by_tg_id=callback.from_user.id)
    await _log_settings_action(callback.from_user.id, "support_reset", "сбросил настройки поддержки", company_id=company_id)
    if callback.message:
        await callback.message.answer("♻️ Раздел \"Поддержка\" сброшен к значениям по умолчанию.")
    await _show_support_settings_editor(callback, state)


@router.message(SupportSettingsStates.SUPPORT_EDIT_DESCRIPTION)
async def settings_support_receive_description(message: Message, state: FSMContext) -> None:
    if not await _is_allowed(message.from_user.id):
        await _deny_access(message, state)
        return
    value = (message.text or "").strip()
    if not value:
        await message.answer("⚠️ Описание не может быть пустым. Введите текст ещё раз.")
        return
    company_id, _, _ = await resolve_support_settings()
    await upsert_support_settings(
        company_id=company_id,
        support_description=value,
        updated_by_tg_id=message.from_user.id,
    )
    await _log_settings_action(message.from_user.id, "support_description_changed", "изменил описание поддержки", company_id=company_id)
    await message.answer("✅ Описание поддержки обновлено")
    await _show_support_settings_editor(message, state)


@router.message(SupportSettingsStates.SUPPORT_EDIT_USERNAME)
async def settings_support_receive_username(message: Message, state: FSMContext) -> None:
    if not await _is_allowed(message.from_user.id):
        await _deny_access(message, state)
        return
    raw_value = message.text or ""
    normalized = normalize_support_username(raw_value)
    if not normalized:
        await message.answer(
            "⚠️ Некорректный username. Укажите Telegram username (5-32 символа, латиница/цифры/_)"
        )
        return
    company_id, _, _ = await resolve_support_settings()
    await upsert_support_settings(
        company_id=company_id,
        support_username=normalized,
        updated_by_tg_id=message.from_user.id,
    )
    await _log_settings_action(message.from_user.id, "support_account_changed", "изменил аккаунт поддержки", company_id=company_id)
    await message.answer(f"✅ Аккаунт поддержки обновлён: @{normalized}")
    await clear_state_preserving_navigation(state)
    await reset_stack(state)
    await _show_support_settings_editor(message, state)


async def _save_contact_field(message: Message, state: FSMContext, *, field: str, value: str) -> None:
    if not await _is_allowed(message.from_user.id):
        await _deny_access(message, state)
        return
    credentials, _ = await get_yclients_credentials()
    payload = {"company_id": credentials.company_id, "updated_by_tg_id": message.from_user.id}
    payload[field] = value.strip()
    await upsert_contacts_override(**payload)
    action_type, human_tail = CONTACT_FIELD_ACTIONS.get(field, ("contacts_changed", "изменил контакты филиала"))
    await _log_settings_action(message.from_user.id, action_type, human_tail, company_id=credentials.company_id, field=field)
    await message.answer("✅ Контакты обновлены")
    await _show_contacts_editor(message, state)


@router.message(ContactsEditStates.CONTACTS_EDIT_ADDRESS)
async def settings_contacts_receive_address(message: Message, state: FSMContext) -> None:
    await _save_contact_field(message, state, field="address", value=message.text or "")


@router.message(ContactsEditStates.CONTACTS_EDIT_PHONE)
async def settings_contacts_receive_phone(message: Message, state: FSMContext) -> None:
    await _save_contact_field(message, state, field="phone", value=message.text or "")


@router.message(ContactsEditStates.CONTACTS_EDIT_SCHEDULE)
async def settings_contacts_receive_schedule(message: Message, state: FSMContext) -> None:
    await _save_contact_field(message, state, field="schedule", value=message.text or "")


@router.callback_query(F.data.startswith(f"{CB_PHOTO_SELECT}:"))
async def settings_photo_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    staff_id = callback.data.removeprefix(f"{CB_PHOTO_SELECT}:")
    await _show_master_card(callback, state, staff_id)


@router.callback_query(F.data.startswith(f"{CB_PHOTO_UPLOAD}:"))
async def settings_photo_upload_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    staff_id = callback.data.removeprefix(f"{CB_PHOTO_UPLOAD}:")
    data = await state.get_data()
    selected_staff_id = _safe_str(data.get("mp_selected_staff_id"))
    staff_name = _safe_str(data.get("mp_selected_staff_name"))
    if selected_staff_id != staff_id:
        await callback.answer("Сначала выберите мастера 🙂", show_alert=True)
        return

    await state.set_state(MasterPhotoStates.WAIT_UPLOAD)
    if callback.message:
        await callback.message.answer(f"📸 Отправьте одно фото для мастера {staff_name} 😊")
    await callback.answer()


@router.message(MasterPhotoStates.WAIT_UPLOAD, ~F.photo)
async def settings_photo_upload_reject_non_photo(message: Message) -> None:
    await message.answer("📸 Пожалуйста, отправьте именно фотографию 🙂")


@router.message(MasterPhotoStates.WAIT_UPLOAD, F.photo)
async def settings_photo_upload_receive(message: Message, state: FSMContext) -> None:
    if not await _is_allowed(message.from_user.id):
        await _deny_access(message, state)
        return

    data = await state.get_data()
    company_id = _safe_str(data.get("mp_company_id"))
    staff_id = _safe_str(data.get("mp_selected_staff_id"))
    staff_name = _safe_str(data.get("mp_selected_staff_name"))
    if not (company_id and staff_id and staff_name):
        await message.answer("⚠️ Не удалось определить мастера. Попробуйте заново 🙂")
        await _show_settings_root(message, state)
        return

    largest = max(message.photo, key=lambda p: p.file_size or 0)
    await upsert_master_photo(
        company_id=company_id,
        staff_id=staff_id,
        staff_name=staff_name,
        file_id=largest.file_id,
        updated_by_tg_id=message.from_user.id,
    )
    await _log_settings_action(message.from_user.id, "master_photo_changed", f"загрузил или заменил фото мастера {staff_name}", company_id=company_id, staff_id=staff_id)
    await message.answer("✅ Фото мастера обновлено")
    await state.set_state(MasterPhotoStates.CHOOSE_ACTION)
    await _show_master_card(message, state, staff_id)


@router.callback_query(F.data.startswith(f"{CB_PHOTO_DELETE}:"))
async def settings_photo_delete_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_allowed(callback.from_user.id):
        await _deny_access(callback, state)
        return
    data = await state.get_data()
    company_id = _safe_str(data.get("mp_company_id"))
    staff_id = callback.data.removeprefix(f"{CB_PHOTO_DELETE}:")
    staff_name = _safe_str(data.get("mp_selected_staff_name"))
    if company_id and staff_id:
        await delete_master_photo(company_id, staff_id)
        await _log_settings_action(callback.from_user.id, "master_photo_deleted", f"удалил фото мастера {staff_name or staff_id}", company_id=company_id, staff_id=staff_id)
    if callback.message:
        await callback.message.answer("🗑️ Фото мастера удалено")
    await _show_master_card(callback, state, staff_id)


@router.callback_query(F.data == CB_PHOTO_BACK)
async def settings_photo_back(callback: CallbackQuery, state: FSMContext) -> None:
    await _remove_contact_keyboard(callback)
    await back_handler(callback, state)


@router.callback_query(F.data == CB_PHOTO_HOME)
async def settings_photo_home(callback: CallbackQuery, state: FSMContext) -> None:
    await _remove_contact_keyboard(callback)
    await home_handler(callback, state)
