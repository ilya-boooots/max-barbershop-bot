from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.core.navigation import push_screen
from app.core.auth import normalize_role
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, require_roles
from app.core.screens import render_personnel_menu
from app.core.staff_permissions import can_view_personnel, resolve_role
from app.core.ui_texts import (
    CONTACTS_BTN,
    DEV_ADMIN_PANEL_BTN,
    MESSAGES_BTN,
    PERSONNEL_BTN,
    SETTINGS_BTN,
    SUPPORT_BTN,
    YCHECK_BTN,
)
from app.handlers.master_photos_settings import open_settings_menu
from app.integrations.yclients import yclients_health_check
from app.repositories.staff_action_logs import add_staff_action_log
from app.repositories.users import get_user_by_tg_id
from app.services.contacts import render_contacts_block, resolve_contacts
from app.services.support import render_support_message, resolve_support_settings, support_screen_kb
from app.ui.navigation import nav_inline_kb
from app.ui.texts import DEV_ADMIN_PLACEHOLDER, MESSAGES_PLACEHOLDER, NO_ACCESS

router = Router()


@router.message(F.text == CONTACTS_BTN)
async def handle_contacts(message: Message, state: FSMContext) -> None:
    await push_screen(state, "contacts")
    contacts = await resolve_contacts()
    await message.answer(render_contacts_block(contacts.resolved), reply_markup=nav_inline_kb())


@router.message(F.text == SUPPORT_BTN)
async def handle_support(message: Message, state: FSMContext) -> None:
    await push_screen(state, "support")
    _, support_settings, _ = await resolve_support_settings()
    await message.answer(
        render_support_message(support_settings.description),
        reply_markup=support_screen_kb(username=support_settings.username, include_home=True),
    )


@router.message(F.text == PERSONNEL_BTN)
async def handle_personnel(message: Message, state: FSMContext) -> None:
    user = await get_user_by_tg_id(message.from_user.id)
    role = resolve_role(message.from_user.id, user.get("role") if user else None)
    if not can_view_personnel(role):
        await message.answer(NO_ACCESS)
        return
    await add_staff_action_log(message.from_user.id, "Открыл раздел «Персонал»")
    await push_screen(state, "personnel_menu")
    await render_personnel_menu(message)


@router.message(F.text == SETTINGS_BTN)
async def handle_settings(message: Message, state: FSMContext) -> None:
    user = await get_user_by_tg_id(message.from_user.id)
    role = normalize_role(message.from_user.id, user.get("role") if user else None)
    if role != "developer" and not (user and user.get("is_registered")):
        await message.answer("Сначала пройдите регистрацию: нажмите /start")
        return
    await push_screen(state, "settings")
    await open_settings_menu(message, state)


@router.message(F.text == MESSAGES_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_messages(message: Message, state: FSMContext) -> None:
    await push_screen(state, "messages")
    await message.answer(MESSAGES_PLACEHOLDER, reply_markup=nav_inline_kb())


@router.message(F.text == DEV_ADMIN_PANEL_BTN)
@require_roles(ROLE_DEVELOPER)
async def handle_dev_admin_panel(message: Message, state: FSMContext) -> None:
    await push_screen(state, "dev_admin_panel")
    await message.answer(DEV_ADMIN_PLACEHOLDER, reply_markup=nav_inline_kb())


@router.message(F.text == YCHECK_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def handle_yclients_check(message: Message, state: FSMContext) -> None:
    await push_screen(state, "yclients_check")
    result = await yclients_health_check()
    if result.ok:
        await message.answer("✅ YClients подключен и отвечает корректно 👌", reply_markup=nav_inline_kb())
        return

    if result.status_code == 429:
        hint = "⏳ Лимит API временно исчерпан. Попробуйте чуть позже."
    elif result.status_code in {401, 403}:
        hint = "🔐 Проверьте токены и права доступа в настройках."
    elif result.status_code and result.status_code >= 500:
        hint = "🛠️ Похоже, сервис YClients сейчас недоступен."
    else:
        hint = "⚙️ Проверьте базовый URL, Company ID и сетевое подключение."

    await message.answer(f"❌ Не удалось проверить YClients. {hint}", reply_markup=nav_inline_kb())
