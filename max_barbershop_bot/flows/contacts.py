"""Contacts flow handlers for the MAX bot."""

from __future__ import annotations

from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactsService, format_contacts_text
from max_barbershop_bot.ui.buttons import MENU_CONTACTS_PAYLOAD, navigation_keyboard


def register_contacts_routes(router: Router) -> None:
    """Register contacts callbacks."""

    router.on_callback(MENU_CONTACTS_PAYLOAD, handle_contacts)


async def handle_contacts(context: RouterContext) -> None:
    """Open the contacts screen from the main menu."""

    await context.answer_callback("Открываем контакты 📍")
    _open_contacts_state(context)

    service = ContactsService(YClientsSettingsRepository(_database_path()))
    contact_info = await service.get_contacts()
    await context.send_text(format_contacts_text(contact_info), keyboard=navigation_keyboard())


def _open_contacts_state(context: RouterContext) -> None:
    max_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    current_screen = state.get_current_screen(max_user_id, chat_id)
    if current_screen != state.CONTACTS_SCREEN:
        state.push_screen(max_user_id, chat_id, current_screen)
    state.set_current_screen(max_user_id, chat_id, state.CONTACTS_SCREEN)


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
