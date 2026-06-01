from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.navigation import back_handler, clear_state_preserving_navigation, home_handler, render_main_menu, render_previous_screen, reset_stack

router = Router()


@router.callback_query(F.data == NAV_BACK_CALLBACK)
async def handle_inline_back(callback: CallbackQuery, state: FSMContext) -> None:
    await back_handler(callback, state)


@router.callback_query(F.data == NAV_HOME_CALLBACK)
async def handle_inline_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await home_handler(callback, state)


@router.message(
    ~StateFilter("RegistrationStates:REG_NAME", "RegistrationStates:REG_BIRTHDATE", "RegistrationStates:REG_PHONE"),
    F.text.in_({"⬅️ Назад", "🏠 Главное меню"}),
)
async def handle_reply_nav_buttons(message: Message, state: FSMContext) -> None:
    if message.text == "🏠 Главное меню":
        await clear_state_preserving_navigation(state)
        await reset_stack(state)
        await render_main_menu(message, message.from_user.id)
        return

    await clear_state_preserving_navigation(state)
    await render_previous_screen(message, state)
