from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.ui_texts import BACK_BTN, MAIN_MENU_BTN

BALANCE_HISTORY_CALLBACK = "balance:history"
BALANCE_HISTORY_PAGE_CALLBACK = "balance:history:page"


def client_actions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Начислить от суммы",
                    callback_data="loy:code:amount",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Начислить вручную",
                    callback_data="loy:code:manual",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="➖ Списать",
                    callback_data="loy:code:spend",
                ),
            ],
            [InlineKeyboardButton(text=MAIN_MENU_BTN, callback_data=NAV_HOME_CALLBACK)],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
        ]
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="loy:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="loy:cancel"),
            ]
        ]
    )


def skip_reason_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="loy:manual:skip")]
        ]
    )


def balance_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 История начислений",
                    callback_data=BALANCE_HISTORY_CALLBACK,
                )
            ],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
        ]
    )


def balance_history_kb(current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    navigation_row: list[InlineKeyboardButton] = []
    if current_page > 0:
        navigation_row.append(
            InlineKeyboardButton(
                text="⬅️ ««",
                callback_data=f"{BALANCE_HISTORY_PAGE_CALLBACK}:{current_page - 1}",
            )
        )
    if current_page < total_pages - 1:
        navigation_row.append(
            InlineKeyboardButton(
                text="➡️ »»",
                callback_data=f"{BALANCE_HISTORY_PAGE_CALLBACK}:{current_page + 1}",
            )
        )
    keyboard: list[list[InlineKeyboardButton]] = []
    if navigation_row:
        keyboard.append(navigation_row)
    keyboard.append([InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
