"""Inline buttons for the MAX bot UI."""

from __future__ import annotations

from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard

MENU_BOOKING_PAYLOAD = "menu:booking"
MENU_MY_APPOINTMENTS_PAYLOAD = "menu:my_appointments"
MENU_MASTERS_PAYLOAD = "menu:masters"
MENU_CONTACTS_PAYLOAD = "menu:contacts"
MENU_SUPPORT_PAYLOAD = "menu:support"

MENU_PAYLOADS = frozenset(
    {
        MENU_BOOKING_PAYLOAD,
        MENU_MY_APPOINTMENTS_PAYLOAD,
        MENU_MASTERS_PAYLOAD,
        MENU_CONTACTS_PAYLOAD,
        MENU_SUPPORT_PAYLOAD,
    }
)


def main_menu_keyboard() -> MaxInlineKeyboard:
    """Build the main menu inline keyboard."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)],
            [MaxButton(text="📅 Мои записи", payload=MENU_MY_APPOINTMENTS_PAYLOAD)],
            [MaxButton(text="👥 Мастера", payload=MENU_MASTERS_PAYLOAD)],
            [MaxButton(text="📍 Контакты", payload=MENU_CONTACTS_PAYLOAD)],
            [MaxButton(text="🆘 Поддержка", payload=MENU_SUPPORT_PAYLOAD)],
        ]
    )
