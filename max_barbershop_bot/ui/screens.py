"""Screen builders for the MAX barbershop bot."""

from __future__ import annotations

from dataclasses import dataclass

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.ui.buttons import (
    main_menu_keyboard,
    settings_menu_keyboard,
    staff_menu_keyboard,
    navigation_keyboard,
    registration_consent_keyboard,
    registration_navigation_keyboard,
    registration_phone_keyboard,
)
from max_barbershop_bot.ui.texts import (
    MAIN_MENU_TEXT,
    REGISTRATION_BIRTHDATE_TEXT,
    REGISTRATION_NAME_TEXT,
    REGISTRATION_PERSONAL_DATA_POLICY_TEXT,
    REGISTRATION_PHONE_TEXT,
    REGISTRATION_POLICIES_TEXT,
    REGISTRATION_PRIVACY_POLICY_TEXT,
    SECTION_SOON_TEXT,
    SETTINGS_MENU_TEXT,
    STAFF_MENU_TEXT,
)


@dataclass(frozen=True)
class Screen:
    """Text plus optional keyboard for one MAX bot screen."""

    text: str
    keyboard: MaxInlineKeyboard | None = None


def main_menu_screen(role: str | None = None, *, display_name: str | None = None) -> Screen:
    """Build the main menu screen for the current role."""

    if display_name:
        text = f"✨ {display_name}, выберите действие в меню ниже 👇"
    else:
        text = MAIN_MENU_TEXT
    return Screen(text=text, keyboard=main_menu_keyboard(role))


def settings_menu_screen(role: str | None = None) -> Screen:
    """Build settings hub screen."""

    return Screen(text=SETTINGS_MENU_TEXT, keyboard=settings_menu_keyboard(role))


def staff_menu_screen(role: str | None = None) -> Screen:
    """Build staff management screen."""

    return Screen(text=STAFF_MENU_TEXT, keyboard=staff_menu_keyboard(role))


def placeholder_screen() -> Screen:
    """Build a temporary section placeholder screen."""

    return Screen(text=SECTION_SOON_TEXT, keyboard=navigation_keyboard())


def registration_consent_screen(*, privacy_accepted: bool = False, personal_accepted: bool = False) -> Screen:
    """Build the registration policy acceptance screen."""

    return Screen(
        text=REGISTRATION_POLICIES_TEXT,
        keyboard=registration_consent_keyboard(
            privacy_accepted=privacy_accepted,
            personal_accepted=personal_accepted,
        ),
    )


def registration_privacy_policy_screen() -> Screen:
    """Build the privacy policy screen."""

    return Screen(text=REGISTRATION_PRIVACY_POLICY_TEXT, keyboard=registration_navigation_keyboard())


def registration_personal_data_policy_screen() -> Screen:
    """Build the personal data policy screen."""

    return Screen(text=REGISTRATION_PERSONAL_DATA_POLICY_TEXT, keyboard=registration_navigation_keyboard())


def registration_phone_screen() -> Screen:
    """Build the registration phone screen."""

    return Screen(text=REGISTRATION_PHONE_TEXT, keyboard=registration_phone_keyboard())


def registration_name_screen() -> Screen:
    """Build the registration name screen."""

    return Screen(text=REGISTRATION_NAME_TEXT, keyboard=registration_navigation_keyboard())


def registration_birthdate_screen() -> Screen:
    """Build the registration birthdate screen."""

    return Screen(text=REGISTRATION_BIRTHDATE_TEXT, keyboard=registration_navigation_keyboard())
