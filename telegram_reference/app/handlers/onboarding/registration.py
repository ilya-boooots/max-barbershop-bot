from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.auth import get_dev_user_id, normalize_role
from app.core.loyalty_cards import ensure_card_number
from app.core.logging import format_log_context
from app.core.navigation import clear_state_preserving_navigation, push_screen, render_main_by_role
from app.core.screens import (
    CALLBACK_CONFIRM_NAME_NO,
    CALLBACK_CONFIRM_NAME_YES,
    CALLBACK_GENDER_FEMALE,
    CALLBACK_GENDER_MALE,
    build_gender_kb,
    build_name_confirmation_kb,
    render_registration_choose_gender,
    render_registration_confirm_name,
    render_registration_enter_birth_date,
    render_registration_enter_name,
)
from app.repositories.transactions import create_transaction, has_registration_bonus
from app.repositories.users import (
    add_loyalty_balance,
    get_user as get_db_user,
    set_first_purchase_done,
    set_role,
    upsert_registered_user,
)
from app.storage.memory import (
    get_user as get_memory_user,
    set_birth_date,
    set_chosen_name,
    set_gender,
    set_phone,
    set_registered,
)

router = Router()
logger = logging.getLogger(__name__)

STATE_TTL = timedelta(minutes=30)
STATE_STARTED_AT_KEY = "state_started_at"


class RegistrationStates(StatesGroup):
    confirming_name = State()
    entering_name = State()
    entering_birth_date = State()
    choosing_gender = State()


def _resolve_first_name(message: Message | CallbackQuery) -> str:
    first_name = message.from_user.first_name if message.from_user else None
    return first_name or "Пользователь"


def _is_valid_birth_date(value: str) -> bool:
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        return False
    current_year = datetime.now().year
    return 1900 <= parsed.year <= current_year


async def _is_registered_in_db(user_id: int) -> bool:
    user = await get_db_user(user_id)
    return bool(user and user["is_registered"])


async def _should_short_circuit_registration(user_id: int, state: FSMContext) -> bool:
    if user_id == get_dev_user_id():
        return False
    current_state = await state.get_state()
    if current_state is not None:
        return False
    return await _is_registered_in_db(user_id)


async def _mark_state_started(state: FSMContext) -> None:
    await state.update_data({STATE_STARTED_AT_KEY: datetime.now(timezone.utc).isoformat()})


async def _handle_stale_state(message: Message, state: FSMContext) -> bool:
    data = await state.get_data()
    started_at = data.get(STATE_STARTED_AT_KEY)
    if not started_at:
        return False
    try:
        started_at_dt = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    if datetime.now(timezone.utc) - started_at_dt <= STATE_TTL:
        return False
    await clear_state_preserving_navigation(state)
    await message.answer(
        "⏳ Похоже, вы вернулись слишком поздно. Давайте начнём сначала — нажмите /start."
    )
    return True


async def start_registration(message: Message, state: FSMContext) -> None:
    name = _resolve_first_name(message)
    await state.set_state(RegistrationStates.confirming_name)
    await _mark_state_started(state)
    await push_screen(state, "registration_confirm_name", {"name": name})
    logger.info(
        "Registration started%s",
        f" | {format_log_context(user_id=message.from_user.id, state=RegistrationStates.confirming_name.state)}",
    )
    await render_registration_confirm_name(message, {"name": name})


@router.message(F.contact)
async def handle_contact(message: Message, state: FSMContext) -> None:
    if await _should_short_circuit_registration(message.from_user.id, state):
        user = await get_db_user(message.from_user.id)
        await push_screen(state, "main_menu")
        await message.answer("Вы успешно зарегестрированы. Давайте продолжим")
        await render_main_by_role(message, message.from_user.id)
        return
    set_phone(message.from_user.id, message.contact.phone_number)
    await start_registration(message, state)


@router.callback_query(F.data == CALLBACK_CONFIRM_NAME_YES)
async def handle_name_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    name = _resolve_first_name(callback)
    set_chosen_name(callback.from_user.id, name)
    await state.set_state(RegistrationStates.entering_birth_date)
    await _mark_state_started(state)
    await push_screen(state, "registration_enter_birth_date")
    logger.info(
        "Registration moved to birth date%s",
        f" | {format_log_context(user_id=callback.from_user.id, state=RegistrationStates.entering_birth_date.state)}",
    )
    await render_registration_enter_birth_date(callback.message)
    await callback.answer()


@router.callback_query(F.data == CALLBACK_CONFIRM_NAME_NO)
async def handle_name_declined(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationStates.entering_name)
    await _mark_state_started(state)
    await push_screen(state, "registration_enter_name")
    logger.info(
        "Registration moved to manual name%s",
        f" | {format_log_context(user_id=callback.from_user.id, state=RegistrationStates.entering_name.state)}",
    )
    await render_registration_enter_name(callback.message)
    await callback.answer()


@router.message(RegistrationStates.entering_name)
async def handle_manual_name(message: Message, state: FSMContext) -> None:
    if await _handle_stale_state(message, state):
        return
    if not message.text:
        await message.answer("⛔️ Пожалуйста, отправьте имя текстом.")
        return
    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("⛔️ Имя не прошло проверку. Введите корректное имя.")
        return
    set_chosen_name(message.from_user.id, name)
    await state.set_state(RegistrationStates.entering_birth_date)
    await _mark_state_started(state)
    await push_screen(state, "registration_enter_birth_date")
    logger.info(
        "Registration moved to birth date%s",
        f" | {format_log_context(user_id=message.from_user.id, state=RegistrationStates.entering_birth_date.state)}",
    )
    await render_registration_enter_birth_date(message)


@router.message(RegistrationStates.entering_birth_date)
async def handle_birth_date(message: Message, state: FSMContext) -> None:
    if await _handle_stale_state(message, state):
        return
    if not message.text:
        await message.answer(
            "⛔️ Пожалуйста, отправьте дату рождения текстом в формате дд.мм.гггг."
        )
        return
    birth_date = message.text.strip()
    if not _is_valid_birth_date(birth_date):
        await message.answer(
            "⛔️ Дата не прошла проверку. Проверьте корректность введенных данных."
        )
        await message.answer(
            "📅 Введите дату рождения в формате дд.мм.гггг (Например: 31.01.1999)."
        )
        return
    set_birth_date(message.from_user.id, birth_date)
    await state.set_state(RegistrationStates.choosing_gender)
    await _mark_state_started(state)
    await push_screen(state, "registration_choose_gender")
    logger.info(
        "Registration moved to gender selection%s",
        f" | {format_log_context(user_id=message.from_user.id, state=RegistrationStates.choosing_gender.state)}",
    )
    await render_registration_choose_gender(message)


@router.callback_query(F.data.in_({CALLBACK_GENDER_MALE, CALLBACK_GENDER_FEMALE}))
async def handle_gender_selected(callback: CallbackQuery, state: FSMContext) -> None:
    gender = "👨 Мужской" if callback.data == CALLBACK_GENDER_MALE else "👩 Женский"
    set_gender(callback.from_user.id, gender)
    started_message = await callback.message.answer("Регистрация начата... ⏳")
    await asyncio.sleep(3)
    await callback.message.answer("Регистрация завершилась успешно! 👍")
    try:
        await callback.bot.delete_message(
            chat_id=started_message.chat.id,
            message_id=started_message.message_id,
        )
    except Exception:
        pass
    user_profile = get_memory_user(callback.from_user.id)
    existing_user = await get_db_user(callback.from_user.id)
    phone_value = user_profile.phone or ""
    should_upsert = (
        callback.from_user.id == get_dev_user_id()
        or not existing_user
        or not existing_user["is_registered"]
    )
    target_user_id = callback.from_user.id
    if should_upsert:
        target_user_id = await upsert_registered_user(
            user_id=callback.from_user.id,
            phone=phone_value,
            name=user_profile.chosen_name or _resolve_first_name(callback),
            birth_date=user_profile.birth_date or "",
            gender=user_profile.gender or gender,
            username=(callback.from_user.username or "").lower() or None,
        )
        registration_bonus_awarded = await has_registration_bonus(callback.from_user.id)
        if (
            callback.from_user.id != get_dev_user_id()
            and not registration_bonus_awarded
            and (not existing_user or not existing_user["is_registered"])
        ):
            await add_loyalty_balance(target_user_id, 200)
            await create_transaction(
                user_tg_id=target_user_id,
                type="registration_bonus",
                amount=200,
                check_sum=None,
                created_by_tg_id=0,
            )
            await set_first_purchase_done(target_user_id, False)
            await callback.message.answer(
                "🎉 Добро пожаловать, дорогой гость!\n"
                "Мы дарим вам 200 бонусных баллов за регистрацию.\n\n"
                "💡 Их можно потратить со второго посещения.\n"
                "1 балл = 1 рубль 🙂"
            )
    refreshed_user = await get_db_user(target_user_id)
    db_role = refreshed_user.get("role") if refreshed_user else None
    normalized_role = normalize_role(callback.from_user.id, db_role)
    await set_role(target_user_id, normalized_role)
    await ensure_card_number(
        target_user_id,
        refreshed_user.get("card_number") if refreshed_user else None,
    )
    set_registered(callback.from_user.id, True)
    await clear_state_preserving_navigation(state)
    logger.info(
        "Registration completed%s",
        f" | {format_log_context(user_id=callback.from_user.id)}",
    )
    await push_screen(state, "main_menu")
    await render_main_by_role(callback, callback.from_user.id)
    await callback.answer()


@router.message(RegistrationStates.confirming_name)
async def handle_name_confirmation_input(message: Message, state: FSMContext) -> None:
    if await _handle_stale_state(message, state):
        return
    await message.answer(
        "Пожалуйста, выберите вариант на кнопках выше.",
        reply_markup=build_name_confirmation_kb(),
    )


@router.message(RegistrationStates.choosing_gender)
async def handle_gender_input(message: Message, state: FSMContext) -> None:
    if await _handle_stale_state(message, state):
        return
    await message.answer("👥 Выберите пол.", reply_markup=build_gender_kb())
