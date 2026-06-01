from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.core.config import get_settings
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK

from app.core.ui_texts import (
    BACK_BTN,
    BOOK_APPOINTMENT_BTN,
    CONTACTS_BTN,
    DEV_ADMIN_PANEL_BTN,
    DEV_DIAGNOSTICS_BTN,
    MAIN_MENU_BTN,
    BROADCAST_BTN,
    MY_APPOINTMENTS_BTN,
    PERSONNEL_BTN,
    SETTINGS_BTN,
    STATISTICS_BTN,
    SUPPORT_BTN,
    LOYALTY_BTN,
    YCLIENTS_INTEGRATION_BTN,
)

DETAILS_CALLBACK = "menu:account:details"


def _build_reply_keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _to_two_column_rows(buttons: list[str]) -> list[list[str]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def user_main_menu_kb() -> ReplyKeyboardMarkup:
    tail_buttons = [CONTACTS_BTN, SUPPORT_BTN]
    if get_settings().loyalty_enabled:
        tail_buttons.append(LOYALTY_BTN)
    tail_buttons.append(SETTINGS_BTN)
    return _build_reply_keyboard(
        [[BOOK_APPOINTMENT_BTN, MY_APPOINTMENTS_BTN], *_to_two_column_rows(tail_buttons)]
    )


def admin_main_menu_kb(*, show_statistics: bool = True, show_personnel: bool = True, show_settings: bool = True, show_messages: bool = True, show_broadcast: bool = True, show_yclients_integration: bool = False, show_ycheck: bool = True) -> ReplyKeyboardMarkup:
    _ = (show_messages, show_ycheck)
    tail_buttons: list[str] = [CONTACTS_BTN, SUPPORT_BTN]
    if get_settings().loyalty_enabled:
        tail_buttons.append(LOYALTY_BTN)
    if show_statistics:
        tail_buttons.append(STATISTICS_BTN)
    if show_personnel:
        tail_buttons.append(PERSONNEL_BTN)
    if show_settings:
        tail_buttons.append(SETTINGS_BTN)
    if show_broadcast:
        tail_buttons.append(BROADCAST_BTN)
    if show_yclients_integration:
        tail_buttons.append(YCLIENTS_INTEGRATION_BTN)
    rows: list[list[str]] = [[BOOK_APPOINTMENT_BTN, MY_APPOINTMENTS_BTN], *_to_two_column_rows(tail_buttons)]
    return _build_reply_keyboard(rows)


def developer_main_menu_kb(*, show_statistics: bool = True, show_personnel: bool = True, show_settings: bool = True, show_messages: bool = True, show_broadcast: bool = True, show_yclients_integration: bool = True, show_ycheck: bool = True, show_dev_diagnostics: bool = True, show_dev_admin_panel: bool = True) -> ReplyKeyboardMarkup:
    _ = (show_messages, show_ycheck)
    tail_buttons: list[str] = [CONTACTS_BTN, SUPPORT_BTN]
    if get_settings().loyalty_enabled:
        tail_buttons.append(LOYALTY_BTN)
    if show_statistics:
        tail_buttons.append(STATISTICS_BTN)
    if show_personnel:
        tail_buttons.append(PERSONNEL_BTN)
    if show_settings:
        tail_buttons.append(SETTINGS_BTN)
    if show_broadcast:
        tail_buttons.append(BROADCAST_BTN)
    if show_dev_diagnostics:
        tail_buttons.append(DEV_DIAGNOSTICS_BTN)
    if show_dev_admin_panel:
        tail_buttons.append(DEV_ADMIN_PANEL_BTN)
    if show_yclients_integration:
        tail_buttons.append(YCLIENTS_INTEGRATION_BTN)
    rows: list[list[str]] = [[BOOK_APPOINTMENT_BTN, MY_APPOINTMENTS_BTN], *_to_two_column_rows(tail_buttons)]
    return _build_reply_keyboard(rows)


def details_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подробнее", callback_data=DETAILS_CALLBACK)],
            [InlineKeyboardButton(text=MAIN_MENU_BTN, callback_data=NAV_HOME_CALLBACK)],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
        ]
    )


def back_reply_kb() -> ReplyKeyboardMarkup:
    return _build_reply_keyboard([[BACK_BTN], [MAIN_MENU_BTN]])


def contacts_inline_kb(*, address: str) -> InlineKeyboardMarkup:
    encoded_address = quote(address)
    links = {
        "yandex": f"https://yandex.ru/maps/?rtext=~{encoded_address}&rtt=auto",
        "2gis": f"https://2gis.ru/search/{encoded_address}",
        "google": (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination={encoded_address}&travelmode=driving"
        ),
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Яндекс Карты", url=links["yandex"])],
            [InlineKeyboardButton(text="2GIS", url=links["2gis"])],
            [InlineKeyboardButton(text="Google Maps", url=links["google"])],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text=MAIN_MENU_BTN, callback_data=NAV_HOME_CALLBACK)],
        ]
    )
