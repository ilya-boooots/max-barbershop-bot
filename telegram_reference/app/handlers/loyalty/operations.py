from __future__ import annotations

from math import floor
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.auth import has_role, normalize_role
from app.core.navigation import clear_state_preserving_navigation
from app.keyboards.loyalty import client_actions_kb, confirm_kb, skip_reason_kb
from app.core.loyalty_cards import loyalty_code_status_message, validate_loyalty_code
from app.repositories.transactions import create_transaction
from app.repositories.users import (
    get_by_tg_id,
    set_card_used_at,
    set_first_purchase_done,
    update_balance_delta,
)
from app.core.nav_constants import NAV_BACK_CALLBACK

router = Router()

STAFF_ROLES = ["admin", "manager", "developer"]
MIN_POINTS = 1
MAX_AMOUNT = 1_000_000


class LoyaltyOperationStates(StatesGroup):
    waiting_action = State()
    waiting_check_sum = State()
    waiting_manual_points = State()
    waiting_manual_reason = State()
    waiting_spend_amount = State()
    waiting_confirm = State()


async def _ensure_staff_access(user_id: int) -> bool:
    user = await get_by_tg_id(user_id)
    role = normalize_role(user_id, user["role"] if user else None)
    return has_role(role, STAFF_ROLES)


def _invalid_code_text(status: str) -> str:
    return loyalty_code_status_message(status)


def _back_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)]]
    )


async def render_client_card(message: Message, user: dict, code: str | None = None) -> None:
    first_visit = not bool(user.get("first_purchase_done"))
    badge_lines = []
    if first_visit:
        badge_lines.append("🎉 Первый визит клиента")
        badge_lines.append("🆕 Новый клиент")
    badge_text = "\n".join(badge_lines)
    if badge_text:
        badge_text = f"{badge_text}\n"
    await message.answer(
        "✅ Клиент найден:\n"
        f"{badge_text}"
        f"Имя: {user.get('name')}\n"
        f"Телефон: {user.get('phone')}\n"
        f"Код: {code or user.get('card_number')}\n"
        f"Баланс: {user.get('loyalty_balance', 0)}",
        reply_markup=client_actions_kb(),
    )


async def _ensure_code_context(state: FSMContext) -> tuple[str, int, str] | None:
    data = await state.get_data()
    code = data.get("code")
    target_tg_id = data.get("target_tg_id")
    search_source = data.get("search_source", "qr")
    if not code or not target_tg_id:
        return None
    return str(code), int(target_tg_id), str(search_source)


def _should_validate_qr(search_source: str) -> bool:
    return search_source == "qr"


@router.callback_query(F.data.in_({"loy:code:amount", "loy:code:manual", "loy:code:spend"}))
async def handle_operation_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _ensure_staff_access(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    context = await _ensure_code_context(state)
    if not context:
        await callback.answer("Данные операции не найдены.", show_alert=True)
        return
    code, target_tg_id, search_source = context
    if _should_validate_qr(search_source):
        status, user_row = await validate_loyalty_code(code)
        if status != "OK":
            await clear_state_preserving_navigation(state)
            now_ts = int(time.time())
            card_created_at = user_row.get("card_created_at") if user_row else None
            card_used_at = user_row.get("card_used_at") if user_row else None
            print(
                "DEBUG: validate_loyalty_code status={status} code={code!r} "
                "now={now_ts} created={card_created_at} used={card_used_at}".format(
                    status=status,
                    code=code,
                    now_ts=now_ts,
                    card_created_at=card_created_at,
                    card_used_at=card_used_at,
                )
            )
            await callback.message.answer(
                _invalid_code_text(status),
                reply_markup=_back_inline_kb(),
            )
            await callback.answer()
            return
    user = await get_by_tg_id(target_tg_id)
    if not user:
        await clear_state_preserving_navigation(state)
        await callback.message.answer("⛔️ Клиент не найден.")
        await callback.answer()
        return
    if callback.data == "loy:code:amount":
        await state.update_data(action="accrue_amount")
        await state.set_state(LoyaltyOperationStates.waiting_check_sum)
        await callback.message.answer("Введите сумму чека (в рублях), например 3500")
    elif callback.data == "loy:code:manual":
        await state.update_data(action="accrue_manual")
        await state.set_state(LoyaltyOperationStates.waiting_manual_points)
        await callback.message.answer(
            "Введите количество баллов для начисления (только число), например 200"
        )
    else:
        await state.update_data(action="spend")
        await state.set_state(LoyaltyOperationStates.waiting_spend_amount)
        await callback.message.answer(
            "Введите количество баллов для списания (только число), например 150"
        )
    await callback.answer()


@router.message(LoyaltyOperationStates.waiting_check_sum)
async def handle_check_sum(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message.from_user.id):
        await clear_state_preserving_navigation(state)
        await message.answer("Недостаточно прав.")
        return
    raw_value = (message.text or "").strip()
    try:
        check_sum = int(raw_value)
    except ValueError:
        await message.answer("⛔️ Введите сумму чека целым числом от 1 до 1 000 000.")
        return
    if check_sum < 1 or check_sum > MAX_AMOUNT:
        await message.answer("⛔️ Введите сумму чека целым числом от 1 до 1 000 000.")
        return
    context = await _ensure_code_context(state)
    if not context:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    _, target_tg_id, _ = context
    user = await get_by_tg_id(target_tg_id)
    if not user:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    points = floor(check_sum * 0.05)
    if points < MIN_POINTS:
        points = MIN_POINTS
    await state.update_data(check_sum=check_sum, points=points)
    await state.set_state(LoyaltyOperationStates.waiting_confirm)
    await message.answer(
        "Начислить {points} баллов клиенту {name} за чек {sum} ₽? 👇".format(
            points=points,
            name=user.get("name"),
            sum=check_sum,
        ),
        reply_markup=confirm_kb(),
    )


@router.message(LoyaltyOperationStates.waiting_manual_points)
async def handle_manual_points(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message.from_user.id):
        await clear_state_preserving_navigation(state)
        await message.answer("Недостаточно прав.")
        return
    raw_value = (message.text or "").strip()
    if not raw_value.isdigit():
        await message.answer("⛔️ Введите количество баллов целым числом.")
        return
    points = int(raw_value)
    if points < MIN_POINTS:
        await message.answer("⛔️ Введите количество баллов целым числом.")
        return
    await state.update_data(points=points)
    await state.set_state(LoyaltyOperationStates.waiting_manual_reason)
    await message.answer(
        "Укажите причину (необязательно), например: подарок / извинения / алкоголь не учитываем",
        reply_markup=skip_reason_kb(),
    )


@router.callback_query(F.data == "loy:manual:skip")
async def handle_manual_reason_skip(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _ensure_staff_access(callback.from_user.id):
        await clear_state_preserving_navigation(state)
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.update_data(reason=None)
    await _send_manual_confirm(callback.message, state)
    await callback.answer()


@router.message(LoyaltyOperationStates.waiting_manual_reason)
async def handle_manual_reason(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message.from_user.id):
        await clear_state_preserving_navigation(state)
        await message.answer("Недостаточно прав.")
        return
    reason = (message.text or "").strip()
    await state.update_data(reason=reason or None)
    await _send_manual_confirm(message, state)


async def _send_manual_confirm(message: Message, state: FSMContext) -> None:
    context = await _ensure_code_context(state)
    if not context:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    _, target_tg_id, _ = context
    data = await state.get_data()
    points = data.get("points")
    if not points:
        await message.answer("⛔️ Данные операции не найдены.")
        await clear_state_preserving_navigation(state)
        return
    user = await get_by_tg_id(target_tg_id)
    if not user:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    await state.set_state(LoyaltyOperationStates.waiting_confirm)
    await message.answer(
        "Начислить {points} баллов клиенту {name}? 👇".format(
            points=points,
            name=user.get("name"),
        ),
        reply_markup=confirm_kb(),
    )


@router.message(LoyaltyOperationStates.waiting_spend_amount)
async def handle_spend_amount(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message.from_user.id):
        await clear_state_preserving_navigation(state)
        await message.answer("Недостаточно прав.")
        return
    raw_value = (message.text or "").strip()
    if not raw_value.isdigit():
        await message.answer("⛔️ Введите количество баллов целым числом.")
        return
    spend_amount = int(raw_value)
    if spend_amount < MIN_POINTS:
        await message.answer("⛔️ Введите количество баллов целым числом.")
        return
    context = await _ensure_code_context(state)
    if not context:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    _, target_tg_id, _ = context
    user = await get_by_tg_id(target_tg_id)
    if not user:
        await message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        return
    current_balance = int(user.get("loyalty_balance", 0))
    if spend_amount > current_balance:
        await message.answer(f"⛔️ Введите значение от 1 до {current_balance}.")
        return
    await state.update_data(points=spend_amount)
    await state.set_state(LoyaltyOperationStates.waiting_confirm)
    await message.answer(
        "Списать {points} баллов у клиента {name}? 👇".format(
            points=spend_amount,
            name=user.get("name"),
        ),
        reply_markup=confirm_kb(),
    )


@router.message(LoyaltyOperationStates.waiting_confirm)
async def handle_waiting_confirm_input(message: Message) -> None:
    await message.answer("Пожалуйста, подтвердите действие кнопками ниже.")


@router.callback_query(F.data == "loy:cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    data = await state.get_data()
    target_tg_id = data.get("target_tg_id")
    code = data.get("code")
    search_source = data.get("search_source", "qr")
    if target_tg_id and code:
        await state.set_state(LoyaltyOperationStates.waiting_action)
        await state.update_data(
            code=code,
            target_tg_id=target_tg_id,
            search_source=search_source,
        )
    else:
        await clear_state_preserving_navigation(state)
    if target_tg_id:
        user = await get_by_tg_id(int(target_tg_id))
        if user:
            await render_client_card(callback.message, user, str(code) if code else None)
    await callback.answer("Действие отменено.")


@router.callback_query(F.data == "loy:confirm")
async def handle_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    if not await _ensure_staff_access(callback.from_user.id):
        await clear_state_preserving_navigation(state)
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    action = data.get("action")
    target_tg_id = data.get("target_tg_id")
    points = data.get("points")
    check_sum = data.get("check_sum")
    reason = data.get("reason")
    code = data.get("code")
    search_source = data.get("search_source", "qr")
    if not action or not target_tg_id or not points or not code:
        await callback.answer("Данные операции не найдены.", show_alert=True)
        await clear_state_preserving_navigation(state)
        return
    user = await get_by_tg_id(int(target_tg_id))
    if not user:
        await callback.message.answer("⛔️ Клиент не найден.")
        await clear_state_preserving_navigation(state)
        await callback.answer()
        return
    if action == "spend" and int(user.get("loyalty_balance", 0)) < int(points):
        await callback.message.answer("⛔️ Недостаточно баллов для списания.")
        await clear_state_preserving_navigation(state)
        await callback.answer()
        return
    if _should_validate_qr(search_source):
        status, user_row = await validate_loyalty_code(str(code))
        if status != "OK":
            await clear_state_preserving_navigation(state)
            now_ts = int(time.time())
            card_created_at = user_row.get("card_created_at") if user_row else None
            card_used_at = user_row.get("card_used_at") if user_row else None
            print(
                "DEBUG: validate_loyalty_code status={status} code={code!r} "
                "now={now_ts} created={card_created_at} used={card_used_at}".format(
                    status=status,
                    code=code,
                    now_ts=now_ts,
                    card_created_at=card_created_at,
                    card_used_at=card_used_at,
                )
            )
            await callback.message.answer(
                _invalid_code_text(status),
                reply_markup=_back_inline_kb(),
            )
            await callback.answer()
            return
    delta = int(points) if action != "spend" else -int(points)
    await update_balance_delta(int(target_tg_id), delta)
    await set_card_used_at(int(target_tg_id))
    if not user.get("first_purchase_done"):
        await set_first_purchase_done(int(target_tg_id), True)
    await create_transaction(
        user_tg_id=int(target_tg_id),
        type="accrual" if action != "spend" else "spend",
        amount=int(points),
        check_sum=int(check_sum) if action == "accrue_amount" else None,
        created_by_tg_id=callback.from_user.id,
        reason=reason if action == "accrue_manual" else None,
    )
    refreshed_user = await get_by_tg_id(int(target_tg_id))
    updated_balance = int((refreshed_user or {}).get("loyalty_balance", 0))
    if action == "spend":
        await callback.message.answer(f"✅ Списано {points} баллов.")
    else:
        await callback.message.answer(f"✅ Начислено {points} баллов.")
    await state.set_state(LoyaltyOperationStates.waiting_action)
    await state.update_data(code=code, target_tg_id=target_tg_id, search_source=search_source)
    if refreshed_user:
        await render_client_card(callback.message, refreshed_user, str(code))
    try:
        await callback.bot.send_message(
            int(target_tg_id),
            (
                f"🎉 Вам начислено {points} баллов! Баланс: {updated_balance}"
                if action != "spend"
                else f"✅ Списано {points} баллов. Баланс: {updated_balance}"
            ),
        )
    except Exception:
        pass
    await callback.answer()
