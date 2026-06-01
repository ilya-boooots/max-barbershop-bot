from __future__ import annotations

import re
from datetime import date, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.auth import normalize_role
from app.core.navigation import clear_state_preserving_navigation, render_main_by_role
from app.core.booking_reminders import notify_hostesses_new_booking
from app.db.bookings_repo import count_user_bookings_today, create_booking
from app.keyboards.booking import (
    BOOK_CALENDAR_NEXT,
    BOOK_CALENDAR_NOOP,
    BOOK_CALENDAR_PREV,
    BOOK_CANCEL,
    BOOK_COMMENT_BACK,
    BOOK_COMMENT_SKIP,
    BOOK_CONFIRM,
    BOOK_CONFIRM_BACK,
    BOOK_DATE_BACK,
    BOOK_TIME_BACK,
    SAMARA_TZ,
    build_calendar,
    build_comment_keyboard,
    build_confirmation_keyboard,
    build_time_keyboard,
    generate_time_slots,
)
from app.repositories.diagnostics import log_bot_event, log_user_event
from app.repositories.users import get_user as get_db_user
from app.core.ui_texts import BOOK_TABLE_BTN

router = Router()

BOOKING_BUTTON = BOOK_TABLE_BTN
ADDRESS_TEXT = "Саратов, улица Пушкина 1"
NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЁё\-\s]{2,40}$")


class BookingStates(StatesGroup):
    waiting_name = State()
    waiting_date = State()
    waiting_guests = State()
    waiting_time = State()
    waiting_comment = State()
    waiting_confirmation = State()


async def _is_allowed(user_id: int) -> bool:
    user = await get_db_user(user_id)
    role = normalize_role(user_id, user["role"] if user else None)
    if role == "developer":
        return True
    return bool(user and user["is_registered"])


def _now_samara() -> datetime:
    return datetime.now(SAMARA_TZ)


def _format_date_ru(date_value: date) -> str:
    return date_value.strftime("%d.%m.%Y")


async def _show_date_step(target: Message | CallbackQuery, state: FSMContext) -> None:
    now_dt = _now_samara()
    data = await state.get_data()
    year = int(data.get("calendar_year", now_dt.year))
    month = int(data.get("calendar_month", now_dt.month))
    keyboard = build_calendar(year, month, now_dt.date())
    await state.set_state(BookingStates.waiting_date)
    await state.update_data(calendar_year=year, calendar_month=month)

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text("Выберите дату:", reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer("Выберите дату:", reply_markup=keyboard)


async def _show_time_step(target: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected_date_raw = data.get("booking_date")
    if not selected_date_raw:
        await _show_date_step(target, state)
        return

    selected_date = date.fromisoformat(selected_date_raw)
    slots = generate_time_slots(selected_date, _now_samara())
    await state.set_state(BookingStates.waiting_time)

    if not slots:
        if isinstance(target, CallbackQuery):
            await target.answer("На эту дату свободного времени нет. Выберите другую дату.", show_alert=True)
            await _show_date_step(target, state)
        else:
            await target.answer("На эту дату свободного времени нет. Выберите другую дату.")
            await _show_date_step(target, state)
        return

    keyboard = build_time_keyboard(slots)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text("Выберите время (шаг 15 минут):", reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer("Выберите время (шаг 15 минут):", reply_markup=keyboard)


async def _show_comment_step(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.waiting_comment)
    text = "Укажите комментарий к броне. Если его нет, нажмите «Продолжить»."
    keyboard = build_comment_keyboard()
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def _show_confirmation_step(target: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    booking_date = date.fromisoformat(data["booking_date"])
    comment = data.get("booking_comment", "")

    lines = [
        "Ваша заявка на бронь:",
        f"Дата: {_format_date_ru(booking_date)}",
        f"Время: {data['booking_time']}",
        f"Адрес: {ADDRESS_TEXT}",
        f"Гостей: {data['booking_guests']}",
        f"Имя: {data['booking_name']}",
    ]
    if comment:
        lines.append(f"Комментарий: {comment}")

    await state.set_state(BookingStates.waiting_confirmation)
    text = "\n".join(lines)
    keyboard = build_confirmation_keyboard()
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == BOOKING_BUTTON)
async def booking_start(message: Message, state: FSMContext) -> None:
    if not await _is_allowed(message.from_user.id):
        return

    await clear_state_preserving_navigation(state)
    now_dt = _now_samara()
    await state.set_state(BookingStates.waiting_name)
    await state.update_data(
        calendar_year=now_dt.year,
        calendar_month=now_dt.month,
    )
    await message.answer("На какое имя забронировать стол?")


@router.message(BookingStates.waiting_name)
async def booking_name_step(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введите имя текстом.")
        return

    name = " ".join(message.text.split())
    if not NAME_PATTERN.fullmatch(name):
        await message.answer("Имя должно содержать только буквы. Попробуйте ещё раз 🙂")
        return

    await state.update_data(booking_name=name)
    await _show_date_step(message, state)


@router.callback_query(BookingStates.waiting_date, F.data == BOOK_CALENDAR_NOOP)
async def booking_calendar_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(BookingStates.waiting_date, F.data == BOOK_DATE_BACK)
async def booking_date_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.waiting_name)
    if callback.message:
        await callback.message.edit_text("На какое имя забронировать стол?")
    await callback.answer()


@router.callback_query(BookingStates.waiting_date, F.data == BOOK_CALENDAR_NEXT)
async def booking_calendar_next(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    now_dt = _now_samara()
    year = int(data.get("calendar_year", now_dt.year))
    month = int(data.get("calendar_month", now_dt.month))

    month += 1
    if month > 12:
        month = 1
        year += 1

    await state.update_data(calendar_year=year, calendar_month=month)
    await _show_date_step(callback, state)


@router.callback_query(BookingStates.waiting_date, F.data == BOOK_CALENDAR_PREV)
async def booking_calendar_prev(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    now_dt = _now_samara()
    year = int(data.get("calendar_year", now_dt.year))
    month = int(data.get("calendar_month", now_dt.month))

    month -= 1
    if month < 1:
        month = 12
        year -= 1

    current_year = now_dt.year
    current_month = now_dt.month
    if (year, month) < (current_year, current_month):
        year = current_year
        month = current_month

    await state.update_data(calendar_year=year, calendar_month=month)
    await _show_date_step(callback, state)


@router.callback_query(BookingStates.waiting_date, F.data.startswith("book:cal:day:"))
async def booking_pick_day(callback: CallbackQuery, state: FSMContext) -> None:
    raw_date = callback.data.removeprefix("book:cal:day:")
    try:
        selected_date = date.fromisoformat(raw_date)
    except ValueError:
        await callback.answer()
        return
    today = _now_samara().date()

    if selected_date < today:
        await callback.answer("Вы выбрали прошедшую дату. Выберите ещё раз 🙂", show_alert=True)
        await _show_date_step(callback, state)
        return

    await state.update_data(booking_date=selected_date.isoformat())
    await state.set_state(BookingStates.waiting_guests)
    if callback.message:
        await callback.message.edit_text("Введите количество гостей")
    await callback.answer()


@router.message(BookingStates.waiting_guests)
async def booking_guests_step(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введите количество гостей числом от 1 до 20.")
        return

    raw_value = message.text.strip()
    if not raw_value.isdigit():
        await message.answer("Введите количество гостей числом от 1 до 20.")
        return

    guests = int(raw_value)
    if guests < 1 or guests > 20:
        await message.answer("Количество гостей должно быть от 1 до 20.")
        return

    await state.update_data(booking_guests=guests)
    await _show_time_step(message, state)


@router.callback_query(BookingStates.waiting_time, F.data == BOOK_TIME_BACK)
async def booking_time_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.waiting_guests)
    if callback.message:
        await callback.message.edit_text("Введите количество гостей")
    await callback.answer()


@router.callback_query(BookingStates.waiting_time, F.data.startswith("book:time:"))
async def booking_pick_time(callback: CallbackQuery, state: FSMContext) -> None:
    raw_time = callback.data.removeprefix("book:time:")

    try:
        chosen_time = datetime.strptime(raw_time, "%H:%M").time()
    except ValueError:
        await callback.answer()
        return

    selected_date = date.fromisoformat((await state.get_data())["booking_date"])
    now_dt = _now_samara()
    selected_dt = datetime.combine(selected_date, chosen_time, tzinfo=SAMARA_TZ)
    if selected_date == now_dt.date() and selected_dt <= now_dt:
        await callback.answer("Это время уже прошло. Выберите другое.", show_alert=True)
        await _show_time_step(callback, state)
        return

    await state.update_data(booking_time=raw_time)
    await _show_comment_step(callback, state)


@router.callback_query(BookingStates.waiting_comment, F.data == BOOK_COMMENT_SKIP)
async def booking_comment_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(booking_comment="")
    await _show_confirmation_step(callback, state)


@router.callback_query(BookingStates.waiting_comment, F.data == BOOK_COMMENT_BACK)
async def booking_comment_back(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_time_step(callback, state)


@router.message(BookingStates.waiting_comment)
async def booking_comment_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введите комментарий текстом или нажмите «Продолжить».")
        return

    comment = message.text.strip()
    if comment.lower() == "нет":
        comment = ""
    if len(comment) > 200:
        await message.answer("Комментарий должен быть до 200 символов.")
        return

    await state.update_data(booking_comment=comment)
    await _show_confirmation_step(message, state)


@router.callback_query(BookingStates.waiting_confirmation, F.data == BOOK_CONFIRM_BACK)
async def booking_confirmation_back(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_comment_step(callback, state)


@router.callback_query(BookingStates.waiting_confirmation, F.data == BOOK_CANCEL)
async def booking_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await callback.answer()
    if callback.message:
        await callback.message.answer("Бронирование отменено.")
    await render_main_by_role(callback, callback.from_user.id)


@router.callback_query(BookingStates.waiting_confirmation, F.data == BOOK_CONFIRM)
async def booking_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user = await get_db_user(callback.from_user.id)
    role = normalize_role(callback.from_user.id, user["role"] if user else None)
    phone = (user or {}).get("phone", "")

    if role == "user":
        daily_count = await count_user_bookings_today(callback.from_user.id)
        if daily_count >= 2:
            await clear_state_preserving_navigation(state)
            if callback.message:
                await callback.message.answer(
                    "Вы уже создали 2 заявки на бронь сегодня. Попробуйте завтра 🙂\n"
                    "Или позвоните, пожалуйста, нам по телефону +79999999999"
                )
            await callback.answer()
            await render_main_by_role(callback, callback.from_user.id)
            return

    try:
        booking_id = await create_booking(
            user_id=callback.from_user.id,
            name=data["booking_name"],
            phone=phone,
            date_value=data["booking_date"],
            time_value=data["booking_time"],
            guests=int(data["booking_guests"]),
            comment=data.get("booking_comment", ""),
        )
        await notify_hostesses_new_booking(callback.bot, booking_id)
        await log_user_event(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            phone=phone,
            event_type="booking",
            event_name="booking_created_pending",
            payload={"booking_id": booking_id},
        )
        await log_bot_event(
            level="INFO",
            source="booking",
            message="Создана новая заявка на бронь",
            details={"booking_id": booking_id, "user_id": callback.from_user.id},
        )
    except Exception as exc:
        await log_bot_event(
            level="ERROR",
            source="booking",
            message="Ошибка при создании заявки на бронь",
            details={"user_id": callback.from_user.id, "error": str(exc)},
        )
        await clear_state_preserving_navigation(state)
        if callback.message:
            await callback.message.answer("Не удалось создать заявку. Попробуйте ещё раз чуть позже.")
        await callback.answer()
        await render_main_by_role(callback, callback.from_user.id)
        return

    await clear_state_preserving_navigation(state)
    await callback.answer()
    if callback.message:
        booking_date = _format_date_ru(date.fromisoformat(data["booking_date"]))
        await callback.message.answer(
            "Ваша заявка на бронь принята ✅\n\n"
            f"Имя: {data['booking_name']}\n"
            f"Дата: {booking_date}\n"
            f"Время: {data['booking_time']}\n"
            f"Кол-во гостей: {data['booking_guests']}\n\n"
            "В течение 10 минут позвоним для подтверждения 😊"
        )
    await render_main_by_role(callback, callback.from_user.id)
