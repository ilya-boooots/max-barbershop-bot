from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.core.auth import ROLE_ORDER, normalize_role
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.ui_texts import BACK_BTN

PERSONNEL_MENU_ACTIONS = [
    ("👀 Показать весь персонал", "staff:menu:show_all"),
    ("➕ Назначить роль", "staff:menu:assign"),
    ("➖ Снять роль", "staff:menu:remove"),
]

ROLE_SELECTION_ACTIONS = [
    ("🧑‍💻 Разработчик", "developer"),
    ("🛠️ Администратор", "admin"),
    ("📋 Управляющий", "manager"),
]


def _append_navigation_rows(keyboard: list[list[InlineKeyboardButton]]) -> list[list[InlineKeyboardButton]]:
    keyboard.append([InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)])
    keyboard.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)])
    return keyboard


def staff_panel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🧾 Операции"),
                KeyboardButton(text="🔍 Найти клиента / 📷 Сканировать QR"),
            ],
            [
                KeyboardButton(text="📊 Отчёты"),
                KeyboardButton(text="👥 Персонал"),
            ],
            [KeyboardButton(text="💬 Сообщения")],
            [KeyboardButton(text="⬅️ В меню клиента")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def personnel_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👀 Показать весь персонал")],
            [KeyboardButton(text="➕ Назначить роль")],
            [KeyboardButton(text="➖ Снять роль")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def personnel_menu_inline_kb(can_manage: bool) -> InlineKeyboardMarkup:
    actions = PERSONNEL_MENU_ACTIONS
    if not can_manage:
        actions = [action for action in PERSONNEL_MENU_ACTIONS if action[1] not in {"staff:menu:assign", "staff:menu:remove"}]
    keyboard = [[InlineKeyboardButton(text=label, callback_data=callback)] for label, callback in actions]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_messages_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Рассылка всем", callback_data="staff:msg:broadcast")],
            [InlineKeyboardButton(text="✉️ Написать пользователю", callback_data="staff:msg:direct")],
            [InlineKeyboardButton(text="📥 Входящие (диалоги)", callback_data="staff:msg:inbox")],
            [InlineKeyboardButton(text="🔎 Найти диалог", callback_data="staff:msg:search_thread")],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def broadcast_segment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем пользователям", callback_data="staff:msg:broadcast:segment:all")],
            [InlineKeyboardButton(text="🔥 Активным за 7 дней", callback_data="staff:msg:broadcast:segment:active_7")],
            [InlineKeyboardButton(text="🔥 Активным за 30 дней", callback_data="staff:msg:broadcast:segment:active_30")],
            [InlineKeyboardButton(text="🔥 Активным за 90 дней", callback_data="staff:msg:broadcast:segment:active_90")],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def confirm_action_kb(confirm_data: str, cancel_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data=confirm_data),
                InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data),
            ]
        ]
    )


def select_user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выбрать", callback_data="staff:msg:user_select"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="staff:msg:cancel"),
            ]
        ]
    )


def user_reply_kb(thread_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"thread:reply:{thread_id}")]
        ]
    )


def staff_thread_controls_kb(thread_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"staff:thread:reply:{thread_id}")],
            [InlineKeyboardButton(text="✅ Закрыть диалог", callback_data=f"staff:thread:close:{thread_id}")],
        ]
    )


def thread_list_kb(threads: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"staff:thread:open:{thread_id}")]
        for thread_id, label in threads
    ]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_role_select_kb(
    actor_tg_id: int,
    actor_role: str | None,
    target_tg_id: int,
    target_role: str | None,
) -> InlineKeyboardMarkup:
    resolved_actor_role = normalize_role(actor_tg_id, actor_role)
    _ = normalize_role(target_tg_id, target_role)
    keyboard = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"staff:assign:{target_tg_id}:{role}",
            )
        ]
        for label, role in ROLE_SELECTION_ACTIONS
        if ROLE_ORDER.get(resolved_actor_role, 0) > ROLE_ORDER.get(role, 0)
    ]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=f"👤 {label}", callback_data=f"staff:card:open:{tg_id}")]
        for tg_id, label in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_client_search_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"staff:client:open:{tg_id}")]
        for tg_id, label in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_card_kb(can_manage: bool, target_tg_id: int) -> InlineKeyboardMarkup:
    _ = can_manage
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.append(
        [
            InlineKeyboardButton(
                text="📜 Журнал действий",
                callback_data=f"staff:card:logs:{target_tg_id}:1",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="📚 История ролей",
                callback_data=f"staff:card:history:{target_tg_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_name_confirm_kb() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="✅ Оставить", callback_data="staff:name:keep"),
            InlineKeyboardButton(text="✏️ Изменить имя", callback_data="staff:name:edit"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def staff_logs_pagination_kb(
    target_tg_id: int,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    controls: list[InlineKeyboardButton] = []
    if page > 1:
        controls.append(
            InlineKeyboardButton(
                text=BACK_BTN,
                callback_data=f"staff:card:logs:{target_tg_id}:{page - 1}",
            )
        )
    if page < total_pages:
        controls.append(
            InlineKeyboardButton(
                text="➡️ Далее",
                callback_data=f"staff:card:logs:{target_tg_id}:{page + 1}",
            )
        )
    if controls:
        keyboard.append(controls)
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_remove_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=f"👤 {label}", callback_data=f"staff:remove:pick:{tg_id}")]
        for tg_id, label in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))


def staff_remove_confirm_kb(target_tg_id: int, can_remove: bool) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if can_remove:
        keyboard.append([InlineKeyboardButton(text="✅ Снять роль", callback_data=f"staff:remove:confirm:{target_tg_id}")])
    return InlineKeyboardMarkup(inline_keyboard=_append_navigation_rows(keyboard))
