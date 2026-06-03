"""Inline buttons for the MAX bot UI."""

from __future__ import annotations

from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard

MENU_BOOKING_PAYLOAD = "menu:booking"
MENU_MY_BOOKINGS_PAYLOAD = "menu:my_bookings"
MENU_MASTERS_PAYLOAD = "menu:masters"
MENU_CONTACTS_PAYLOAD = "menu:contacts"
MENU_SUPPORT_PAYLOAD = "menu:support"

NAV_BACK_PAYLOAD = "nav:back"
NAV_HOME_PAYLOAD = "nav:home"

REGISTRATION_CONSENT_ACCEPT_PAYLOAD = "registration:consent:accept"
REGISTRATION_CONSENT_DECLINE_PAYLOAD = "registration:consent:decline"
REGISTRATION_BACK_PAYLOAD = "registration:nav:back"
REGISTRATION_HOME_PAYLOAD = "registration:nav:home"

MENU_PAYLOADS = frozenset(
    {
        MENU_BOOKING_PAYLOAD,
        MENU_MY_BOOKINGS_PAYLOAD,
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
            [MaxButton(text="📅 Мои записи", payload=MENU_MY_BOOKINGS_PAYLOAD)],
            [MaxButton(text="👥 Мастера", payload=MENU_MASTERS_PAYLOAD)],
            [MaxButton(text="📍 Контакты", payload=MENU_CONTACTS_PAYLOAD)],
            [MaxButton(text="🆘 Поддержка", payload=MENU_SUPPORT_PAYLOAD)],
        ]
    )


def navigation_keyboard() -> MaxInlineKeyboard:
    """Build Back/Home navigation buttons for section screens."""

    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD),
                MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD),
            ]
        ]
    )


def registration_consent_keyboard() -> MaxInlineKeyboard:
    """Build consent buttons for the registration start screen."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Согласен", payload=REGISTRATION_CONSENT_ACCEPT_PAYLOAD)],
            [MaxButton(text="❌ Не согласен", payload=REGISTRATION_CONSENT_DECLINE_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=REGISTRATION_HOME_PAYLOAD)],
        ]
    )


def registration_phone_keyboard() -> MaxInlineKeyboard:
    """Build phone step buttons with contact request and safe navigation."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📱 Отправить телефон", type="request_contact")],
            [
                MaxButton(text="⬅️ Назад", payload=REGISTRATION_BACK_PAYLOAD),
                MaxButton(text="🏠 Главное меню", payload=REGISTRATION_HOME_PAYLOAD),
            ],
        ]
    )


def registration_navigation_keyboard() -> MaxInlineKeyboard:
    """Build registration Back/Home navigation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="⬅️ Назад", payload=REGISTRATION_BACK_PAYLOAD),
                MaxButton(text="🏠 Главное меню", payload=REGISTRATION_HOME_PAYLOAD),
            ]
        ]
    )
