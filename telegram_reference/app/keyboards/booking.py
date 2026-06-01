from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

BOOK_CALENDAR_NOOP = "book:cal:noop"
BOOK_CALENDAR_PREV = "book:cal:prev"
BOOK_CALENDAR_NEXT = "book:cal:next"

BOOK_DATE_BACK = "book:date:back"
BOOK_TIME_BACK = "book:time:back"
BOOK_COMMENT_SKIP = "book:comment:skip"
BOOK_COMMENT_BACK = "book:comment:back"
BOOK_CONFIRM = "book:confirm"
BOOK_CANCEL = "book:cancel"
BOOK_CONFIRM_BACK = "book:confirm:back"

try:
    SAMARA_TZ = ZoneInfo("Europe/Samara")
except ZoneInfoNotFoundError:
    SAMARA_TZ = timezone(timedelta(hours=4))

RUS_MONTHS = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]


def month_label(year: int, month: int) -> str:
    return f"{RUS_MONTHS[month - 1]} {year}"


def build_calendar(year: int, month: int, min_date: date) -> InlineKeyboardMarkup:
    first_weekday, days_in_month = calendar.monthrange(year, month)
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=month_label(year, month), callback_data=BOOK_CALENDAR_NOOP)],
        [
            InlineKeyboardButton(text="Предыдущий месяц", callback_data=BOOK_CALENDAR_PREV),
            InlineKeyboardButton(text="Следующий месяц", callback_data=BOOK_CALENDAR_NEXT),
        ],
        [
            InlineKeyboardButton(text="ПН", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="ВТ", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="СР", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="ЧТ", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="ПТ", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="СБ", callback_data=BOOK_CALENDAR_NOOP),
            InlineKeyboardButton(text="ВС", callback_data=BOOK_CALENDAR_NOOP),
        ],
    ]

    current_row: list[InlineKeyboardButton] = []
    for _ in range(first_weekday):
        current_row.append(InlineKeyboardButton(text=" ", callback_data=BOOK_CALENDAR_NOOP))

    for day in range(1, days_in_month + 1):
        selected_date = date(year, month, day)
        if selected_date < min_date:
            callback_data = BOOK_CALENDAR_NOOP
        else:
            callback_data = f"book:cal:day:{selected_date.isoformat()}"
        current_row.append(InlineKeyboardButton(text=str(day), callback_data=callback_data))
        if len(current_row) == 7:
            rows.append(current_row)
            current_row = []

    if current_row:
        while len(current_row) < 7:
            current_row.append(InlineKeyboardButton(text=" ", callback_data=BOOK_CALENDAR_NOOP))
        rows.append(current_row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=BOOK_DATE_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_time_keyboard(times: list[time]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in times:
        row.append(
            InlineKeyboardButton(
                text=slot.strftime("%H:%M"),
                callback_data=f"book:time:{slot.strftime('%H:%M')}",
            )
        )
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=BOOK_TIME_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data=BOOK_COMMENT_SKIP)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=BOOK_COMMENT_BACK)],
        ]
    )


def build_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=BOOK_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=BOOK_CANCEL)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=BOOK_CONFIRM_BACK)],
        ]
    )


def generate_time_slots(selected_date: date, now_dt: datetime) -> list[time]:
    slots: list[time] = []
    start_dt = datetime.combine(selected_date, time(10, 0), tzinfo=SAMARA_TZ)
    end_dt = datetime.combine(selected_date, time(23, 45), tzinfo=SAMARA_TZ)

    current = start_dt
    while current <= end_dt:
        if selected_date > now_dt.date() or current > now_dt:
            slots.append(current.timetz().replace(tzinfo=None))
        current += timedelta(minutes=15)
    return slots
