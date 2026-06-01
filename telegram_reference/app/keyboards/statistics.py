from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.booking import month_label
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK

CB_PREFIX = "stats"


def statistics_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def stats_calendar_kb(*, year: int, month: int) -> InlineKeyboardMarkup:
    first_weekday, days_in_month = calendar.monthrange(year, month)
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=month_label(year, month), callback_data=f"{CB_PREFIX}:cal:noop")],
        [
            InlineKeyboardButton(text="Предыдущий месяц", callback_data=f"{CB_PREFIX}:cal:prev"),
            InlineKeyboardButton(text="Следующий месяц", callback_data=f"{CB_PREFIX}:cal:next"),
        ],
        [
            InlineKeyboardButton(text="ПН", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="ВТ", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="СР", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="ЧТ", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="ПТ", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="СБ", callback_data=f"{CB_PREFIX}:cal:noop"),
            InlineKeyboardButton(text="ВС", callback_data=f"{CB_PREFIX}:cal:noop"),
        ],
    ]

    current_row: list[InlineKeyboardButton] = []
    for _ in range(first_weekday):
        current_row.append(InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}:cal:noop"))

    for day in range(1, days_in_month + 1):
        selected = date(year, month, day)
        current_row.append(
            InlineKeyboardButton(text=str(day), callback_data=f"{CB_PREFIX}:cal:day:{selected.isoformat()}")
        )
        if len(current_row) == 7:
            rows.append(current_row)
            current_row = []

    if current_row:
        while len(current_row) < 7:
            current_row.append(InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}:cal:noop"))
        rows.append(current_row)

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
