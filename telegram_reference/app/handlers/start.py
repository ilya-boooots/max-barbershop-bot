from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.core.auth import get_dev_user_id, normalize_role
from app.core.error_monitor import send_dev_alert
from app.core.navigation import clear_state_preserving_navigation, push_screen, render_main_by_role, reset_stack
from app.integrations.yclients.clients_sync import upsert_client_profile
from app.integrations.yclients.errors import YClientsError
from app.services.loyalty_mvp import apply_start_referral, sync_referral_reward_if_eligible
from app.utils.phone import normalize_phone
from app.repositories.users import (
    get_user,
    is_registration_success_message_shown,
    mark_registration_success_message_shown,
    set_username,
    upsert_registration_profile,
    upsert_telegram_user,
)
router = Router()
logger = logging.getLogger(__name__)


class RegistrationStates(StatesGroup):
    REG_NAME = State()
    REG_BIRTHDATE = State()
    REG_PHONE = State()


def _parse_birthdate(raw_value: str) -> str | None:
    try:
        parsed = datetime.strptime(raw_value.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None
    if parsed < date(1900, 1, 1) or parsed > date.today():
        return None
    return parsed.isoformat()


def _registration_phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Поделиться контактом", request_contact=True)],
        ],
        resize_keyboard=True,
    )


_REGISTRATION_STATES = (
    RegistrationStates.REG_NAME,
    RegistrationStates.REG_BIRTHDATE,
    RegistrationStates.REG_PHONE,
)


def _is_developer_user(user_id: int, db_role: str | None = None) -> bool:
    return user_id == get_dev_user_id() or normalize_role(user_id, db_role) == "developer"


async def _restart_registration_flow(message: Message, state: FSMContext, *, debug_prefix: str | None = None) -> None:
    logger.info("Registration started | user_id=%s", message.from_user.id)
    await clear_state_preserving_navigation(state)
    await reset_stack(state)
    await push_screen(state, "registration")
    await state.set_state(RegistrationStates.REG_NAME)
    if debug_prefix:
        logger.info("Developer forced registration restart | user_id=%s", message.from_user.id)
        await message.answer(debug_prefix, reply_markup=ReplyKeyboardRemove())
    await message.answer("👋 Приветствуем вас! Пожалуйста, пройдите регистрацию 😊", reply_markup=ReplyKeyboardRemove())
    await _ask_registration_step(message, state)


async def _ask_registration_step(message: Message, state: FSMContext, prefix: str | None = None) -> None:
    current_state = await state.get_state()
    if prefix:
        await message.answer(prefix)

    if current_state == RegistrationStates.REG_NAME.state:
        await message.answer("✍️ Как вас зовут?", reply_markup=ReplyKeyboardRemove())
        return
    if current_state == RegistrationStates.REG_BIRTHDATE.state:
        await message.answer(
            "🎂 Укажите дату рождения (в формате ДД.ММ.ГГГГ)",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer(
        "📱 Поделитесь номером телефона, чтобы мы могли вас записать 😊",
        reply_markup=_registration_phone_kb(),
    )


@router.message(CommandStart(), StateFilter(*_REGISTRATION_STATES))
async def handle_start_during_registration(message: Message, state: FSMContext) -> None:
    if _is_developer_user(message.from_user.id):
        await _restart_registration_flow(
            message,
            state,
            debug_prefix="🛠 Режим разработчика: запускаю регистрацию заново",
        )
        return
    await _ask_registration_step(message, state, prefix="📝 Регистрация уже идет. Давайте закончим 🙂")


async def _show_main_menu(message: Message, state: FSMContext, _text: str) -> None:
    await clear_state_preserving_navigation(state)
    await reset_stack(state)
    await push_screen(state, "main_menu")
    # Keep a single canonical render path to avoid duplicate menu messages.
    await render_main_by_role(message, message.from_user.id)


@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await upsert_telegram_user(
        tg_id=user_id,
        username=message.from_user.username,
        name=message.from_user.full_name,
    )
    await set_username(user_id, (message.from_user.username or "").lower() or None)

    start_param = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        start_param = parts[1].strip()
    await apply_start_referral(invited_tg_id=user_id, start_param=start_param or None)

    user = await get_user(user_id)
    user_role = user.get("role") if user else None
    is_registered = bool(user and user.get("is_registered") and user.get("phone") and user.get("birth_date"))
    is_developer = _is_developer_user(user_id, user_role)
    if is_registered and not is_developer:
        await sync_referral_reward_if_eligible(invited_tg_id=user_id, bot=message.bot)
        await _show_main_menu(message, state, "✅ Вы уже зарегистрированы! Выберите пункт меню ниже 👇")
        return
    await _restart_registration_flow(
        message,
        state,
        debug_prefix="🛠 Режим разработчика: запускаю регистрацию заново" if is_registered and is_developer else None,
    )


@router.message(RegistrationStates.REG_NAME)
async def handle_registration_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("🙂 Пожалуйста, укажите имя (минимум 2 символа).")
        return
    await state.update_data(reg_name=name)
    await state.set_state(RegistrationStates.REG_BIRTHDATE)
    await _ask_registration_step(message, state)


@router.message(RegistrationStates.REG_BIRTHDATE)
async def handle_registration_birthdate(message: Message, state: FSMContext) -> None:
    birthdate_iso = _parse_birthdate(message.text or "")
    if birthdate_iso is None:
        await message.answer("😌 Дата не распознана. Введите в формате ДД.ММ.ГГГГ, например 31.01.1999.")
        return
    await state.update_data(reg_birthdate=birthdate_iso)
    await state.set_state(RegistrationStates.REG_PHONE)
    await _ask_registration_step(message, state)


@router.message(RegistrationStates.REG_PHONE, F.text)
@router.message(RegistrationStates.REG_PHONE, F.contact)
async def handle_registration_phone(message: Message, state: FSMContext) -> None:
    logger.info("Registration phone/contact received | user_id=%s has_contact=%s", message.from_user.id, bool(message.contact))
    phone_source = message.contact.phone_number if message.contact else (message.text or "")
    normalized_phone = normalize_phone(phone_source, default_region="RU")
    phone = normalized_phone.canonical_e164
    logger.info("Phone normalized | user_id=%s phone=%s valid=%s", message.from_user.id, phone, normalized_phone.is_valid)
    if not phone or not normalized_phone.is_valid:
        await message.answer("⚠️ Не удалось распознать номер телефона. Отправьте контакт ещё раз или введите номер вручную.")
        return

    data = await state.get_data()
    name = str(data.get("reg_name") or "").strip() or (message.from_user.full_name or "Гость")
    birthdate_iso = str(data.get("reg_birthdate") or "")

    progress_message = await message.answer("⏳ Регистрация начата, подождите немного...", reply_markup=ReplyKeyboardRemove())
    yclients_client_id: int | None = None
    yclients_error = None
    try:
        sync_result = await upsert_client_profile(name=name, phone=phone, birthdate_iso=birthdate_iso)
        yclients_client_id = sync_result.client_id
        logger.info("YClients sync success | user_id=%s yclients_client_id=%s", message.from_user.id, yclients_client_id)
    except Exception as exc:
        yclients_error = exc
        logger.exception("YClients sync failed | user_id=%s", message.from_user.id)
        trace_id = getattr(exc, "trace_id", None) or "n/a"
        method = getattr(exc, "method", None) or "unknown"
        endpoint = getattr(exc, "endpoint", None) or "unknown"
        status = getattr(exc, "status_code", None)
        response_snippet = getattr(exc, "response_snippet", None) or (str(exc)[:200] or "—")
        await send_dev_alert(
            message.bot,
            (
                "🚨 Ошибка регистрации YClients\n"
                f"🧩 trace_id: {trace_id}\n"
                f"🛠 method: {method}\n"
                f"🔗 endpoint: {endpoint}\n"
                f"📟 status: {status if status is not None else 'n/a'}\n"
                f"💬 response: {response_snippet[:300]}"
            ),
        )

    await upsert_registration_profile(
        tg_user_id=message.from_user.id,
        name=name,
        birthdate_iso=birthdate_iso,
        phone=phone,
        phone_raw=phone_source,
        phone_digits=normalized_phone.digits_only,
        phone_e164=normalized_phone.canonical_e164,
        phone_ru_7=normalized_phone.ru_11_with_7,
        phone_ru_8=normalized_phone.ru_11_with_8,
        match_source="registration",
        username=message.from_user.username,
        yclients_client_id=yclients_client_id,
    )

    logger.info("Local registration profile upserted | user_id=%s", message.from_user.id)
    await asyncio.sleep(2)
    try:
        await message.bot.delete_message(chat_id=progress_message.chat.id, message_id=progress_message.message_id)
    except Exception:
        logger.debug("Failed to delete registration progress message", exc_info=True)

    if yclients_error is not None:
        await clear_state_preserving_navigation(state)
        if isinstance(yclients_error, YClientsError):
            await message.answer("⚠️ Не удалось синхронизировать данные с YClients. Попробуйте позже.")
        elif "UNIQUE constraint failed" in str(yclients_error):
            await message.answer("⚠️ Этот номер уже есть в базе. Мы не смогли автоматически привязать его к вашему профилю. Обратитесь в поддержку.")
        else:
            await message.answer("⚠️ Не удалось синхронизировать данные с YClients. Попробуйте позже.")
        return

    await sync_referral_reward_if_eligible(invited_tg_id=message.from_user.id, bot=message.bot)
    message_already_shown = await is_registration_success_message_shown(message.from_user.id)
    if not message_already_shown:
        await message.answer(
            "✅ Регистрация завершена!\n\n"
            "Теперь вы можете записываться на услуги, смотреть свои записи, переносить или отменять визиты прямо в боте.\n\n"
            "Нажмите \"✂️ Записаться\", чтобы выбрать услугу, мастера и удобное время 😊"
        )
        await mark_registration_success_message_shown(message.from_user.id)
        logger.info("Registration success message shown | user_id=%s", message.from_user.id)
    else:
        logger.info("Registration success message skipped | user_id=%s", message.from_user.id)
    await _show_main_menu(state=state, message=message, _text="✅ Вы успешно зарегистрировались! Выберите пункт меню ниже 👇")


@router.message(Command("menu"), ~StateFilter(*_REGISTRATION_STATES))
async def handle_menu(message: Message, state: FSMContext) -> None:
    await set_username(message.from_user.id, (message.from_user.username or "").lower() or None)
    await _show_main_menu(message, state, "Выберите пункт меню ниже 👇")


@router.message(StateFilter(*_REGISTRATION_STATES))
async def handle_registration_blocking_input(message: Message, state: FSMContext) -> None:
    await _ask_registration_step(message, state, prefix="📝 Сначала завершите регистрацию, пожалуйста 🙂")
