from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.core.logging import format_log_context
from app.core.ui_texts import (
    ADMIN_APPOINTMENTS_BTN,
    BOOK_APPOINTMENT_BTN,
    BROADCAST_BTN,
    CLIENTS_BTN,
    CONTACTS_BTN,
    DEV_ADMIN_PANEL_BTN,
    DEV_DIAGNOSTICS_BTN,
    MESSAGES_BTN,
    MY_APPOINTMENTS_BTN,
    NOTIFICATIONS_BTN,
    PERSONNEL_BTN,
    SETTINGS_BTN,
    SUPPORT_BTN,
    YCHECK_BTN,
    YCLIENTS_INTEGRATION_BTN,
)
from app.core.navigation import clear_state_preserving_navigation, render_main_by_role
from app.ui.texts import FALLBACK_MESSAGE

router = Router()
logger = logging.getLogger(__name__)

KNOWN_MENU_TEXTS = {
    BOOK_APPOINTMENT_BTN,
    MY_APPOINTMENTS_BTN,
    CONTACTS_BTN,
    SUPPORT_BTN,
    NOTIFICATIONS_BTN,
    ADMIN_APPOINTMENTS_BTN,
    CLIENTS_BTN,
    PERSONNEL_BTN,
    SETTINGS_BTN,
    MESSAGES_BTN,
    BROADCAST_BTN,
    DEV_DIAGNOSTICS_BTN,
    DEV_ADMIN_PANEL_BTN,
    YCLIENTS_INTEGRATION_BTN,
    YCHECK_BTN,
}


@router.message(
    StateFilter("*"),
    ~StateFilter("RegistrationStates:REG_NAME", "RegistrationStates:REG_BIRTHDATE", "RegistrationStates:REG_PHONE"),
    ~F.text.in_(KNOWN_MENU_TEXTS),
)
async def handle_global_fallback(message: Message, state: FSMContext) -> None:
    """Final fallback handler for any unhandled message content.

    It is registered after all other routers, so it never shadows
    existing handlers and only runs when nothing else matched.
    """
    await clear_state_preserving_navigation(state)
    user_id = message.from_user.id
    context = format_log_context(
        user_id=user_id,
        username=message.from_user.username if message.from_user else None,
        update_type=type(message).__name__,
        handler="global_fallback",
    )
    logger.info("Fallback handler triggered%s%s", " | " if context else "", context)
    await message.answer(FALLBACK_MESSAGE)
    await render_main_by_role(message, user_id)
