from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.navigation import clear_state_preserving_navigation, push_screen
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, require_roles
from app.core.staff_permissions import can_manage_yclients, resolve_role
from app.core.ui_texts import YCLIENTS_INTEGRATION_BTN
from app.integrations.yclients import YClientsCredentials, yclients_health_check
from app.repositories.staff_action_logs import add_staff_action_log
from app.repositories.users import get_user_by_tg_id
from app.repositories.yclients_settings import (
    YClientsSettings,
    get_yclients_settings,
    reset_yclients_settings,
    upsert_yclients_settings,
)

router = Router()

CB_SETUP = "yclients:setup"
CB_CHECK = "yclients:check"
CB_RESET = "yclients:reset"
CB_RESET_YES = "yclients:reset:yes"
CB_RESET_NO = "yclients:reset:no"
CB_SKIP_USER = "yclients:skip_user"
CB_SKIP_BASE = "yclients:skip_base"


class YClientsSetupStates(StatesGroup):
    waiting_company_id = State()
    waiting_partner_token = State()
    waiting_user_token = State()
    waiting_base_url = State()


@dataclass
class Draft:
    company_id: str
    partner_token: str
    user_token: str | None
    base_url: str | None


def _mask_secret(value: str | None) -> str:
    if not value:
        return "—"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-2:]}"


def _status_text(settings: YClientsSettings | None) -> str:
    status = "✅ Подключено" if settings and settings.company_id and settings.partner_token else "❌ Не подключено"
    return (
        "⚙️ Интеграция YClients\n\n"
        f"Статус: {status}\n"
        f"🏢 Company ID: {_mask_secret(settings.company_id if settings else None)}\n"
        f"🔐 Partner token: {_mask_secret(settings.partner_token if settings else None)}\n"
        f"👤 User token: {_mask_secret(settings.user_token if settings else None)}\n"
        f"🌐 Base URL: {settings.base_url if settings and settings.base_url else 'по умолчанию'}"
    )


async def _can_manage(user_id: int) -> bool:
    user = await get_user_by_tg_id(user_id)
    role = resolve_role(user_id, user.get("role") if user else None)
    return can_manage_yclients(role)


def _entry_kb(*, can_manage: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_manage:
        rows.append([InlineKeyboardButton(text="🧩 Настроить / Изменить", callback_data=CB_SETUP)])
    rows.append([InlineKeyboardButton(text="🔌 Проверить подключение", callback_data=CB_CHECK)])
    if can_manage:
        rows.append([InlineKeyboardButton(text="🧹 Сбросить настройки", callback_data=CB_RESET)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _wizard_nav_kb(*, extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.extend([
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav:back")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _friendly_result_text(status_code: int | None) -> str:
    if status_code in {401, 403}:
        return "🔐 Не удалось авторизоваться. Проверьте токены и попробуйте снова."
    if status_code == 429:
        return "⏳ Слишком много запросов. Попробуйте повторить немного позже."
    if status_code and status_code >= 500:
        return "🛠️ Сервис YClients временно недоступен. Повторите позже."
    return "🌐 Не удалось связаться с YClients. Проверьте URL и интернет-соединение."


async def _show_entry(target: Message | CallbackQuery, state: FSMContext) -> None:
    await push_screen(state, "yclients_setup")
    settings = await get_yclients_settings()
    can_manage = await _can_manage(target.from_user.id)
    text = _status_text(settings)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=_entry_kb(can_manage=can_manage))
        await target.answer()
        return
    await target.answer(text, reply_markup=_entry_kb(can_manage=can_manage))


@router.message(F.text == YCLIENTS_INTEGRATION_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def open_setup_menu(message: Message, state: FSMContext) -> None:
    await add_staff_action_log(message.from_user.id, "Открыл раздел интеграции YClients")
    await _show_entry(message, state)


@router.callback_query(F.data == CB_SETUP)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def start_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await add_staff_action_log(callback.from_user.id, "Открыл редактирование интеграции YClients")
    await state.set_state(YClientsSetupStates.waiting_company_id)
    await callback.message.answer("📝 Шаг 1/4\nВведите Company ID YClients:", reply_markup=_wizard_nav_kb())
    await callback.answer()


@router.message(YClientsSetupStates.waiting_company_id)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def handle_company_id(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value:
        await message.answer("⚠️ Company ID не может быть пустым. Попробуйте снова.", reply_markup=_wizard_nav_kb())
        return
    await state.update_data(yclients_company_id=value)
    await state.set_state(YClientsSetupStates.waiting_partner_token)
    await message.answer("📝 Шаг 2/4\nВведите Partner token:", reply_markup=_wizard_nav_kb())


@router.message(YClientsSetupStates.waiting_partner_token)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def handle_partner_token(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if len(value) < 8:
        await message.answer("⚠️ Похоже, токен слишком короткий. Введите Partner token ещё раз.", reply_markup=_wizard_nav_kb())
        return
    await state.update_data(yclients_partner_token=value)
    await state.set_state(YClientsSetupStates.waiting_user_token)
    await message.answer(
        "📝 Шаг 3/4\nВведите User token или нажмите «⏭️ Пропустить»:",
        reply_markup=_wizard_nav_kb(extra_rows=[[InlineKeyboardButton(text="⏭️ Пропустить", callback_data=CB_SKIP_USER)]]),
    )


@router.callback_query(F.data == CB_SKIP_USER)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def skip_user_token(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(yclients_user_token=None)
    await state.set_state(YClientsSetupStates.waiting_base_url)
    await callback.message.answer(
        "📝 Шаг 4/4\nВведите Base URL или нажмите «⏭️ По умолчанию»:",
        reply_markup=_wizard_nav_kb(extra_rows=[[InlineKeyboardButton(text="⏭️ По умолчанию", callback_data=CB_SKIP_BASE)]]),
    )
    await callback.answer()


@router.message(YClientsSetupStates.waiting_user_token)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def handle_user_token(message: Message, state: FSMContext) -> None:
    await state.update_data(yclients_user_token=(message.text or "").strip() or None)
    await state.set_state(YClientsSetupStates.waiting_base_url)
    await message.answer(
        "📝 Шаг 4/4\nВведите Base URL или нажмите «⏭️ По умолчанию»:",
        reply_markup=_wizard_nav_kb(extra_rows=[[InlineKeyboardButton(text="⏭️ По умолчанию", callback_data=CB_SKIP_BASE)]]),
    )


@router.callback_query(F.data == CB_SKIP_BASE)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def skip_base_url(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(yclients_base_url=None)
    await _validate_draft(callback.message, state)
    await callback.answer()


@router.message(YClientsSetupStates.waiting_base_url)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def handle_base_url(message: Message, state: FSMContext) -> None:
    base_url = (message.text or "").strip() or None
    if base_url and not base_url.startswith("http"):
        await message.answer("⚠️ Base URL должен начинаться с http:// или https://", reply_markup=_wizard_nav_kb())
        return
    await state.update_data(yclients_base_url=base_url)
    await _validate_draft(message, state)


async def _validate_draft(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    draft = Draft(
        company_id=str(data.get("yclients_company_id") or "").strip(),
        partner_token=str(data.get("yclients_partner_token") or "").strip(),
        user_token=data.get("yclients_user_token"),
        base_url=data.get("yclients_base_url"),
    )
    if not draft.company_id or not draft.partner_token:
        await message.answer("⚠️ Не хватает обязательных полей. Начните настройку заново.", reply_markup=_entry_kb(can_manage=await _can_manage(message.from_user.id)))
        await state.clear()
        return

    result = await yclients_health_check(
        credentials_override=YClientsCredentials(company_id=draft.company_id, partner_token=draft.partner_token, user_token=draft.user_token),
        base_url_override=draft.base_url,
    )
    if result.ok:
        await upsert_yclients_settings(
            company_id=draft.company_id,
            partner_token=draft.partner_token,
            user_token=draft.user_token,
            base_url=draft.base_url,
            updated_by_tg_id=message.from_user.id,
        )
        await state.clear()
        await add_staff_action_log(message.from_user.id, "Изменил настройки интеграции YClients")
        await message.answer("✅ Проверка прошла успешно! Настройки сохранены и подключение работает.", reply_markup=_entry_kb(can_manage=await _can_manage(message.from_user.id)))
        return

    await message.answer(
        f"❌ Подключение не подтверждено. {_friendly_result_text(result.status_code)}\nВы можете изменить данные и попробовать снова.",
        reply_markup=_wizard_nav_kb(extra_rows=[[InlineKeyboardButton(text="🔁 Повторить настройку", callback_data=CB_SETUP)]]),
    )


@router.callback_query(F.data == CB_CHECK)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def check_connection(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    result = await yclients_health_check()
    text = "✅ Подключение к YClients работает!" if result.ok else f"❌ Проверка не пройдена. {_friendly_result_text(result.status_code)}"
    await callback.message.answer(text, reply_markup=_entry_kb(can_manage=await _can_manage(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == CB_RESET)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def ask_reset(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "❗️Точно сбросить настройки YClients?",
        reply_markup=_wizard_nav_kb(extra_rows=[[InlineKeyboardButton(text="✅ Да", callback_data=CB_RESET_YES), InlineKeyboardButton(text="❌ Нет", callback_data=CB_RESET_NO)]]),
    )
    await callback.answer()


@router.callback_query(F.data == CB_RESET_YES)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def confirm_reset(callback: CallbackQuery, state: FSMContext) -> None:
    await add_staff_action_log(callback.from_user.id, "Удалил настройки интеграции YClients")
    await reset_yclients_settings(updated_by_tg_id=callback.from_user.id)
    await clear_state_preserving_navigation(state)
    await callback.message.answer("🧹 Настройки YClients сброшены.", reply_markup=_entry_kb(can_manage=await _can_manage(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == CB_RESET_NO)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def cancel_reset(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await callback.message.answer("👌 Сброс отменён.", reply_markup=_entry_kb(can_manage=await _can_manage(callback.from_user.id)))
    await callback.answer()
