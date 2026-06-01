from __future__ import annotations

from io import BytesIO
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.auth import has_role, normalize_role
from app.core.security import CARD_NUMBER_PATTERN
from app.core.loyalty_cards import loyalty_code_status_message, validate_loyalty_code
from app.core.navigation import clear_state_preserving_navigation, push_screen, render_main_by_role
from app.core.screens import (
    render_personnel_menu,
    render_staff_operations,
    render_staff_scan_qr,
)
from app.handlers.loyalty.operations import LoyaltyOperationStates, render_client_card
from app.keyboards.menu import back_reply_kb
from app.keyboards.staff import staff_client_search_kb
from app.repositories.users import (
    get_user as get_db_user,
    get_user_by_tg_id,
    search_users_by_phone_suffix,
)
from app.utils.qr_decode import decode_qr_code_from_bytes

router = Router()

STAFF_ROLES = ["admin", "manager", "developer"]
PERSONNEL_ROLES = ["admin", "manager", "developer"]


class StaffScanQrStates(StatesGroup):
    awaiting_payload = State()


def _resolve_user_role(user_id: int, db_role: str | None) -> str:
    return normalize_role(user_id, db_role)


async def _get_user_context(user_id: int) -> tuple[dict | None, str]:
    user = await get_db_user(user_id)
    role = _resolve_user_role(user_id, user["role"] if user else None)
    return user, role


async def _ensure_staff_access(message: Message) -> tuple[dict | None, str] | None:
    user, role = await _get_user_context(message.from_user.id)
    if not has_role(role, STAFF_ROLES):
        await message.answer("⛔️ Недостаточно прав.")
        return None
    return user, role


@router.message(F.text == "🧾 Операции")
async def handle_operations(message: Message, state: FSMContext) -> None:
    context = await _ensure_staff_access(message)
    if not context:
        return
    await push_screen(state, "staff_operations")
    await render_staff_operations(message)


@router.message(F.text == "🔍 Найти клиента / 📷 Сканировать QR")
async def handle_scan_qr_prompt(message: Message, state: FSMContext) -> None:
    context = await _ensure_staff_access(message)
    if not context:
        return
    await state.set_state(StaffScanQrStates.awaiting_payload)
    await push_screen(state, "staff_scan_qr")
    await render_staff_scan_qr(message)


@router.message(F.text == "👥 Персонал")
async def handle_personnel(message: Message, state: FSMContext) -> None:
    context = await _ensure_staff_access(message)
    if not context:
        return
    _, role = context
    if not has_role(role, PERSONNEL_ROLES):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await push_screen(state, "personnel_menu")
    await render_personnel_menu(message)


@router.message(F.text == "⬅️ В меню клиента")
async def handle_back_to_customer_menu(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message):
        return
    await push_screen(state, "main_menu")
    await message.answer("Вы вернулись в меню клиента.")
    await render_main_by_role(message, message.from_user.id)


async def _process_loyalty_code(
    message: Message,
    state: FSMContext,
    code: str,
    keep_state_on_invalid: bool,
    search_source: str,
) -> bool:
    code = code.strip()
    if not CARD_NUMBER_PATTERN.fullmatch(code):
        await message.answer("⛔️ Введите код в формате 000-000.", reply_markup=back_reply_kb())
        if not keep_state_on_invalid:
            await clear_state_preserving_navigation(state)
        return False
    status, user_row = await validate_loyalty_code(code)
    if status != "OK":
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
        await message.answer(
            loyalty_code_status_message(status),
            reply_markup=back_reply_kb(),
        )
        if not keep_state_on_invalid:
            await clear_state_preserving_navigation(state)
        return False
    user_id = int(user_row["user_id"]) if user_row else None
    user = await get_user_by_tg_id(user_id) if user_id else None
    if not user:
        await message.answer("⛔️ Клиент не найден.")
        if not keep_state_on_invalid:
            await clear_state_preserving_navigation(state)
        return False
    await state.set_state(LoyaltyOperationStates.waiting_action)
    await state.update_data(
        code=code,
        target_tg_id=int(user_id),
        card_number=user.get("card_number"),
        search_source=search_source,
    )
    await render_client_card(message, user, code)
    return True


def _normalize_digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _format_card_number(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:]}"


def _mask_phone_suffix(phone: str) -> str:
    digits = _normalize_digits(phone)
    suffix = digits[-4:] if len(digits) >= 4 else digits
    return f"****{suffix}"


async def _open_client_card(
    message: Message,
    state: FSMContext,
    user: dict,
    code: str | None = None,
) -> None:
    await state.set_state(LoyaltyOperationStates.waiting_action)
    await state.update_data(
        code=code or user.get("card_number"),
        target_tg_id=int(user["user_id"]),
        card_number=user.get("card_number"),
        search_source="phone",
    )
    await render_client_card(message, user, code)


@router.message(StaffScanQrStates.awaiting_payload, F.photo)
async def handle_scan_qr_photo(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message):
        await clear_state_preserving_navigation(state)
        return
    if not message.photo:
        await message.answer(
            "😕 Не получилось распознать QR.\n"
            "Отправьте фото ещё раз (поближе, без бликов) или введите код вручную (например 467-400).",
            reply_markup=back_reply_kb(),
        )
        return
    file = await message.bot.get_file(message.photo[-1].file_id)
    buffer = BytesIO()
    await message.bot.download_file(file.file_path, buffer)
    code = decode_qr_code_from_bytes(buffer.getvalue())
    if not code:
        await message.answer(
            "😕 Не получилось распознать QR.\n"
            "Отправьте фото ещё раз (поближе, без бликов) или введите код вручную (например 467-400).",
            reply_markup=back_reply_kb(),
        )
        return
    await message.answer(f"✅ QR распознан, код: {code}")
    await _process_loyalty_code(
        message,
        state,
        code,
        keep_state_on_invalid=True,
        search_source="qr",
    )


@router.message(StaffScanQrStates.awaiting_payload)
async def handle_scan_qr_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_staff_access(message):
        await clear_state_preserving_navigation(state)
        return
    if not message.text:
        await message.answer(
            "😕 Не получилось распознать QR.\n"
            "Отправьте фото ещё раз (поближе, без бликов) или введите код вручную (например 467-400).",
            reply_markup=back_reply_kb(),
        )
        return
    query = message.text.strip()
    digits = _normalize_digits(query)
    if len(digits) == 6:
        await _process_loyalty_code(
            message,
            state,
            _format_card_number(digits),
            keep_state_on_invalid=True,
            search_source="manual",
        )
        return
    if len(digits) == 4:
        users = await search_users_by_phone_suffix(digits)
        if not users:
            await message.answer("❌ Клиент не найден", reply_markup=back_reply_kb())
            return
        if len(users) == 1:
            await _open_client_card(message, state, users[0])
            return
        items = []
        for user in users:
            label = f"{user.get('name') or 'Клиент'} — {_mask_phone_suffix(user.get('phone') or '')}"
            items.append((int(user["user_id"]), label))
        await message.answer(
            "Найдено несколько клиентов. Выберите нужного:",
            reply_markup=staff_client_search_kb(items),
        )
        return
    await message.answer(
        "⛔️ Введите код в формате 000-000 или последние 4 цифры телефона.",
        reply_markup=back_reply_kb(),
    )


@router.callback_query(F.data.startswith("staff:client:open:"))
async def handle_client_open(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        await callback.answer()
        return
    _, role = await _get_user_context(callback.from_user.id)
    if not has_role(role, STAFF_ROLES):
        await clear_state_preserving_navigation(state)
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target_tg_id = int(callback.data.split(":")[-1])
    user = await get_user_by_tg_id(target_tg_id)
    if not user:
        await callback.message.answer("❌ Клиент не найден", reply_markup=back_reply_kb())
        await callback.answer()
        return
    await _open_client_card(callback.message, state, user)
    await callback.answer()
