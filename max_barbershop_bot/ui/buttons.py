"""Central Russian UI button texts and callback payloads for MAX."""

from __future__ import annotations

from max_barbershop_bot.max_api.keyboards import MaxInlineKeyboard, callback_button, inline_keyboard

BOOKING_TEXT = "✂️ Записаться"
MY_BOOKINGS_TEXT = "📅 Мои записи"
MASTERS_TEXT = "👥 Мастера"
CONTACTS_TEXT = "📍 Контакты"
SUPPORT_TEXT = "🆘 Поддержка"
BACK_TEXT = "⬅️ Назад"
HOME_TEXT = "🏠 Главное меню"

BOOKING_CALLBACK = "menu:booking"
MY_BOOKINGS_CALLBACK = "menu:my_bookings"
MASTERS_CALLBACK = "menu:masters"
CONTACTS_CALLBACK = "menu:contacts"
SUPPORT_CALLBACK = "menu:support"
BACK_CALLBACK = "nav:back"
HOME_CALLBACK = "nav:home"


def build_main_menu_keyboard() -> MaxInlineKeyboard:
    """Build the future MAX main menu with callback buttons."""

    return inline_keyboard(
        [
            [callback_button(BOOKING_TEXT, BOOKING_CALLBACK)],
            [
                callback_button(MY_BOOKINGS_TEXT, MY_BOOKINGS_CALLBACK),
                callback_button(MASTERS_TEXT, MASTERS_CALLBACK),
            ],
            [
                callback_button(CONTACTS_TEXT, CONTACTS_CALLBACK),
                callback_button(SUPPORT_TEXT, SUPPORT_CALLBACK),
            ],
        ]
    )
