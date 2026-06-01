from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK


def reports_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать клиентов (CSV)", callback_data="reports:csv")],
            [InlineKeyboardButton(text="📄 Показать список", callback_data="reports:list:1")],
            [InlineKeyboardButton(text="🔎 Поиск клиента", callback_data="reports:search:prompt")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def reports_list_kb(clients: list[dict], page: int, total_pages: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []

    index_row: list[InlineKeyboardButton] = []
    for idx, client in enumerate(clients, start=1):
        index_row.append(
            InlineKeyboardButton(
                text=str(idx),
                callback_data=f"reports:open:{client['tg_id']}:{page}",
            )
        )
        if len(index_row) == 5:
            keyboard.append(index_row)
            index_row = []
    if index_row:
        keyboard.append(index_row)

    keyboard.append(
        [
            InlineKeyboardButton(text="⬅️", callback_data=f"reports:list:{max(1, page - 1)}"),
            InlineKeyboardButton(text=f"{page}/{max(total_pages, 1)}", callback_data="reports:noop"),
            InlineKeyboardButton(text="➡️", callback_data=f"reports:list:{min(total_pages, page + 1)}"),
        ]
    )
    keyboard.append(
        [InlineKeyboardButton(text="🔍 Открыть клиента", callback_data=f"reports:open_prompt:{page}")]
    )
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="reports:menu")])
    keyboard.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def reports_search_results_kb(clients: list[dict[str, str | int]]) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{client.get('name') or client.get('username') or client.get('tg_id')}",
                callback_data=f"reports:open:{int(client['tg_id'])}:1",
            )
        ]
        for client in clients
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="reports:menu")])
    keyboard.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
