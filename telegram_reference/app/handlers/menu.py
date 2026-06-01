from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.auth import normalize_role
from app.core.navigation import push_screen
from app.core.screens import (
    render_account_info,
    render_balance,
    render_digital_menu,
    render_virtual_card,
)
from app.keyboards.menu import DETAILS_CALLBACK
from app.repositories.users import get_user as get_db_user

router = Router()

async def _get_access_context(message: Message) -> tuple[dict | None, str, bool]:
    user = await get_db_user(message.from_user.id)
    role = normalize_role(message.from_user.id, user["role"] if user else None)
    if role == "developer":
        return user, role, True
    return user, role, bool(user and user["is_registered"])



@router.message(F.text == "📊 Информация по счету")
async def handle_account_info(message: Message, state: FSMContext) -> None:
    _, _, allowed = await _get_access_context(message)
    if not allowed:
        return
    await push_screen(state, "account_info")
    await render_account_info(message)


@router.callback_query(F.data == DETAILS_CALLBACK)
async def handle_details_stub(callback: CallbackQuery) -> None:
    user = await get_db_user(callback.from_user.id)
    role = normalize_role(callback.from_user.id, user["role"] if user else None)
    if role != "developer" and (not user or not user["is_registered"]):
        await callback.answer()
        return
    await callback.answer("Скоро здесь появятся детали.")


@router.message(F.text == "💳 Баланс счёта")
async def handle_balance(message: Message, state: FSMContext) -> None:
    _, _, allowed = await _get_access_context(message)
    if not allowed:
        await message.answer("Сначала пройдите регистрацию: нажмите /start")
        return
    await push_screen(state, "balance")
    await render_balance(message)


@router.message(F.text == "💳 Виртуальная карта")
async def handle_virtual_card(message: Message, state: FSMContext) -> None:
    _, _, allowed = await _get_access_context(message)
    if not allowed:
        return
    await push_screen(state, "virtual_card")
    await render_virtual_card(message)


@router.message(F.text == "⭐️ Электронное меню")
async def handle_digital_menu(message: Message, state: FSMContext) -> None:
    _, _, allowed = await _get_access_context(message)
    if not allowed:
        return
    await push_screen(state, "digital_menu")
    await render_digital_menu(message)
