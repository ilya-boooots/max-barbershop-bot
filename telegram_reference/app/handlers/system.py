from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.navigation import clear_state_preserving_navigation
from app.core.navigation import render_main_by_role
from app.core.permissions import is_developer, is_manager
from app.core.status import build_status_text
from app.repositories.diagnostics import log_bot_event
from app.core.error_monitor import clear_error_events_storage, get_error_events
from app.repositories.users import get_user as get_db_user
from app.ui.texts import RESET_DONE, RESET_DONE_UNREGISTERED, STATUS_ERROR
from app.utils.datetime import format_branch_datetime

router = Router()


def _errors_kb(include_clear: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="errors:refresh")]]
    if include_clear:
        rows.append([InlineKeyboardButton(text="🧹 Очистить", callback_data="errors:clear")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)



async def _render_errors(message: Message, tg_id: int) -> None:
    rows = await get_error_events(limit=10)
    developer = await is_developer(tg_id)
    if not rows:
        text = "🧯 Ошибки\n\nПока ошибок не зафиксировано ✅"
        await message.answer(text, reply_markup=_errors_kb(developer))
        return
    lines = ["🧯 Ошибки (последние 10):"]
    for row in rows:
        lines.append(
            f"• 🧩 {row['fingerprint']} | {row['error_type']}\n"
            f"  📍 {row['where']}\n"
            f"  📈 {row['count']} | 🕒 {await format_branch_datetime(row['last_seen'])}"
        )
    await message.answer("\n".join(lines)[:3900], reply_markup=_errors_kb(developer))


@router.message(Command("reset"), StateFilter("*"))
async def handle_reset(message: Message, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    user = await get_db_user(message.from_user.id)
    if user and user["is_registered"]:
        await message.answer(RESET_DONE)
        await render_main_by_role(message, message.from_user.id)
        return
    await message.answer(RESET_DONE_UNREGISTERED)


@router.message(Command("status"))
async def handle_status(message: Message) -> None:
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав для просмотра статуса.")
        return
    try:
        text = await build_status_text(message.from_user.id)
        await message.answer(text, parse_mode="Markdown")
    except Exception as exc:
        await log_bot_event(
            level="ERROR",
            source="status",
            message=f"status command failed: {type(exc).__name__}",
            details={"user_id": message.from_user.id},
        )
        await message.answer(STATUS_ERROR)


@router.message(Command("errors"))
@router.message(F.text == "🧯 Ошибки")
async def handle_errors(message: Message) -> None:
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа к ошибкам.")
        return
    await _render_errors(message, message.from_user.id)


@router.callback_query(F.data == "errors:refresh")
async def handle_errors_refresh(callback: CallbackQuery) -> None:
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    await _render_errors(callback.message, callback.from_user.id)
    await callback.answer("Обновлено ✅")


@router.callback_query(F.data == "errors:clear")
async def handle_errors_clear(callback: CallbackQuery) -> None:
    if not await is_developer(callback.from_user.id):
        await callback.answer("⛔️ Только для разработчика", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    await clear_error_events_storage()
    await callback.message.answer("🧹 Счётчики ошибок очищены ✅")
    await _render_errors(callback.message, callback.from_user.id)
    await callback.answer("Очищено")


@router.message(Command("crash_test"))
async def handle_crash_test(message: Message) -> None:
    if not await is_developer(message.from_user.id):
        await message.answer("⛔️ Команда только для разработчика.")
        return
    raise RuntimeError("Тестовый критический сбой /crash_test")
