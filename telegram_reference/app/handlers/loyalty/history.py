from __future__ import annotations

from math import ceil
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from app.core.navigation import push_screen
from app.keyboards.loyalty import (
    BALANCE_HISTORY_CALLBACK,
    BALANCE_HISTORY_PAGE_CALLBACK,
    balance_history_kb,
)
from app.repositories.transactions import count_user_transactions, get_user_transactions
from app.utils.datetime import format_branch_datetime

router = Router()

HISTORY_PAGE_SIZE = 5


class BalanceHistoryStates(StatesGroup):
    viewing = State()


async def _format_history_datetime(value: Any) -> str:
    formatted = await format_branch_datetime(value)
    if formatted == "—":
        return formatted
    if formatted.count(":") >= 2:
        return formatted.rsplit(":", 1)[0]
    return formatted


def _build_reason(entry: dict[str, Any]) -> str:
    check_sum = entry.get("check_sum")
    reason = entry.get("reason")
    entry_type = entry.get("type")
    if entry_type != "spend" and check_sum:
        return f"чек {check_sum} ₽"
    if reason:
        return str(reason)
    if entry_type == "registration_bonus":
        return "бонус за регистрацию"
    if entry_type == "spend":
        return "оплата заказа"
    return "начисление бонусов"


async def _format_operation(entry: dict[str, Any]) -> str:
    is_spend = entry.get("type") == "spend"
    title = "➖ Списание" if is_spend else "➕ Начисление"
    amount_value = entry.get("amount", 0)
    amount = f"{'-' if is_spend else '+'}{amount_value} баллов"
    reason = _build_reason(entry)
    created_at = await _format_history_datetime(entry.get("created_at"))
    return "\n".join([title, amount, f"Причина: {reason}", created_at])


def _history_keyboard(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    return balance_history_kb(current_page, total_pages)


async def _render_history(callback: CallbackQuery, state: FSMContext, page: int) -> None:
    user_id = callback.from_user.id
    total = await count_user_transactions(user_id)
    if total == 0:
        await state.set_state(BalanceHistoryStates.viewing)
        await state.update_data(user_id=user_id, current_page=0)
        if callback.message:
            await callback.message.edit_text(
                "📭 История операций пока пуста",
                reply_markup=balance_history_kb(0, 1),
            )
        await callback.answer()
        return

    total_pages = max(1, ceil(total / HISTORY_PAGE_SIZE))
    safe_page = max(0, min(page, total_pages - 1))
    await state.set_state(BalanceHistoryStates.viewing)
    await state.update_data(user_id=user_id, current_page=safe_page)

    offset = safe_page * HISTORY_PAGE_SIZE
    rows = await get_user_transactions(user_id, limit=HISTORY_PAGE_SIZE, offset=offset)
    lines = ["📜 История операций", ""]
    for entry in rows:
        lines.append(await _format_operation(entry))
    text = "\n\n".join(lines)
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=_history_keyboard(safe_page, total_pages),
        )
    await callback.answer()


@router.callback_query(F.data == BALANCE_HISTORY_CALLBACK)
async def handle_balance_history(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    await push_screen(state, "balance_history")
    await _render_history(callback, state, page=0)


@router.callback_query(F.data.startswith(f"{BALANCE_HISTORY_PAGE_CALLBACK}:"))
async def handle_balance_history_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    raw_page = callback.data.rsplit(":", 1)[-1]
    try:
        page = int(raw_page)
    except ValueError:
        page = 0
    await _render_history(callback, state, page=page)
