"""Screen builders for the MAX barbershop bot."""

from __future__ import annotations

from dataclasses import dataclass

from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.ui.buttons import main_menu_keyboard, navigation_keyboard
from max_barbershop_bot.ui.texts import MAIN_MENU_TEXT, SECTION_SOON_TEXT


@dataclass(frozen=True)
class Screen:
    """Text plus optional keyboard for one MAX bot screen."""

    text: str
    keyboard: MaxInlineKeyboard | None = None


def main_menu_screen() -> Screen:
    """Build the main menu screen."""

    return Screen(text=MAIN_MENU_TEXT, keyboard=main_menu_keyboard())


def placeholder_screen() -> Screen:
    """Build a temporary section placeholder screen."""

    return Screen(text=SECTION_SOON_TEXT, keyboard=navigation_keyboard())
