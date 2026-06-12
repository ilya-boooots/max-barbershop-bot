"""Registration flow handlers for the MAX bot."""

from __future__ import annotations

import logging

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.users import User, UserProfileUpdate, UsersRepository
from max_barbershop_bot.services.registration import (
    contains_contact_attachment,
    extract_contact_phone,
    mask_phone,
    normalize_phone,
    save_registration_profile,
    validate_birthdate,
    validate_name,
)
from max_barbershop_bot.ui.buttons import (
    REGISTRATION_BACK_PAYLOAD,
    REGISTRATION_CONTINUE_PAYLOAD,
    REGISTRATION_HOME_PAYLOAD,
    REGISTRATION_OPEN_PERSONAL_PAYLOAD,
    REGISTRATION_OPEN_PRIVACY_PAYLOAD,
    REGISTRATION_TOGGLE_PERSONAL_PAYLOAD,
    REGISTRATION_TOGGLE_PRIVACY_PAYLOAD,
)
from max_barbershop_bot.ui.screens import (
    registration_birthdate_screen,
    registration_consent_screen,
    registration_name_screen,
    registration_personal_data_policy_screen,
    registration_phone_screen,
    registration_privacy_policy_screen,
)
from max_barbershop_bot.ui.texts import (
    REGISTRATION_BIRTHDATE_INVALID_TEXT,
    REGISTRATION_COMPLETE_TEXT,
    REGISTRATION_CONTACT_PHONE_MISSING_TEXT,
    REGISTRATION_NAME_INVALID_TEXT,
    REGISTRATION_PHONE_INVALID_TEXT,
    REGISTRATION_REQUIRED_TEXT,
)

logger = logging.getLogger(__name__)

_PRIVACY_ACCEPTED_KEY = "registration_privacy_accepted"
_PERSONAL_ACCEPTED_KEY = "registration_personal_accepted"
_PHONE_KEY = "registration_phone"
_NAME_KEY = "registration_name"
_BIRTHDATE_KEY = "registration_birthdate"
_BIRTHDATE_ONLY_KEY = "registration_birthdate_only"


def register_registration_routes(router: Router) -> None:
    """Register callbacks and screen-scoped text handlers for registration."""

    router.on_callback(REGISTRATION_OPEN_PRIVACY_PAYLOAD, handle_open_privacy_policy)
    router.on_callback(REGISTRATION_OPEN_PERSONAL_PAYLOAD, handle_open_personal_data_policy)
    router.on_callback(REGISTRATION_TOGGLE_PRIVACY_PAYLOAD, handle_toggle_privacy_policy)
    router.on_callback(REGISTRATION_TOGGLE_PERSONAL_PAYLOAD, handle_toggle_personal_data_policy)
    router.on_callback(REGISTRATION_CONTINUE_PAYLOAD, handle_policies_continue)
    router.on_callback(REGISTRATION_BACK_PAYLOAD, handle_registration_back)
    router.on_callback(REGISTRATION_HOME_PAYLOAD, handle_registration_home)
    router.on_screen_text(state.REGISTRATION_NAME_SCREEN, handle_name_input)
    router.on_screen_text(state.REGISTRATION_BIRTHDATE_SCREEN, handle_birthdate_input)
    router.on_screen_text(state.REGISTRATION_PHONE_SCREEN, handle_phone_input)


async def start_registration(context: RouterContext) -> None:
    """Reset temporary data and show the first required registration screen."""

    platform_user_id = context.event.platform_user_id
    chat_id = context.event.chat_id
    state.clear_state_data(platform_user_id, chat_id)

    user = _find_current_user(platform_user_id)
    if user is not None and user.first_name and user.phone and not user.birthdate:
        state.set_state_data_value(platform_user_id, chat_id, _BIRTHDATE_ONLY_KEY, True)
        state.set_current_screen(platform_user_id, chat_id, state.REGISTRATION_BIRTHDATE_SCREEN)
        screen = registration_birthdate_screen()
        await context.send_text(screen.text, keyboard=screen.keyboard)
        return

    state.set_current_screen(platform_user_id, chat_id, state.REGISTRATION_CONSENT_SCREEN)
    screen = _policy_screen(platform_user_id, chat_id)
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_open_privacy_policy(context: RouterContext) -> None:
    """Show privacy policy text."""

    await context.answer_callback("🔐 Политика конфиденциальности")
    state.push_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_CONSENT_SCREEN)
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.REGISTRATION_PRIVACY_POLICY_SCREEN,
    )
    screen = registration_privacy_policy_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_open_personal_data_policy(context: RouterContext) -> None:
    """Show personal data policy text."""

    await context.answer_callback("🔐 Политика обработки персональных данных")
    state.push_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_CONSENT_SCREEN)
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.REGISTRATION_PERSONAL_DATA_POLICY_SCREEN,
    )
    screen = registration_personal_data_policy_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def handle_toggle_privacy_policy(context: RouterContext) -> None:
    """Toggle privacy policy acceptance and redraw the policy menu."""

    await context.answer_callback("✅ Принять политику конфиденциальности")
    _toggle_policy_state(context, _PRIVACY_ACCEPTED_KEY)
    await _show_consent(context)


async def handle_toggle_personal_data_policy(context: RouterContext) -> None:
    """Toggle personal data policy acceptance and redraw the policy menu."""

    await context.answer_callback("✅ Принять политику обработки персональных данных")
    _toggle_policy_state(context, _PERSONAL_ACCEPTED_KEY)
    await _show_consent(context)


async def handle_policies_continue(context: RouterContext) -> None:
    """Move from accepted policies to Telegram registration order: name → birthdate → phone."""

    if not _policies_accepted(context.event.platform_user_id, context.event.chat_id):
        await context.answer_callback(REGISTRATION_REQUIRED_TEXT)
        await _show_consent(context)
        return

    await context.answer_callback("Продолжаем регистрацию ✅")
    state.push_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_CONSENT_SCREEN)
    await _show_name(context)


async def handle_name_input(context: RouterContext) -> None:
    """Validate name and move to the birthdate step."""

    name = validate_name(context.event.text)
    if name is None:
        await context.send_text(REGISTRATION_NAME_INVALID_TEXT)
        return

    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _NAME_KEY, name)
    state.push_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_NAME_SCREEN)
    await _show_birthdate(context)


async def handle_birthdate_input(context: RouterContext) -> None:
    """Validate birthdate and either finish a partial profile or move to phone."""

    validation = validate_birthdate(context.event.text)
    if not validation.is_valid or validation.birthdate is None:
        await context.send_text(REGISTRATION_BIRTHDATE_INVALID_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return

    state.set_state_data_value(platform_user_id, context.event.chat_id, _BIRTHDATE_KEY, validation.birthdate)
    if state.get_state_data_value(platform_user_id, context.event.chat_id, _BIRTHDATE_ONLY_KEY) is True:
        repository = UsersRepository(_database_path())
        user = repository.update_profile(
            platform_user_id,
            UserProfileUpdate(birthdate=validation.birthdate),
        )
        if user is None:
            await context.send_text(REGISTRATION_REQUIRED_TEXT)
            await start_registration(context)
            return
        await _complete_registration(context)
        return

    state.push_screen(platform_user_id, context.event.chat_id, state.REGISTRATION_BIRTHDATE_SCREEN)
    await _show_phone(context)


async def handle_phone_input(context: RouterContext) -> None:
    """Validate a manual/contact phone, save profile and open the main menu."""

    phone = extract_contact_phone(context.event.attachments)
    if phone is None and contains_contact_attachment(context.event.attachments):
        await context.send_text(REGISTRATION_CONTACT_PHONE_MISSING_TEXT)
        return

    phone = phone or normalize_phone(context.event.text)
    if phone is None:
        await context.send_text(REGISTRATION_PHONE_INVALID_TEXT)
        return

    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        await context.send_text("Не удалось определить пользователя 😕 Нажмите /start ещё раз.")
        return

    chat_id = context.event.chat_id
    state.set_state_data_value(platform_user_id, chat_id, _PHONE_KEY, phone)
    name = state.get_state_data_value(platform_user_id, chat_id, _NAME_KEY)
    birthdate = state.get_state_data_value(platform_user_id, chat_id, _BIRTHDATE_KEY)
    if not isinstance(name, str) or not isinstance(birthdate, str):
        await context.send_text(REGISTRATION_REQUIRED_TEXT)
        await _show_name(context)
        return

    repository = UsersRepository(_database_path())
    save_registration_profile(
        repository,
        platform_user_id=platform_user_id,
        phone=phone,
        first_name=name,
        birthdate=birthdate,
    )
    logger.info("Registration phone accepted: user=%s phone=%s", platform_user_id, mask_phone(phone))
    await _complete_registration(context)


async def handle_registration_back(context: RouterContext) -> None:
    """Navigate backward inside the registration flow without falling into main menu."""

    await context.answer_callback("Возвращаемся назад ⬅️")
    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen in {
        state.REGISTRATION_PRIVACY_POLICY_SCREEN,
        state.REGISTRATION_PERSONAL_DATA_POLICY_SCREEN,
        state.REGISTRATION_NAME_SCREEN,
    }:
        await _show_consent(context)
        return
    if current_screen == state.REGISTRATION_BIRTHDATE_SCREEN:
        if state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _BIRTHDATE_ONLY_KEY) is True:
            await _show_birthdate(context)
            return
        await _show_name(context)
        return
    if current_screen == state.REGISTRATION_PHONE_SCREEN:
        await _show_birthdate(context)
        return
    await _show_consent(context)


async def handle_registration_home(context: RouterContext) -> None:
    """Keep unregistered users inside registration when they press Home."""

    await context.answer_callback(REGISTRATION_REQUIRED_TEXT)
    await context.send_text(REGISTRATION_REQUIRED_TEXT)
    current_screen = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current_screen == state.REGISTRATION_BIRTHDATE_SCREEN and state.get_state_data_value(
        context.event.platform_user_id,
        context.event.chat_id,
        _BIRTHDATE_ONLY_KEY,
    ) is True:
        await _show_birthdate(context)
        return
    await _show_consent(context)


async def _show_consent(context: RouterContext) -> None:
    state.set_current_screen(
        context.event.platform_user_id,
        context.event.chat_id,
        state.REGISTRATION_CONSENT_SCREEN,
    )
    screen = _policy_screen(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_name(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_NAME_SCREEN)
    screen = registration_name_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_birthdate(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_BIRTHDATE_SCREEN)
    screen = registration_birthdate_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _show_phone(context: RouterContext) -> None:
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.REGISTRATION_PHONE_SCREEN)
    screen = registration_phone_screen()
    await context.send_text(screen.text, keyboard=screen.keyboard)


async def _complete_registration(context: RouterContext) -> None:
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(REGISTRATION_COMPLETE_TEXT)
    from max_barbershop_bot.flows.menu import show_main_menu

    await show_main_menu(context)


def _policy_screen(platform_user_id: str | None, chat_id: str | None):
    return registration_consent_screen(
        privacy_accepted=state.get_state_data_value(platform_user_id, chat_id, _PRIVACY_ACCEPTED_KEY) is True,
        personal_accepted=state.get_state_data_value(platform_user_id, chat_id, _PERSONAL_ACCEPTED_KEY) is True,
    )


def _toggle_policy_state(context: RouterContext, key: str) -> None:
    current = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, key) is True
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, key, not current)


def _policies_accepted(platform_user_id: str | None, chat_id: str | None) -> bool:
    return (
        state.get_state_data_value(platform_user_id, chat_id, _PRIVACY_ACCEPTED_KEY) is True
        and state.get_state_data_value(platform_user_id, chat_id, _PERSONAL_ACCEPTED_KEY) is True
    )


def _find_current_user(platform_user_id: str | None) -> User | None:
    if platform_user_id is None:
        return None
    return UsersRepository(_database_path()).find_by_platform_user_id(platform_user_id)


def _database_path() -> str:
    from os import getenv

    from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH

    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH
