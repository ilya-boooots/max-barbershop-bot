"""Screen builders for the MAX barbershop bot."""

from __future__ import annotations

from dataclasses import dataclass

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.ui.buttons import (
    main_menu_keyboard,
    staff_menu_keyboard,
    navigation_keyboard,
    registration_consent_keyboard,
    registration_navigation_keyboard,
    registration_phone_keyboard,
)
from max_barbershop_bot.ui.texts import (
    MAIN_MENU_TEXT,
    REGISTRATION_NAME_TEXT,
    REGISTRATION_PHONE_TEXT,
    REGISTRATION_WELCOME_TEXT,
    SECTION_SOON_TEXT,
    STAFF_MENU_TEXT,
)


@dataclass(frozen=True)
class Screen:
    """Text plus optional keyboard for one MAX bot screen."""

    text: str
    keyboard: MaxInlineKeyboard | None = None


def main_menu_screen(role: str | None = None) -> Screen:
    """Build the main menu screen for the current role."""

    return Screen(text=MAIN_MENU_TEXT, keyboard=main_menu_keyboard(role))


def staff_menu_screen(role: str | None = None) -> Screen:
    """Build staff management screen."""

    return Screen(text=STAFF_MENU_TEXT, keyboard=staff_menu_keyboard(role))


def placeholder_screen() -> Screen:
    """Build a temporary section placeholder screen."""

    return Screen(text=SECTION_SOON_TEXT, keyboard=navigation_keyboard())


def registration_consent_screen() -> Screen:
    """Build the registration consent screen."""

    return Screen(text=REGISTRATION_WELCOME_TEXT, keyboard=registration_consent_keyboard())


def registration_phone_screen() -> Screen:
    """Build the registration phone screen."""

    return Screen(text=REGISTRATION_PHONE_TEXT, keyboard=registration_phone_keyboard())


def registration_name_screen() -> Screen:
    """Build the registration name screen."""

    return Screen(text=REGISTRATION_NAME_TEXT, keyboard=registration_navigation_keyboard())
