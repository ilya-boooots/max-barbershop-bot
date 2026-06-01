from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.ui.buttons import BACK, HOME
from app.ui.callbacks import NAV_BACK, NAV_HOME


def nav_inline_kb(*, include_home: bool = True) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=BACK, callback_data=NAV_BACK)]]
    if include_home:
        rows.append([InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
