"""Simple in-memory navigation state for MAX bot users."""

from __future__ import annotations

from dataclasses import dataclass, field

MAIN_MENU_SCREEN = "main_menu"
BOOKING_PLACEHOLDER_SCREEN = "booking_placeholder"
BOOKING_CATEGORIES_SCREEN = "booking_categories"
BOOKING_SERVICES_SCREEN = "booking_services"
BOOKING_SERVICE_SELECTED_SCREEN = "booking_service_selected"
BOOKING_MASTERS_SCREEN = "booking_masters"
BOOKING_MASTER_SELECTED_SCREEN = "booking_master_selected"
BOOKING_DATES_SCREEN = "booking_dates"
BOOKING_SLOTS_SCREEN = "booking_slots"
BOOKING_SLOT_SELECTED_SCREEN = "booking_slot_selected"
BOOKING_CONFIRMATION_SCREEN = "booking_confirmation"
BOOKING_SUCCESS_SCREEN = "booking_success"
MY_BOOKINGS_PLACEHOLDER_SCREEN = "my_bookings_placeholder"
MY_BOOKINGS_SCREEN = "my_bookings"
MY_BOOKINGS_EMPTY_SCREEN = "my_bookings_empty"
MY_BOOKINGS_ERROR_SCREEN = "my_bookings_error"
MY_BOOKING_DETAILS_SCREEN = "my_booking_details"
MY_BOOKING_CANCEL_CONFIRM_SCREEN = "my_booking_cancel_confirm"
MY_BOOKING_CANCEL_SUCCESS_SCREEN = "my_booking_cancel_success"
MY_BOOKING_CANCEL_ERROR_SCREEN = "my_booking_cancel_error"
MY_BOOKING_RESCHEDULE_DATES_SCREEN = "my_booking_reschedule_dates"
MY_BOOKING_RESCHEDULE_SLOTS_SCREEN = "my_booking_reschedule_slots"
MY_BOOKING_RESCHEDULE_CONFIRM_SCREEN = "my_booking_reschedule_confirm"
MY_BOOKING_RESCHEDULE_SUCCESS_SCREEN = "my_booking_reschedule_success"
MY_BOOKING_RESCHEDULE_ERROR_SCREEN = "my_booking_reschedule_error"
MASTERS_PLACEHOLDER_SCREEN = "masters_placeholder"
CONTACTS_PLACEHOLDER_SCREEN = "contacts_placeholder"
CONTACTS_SCREEN = "contacts"
SUPPORT_SCREEN = "support"
STAFF_MENU_SCREEN = "staff_menu"
STAFF_LIST_SCREEN = "staff_list"
STAFF_ASSIGN_IDENTIFIER_SCREEN = "staff_assign_identifier"
STAFF_ASSIGN_ROLE_SCREEN = "staff_assign_role"
STAFF_REMOVE_IDENTIFIER_SCREEN = "staff_remove_identifier"
STAFF_REMOVE_ROLE_SCREEN = "staff_remove_role"
SETTINGS_PLACEHOLDER_SCREEN = "settings_placeholder"
BROADCAST_MENU_SCREEN = "broadcast_menu"
BROADCAST_ONE_TIME_TEXT_SCREEN = "broadcast_one_time_text"
BROADCAST_ONE_TIME_PREVIEW_SCREEN = "broadcast_one_time_preview"
BROADCAST_ONE_TIME_AUDIENCE_SCREEN = "broadcast_one_time_audience"
BROADCAST_ONE_TIME_CONFIRM_SCREEN = "broadcast_one_time_confirm"
BROADCAST_ONE_TIME_SENDING_SCREEN = "broadcast_one_time_sending"
BROADCAST_ONE_TIME_REPORT_SCREEN = "broadcast_one_time_report"
BROADCASTS_PLACEHOLDER_SCREEN = BROADCAST_MENU_SCREEN
STATISTICS_PLACEHOLDER_SCREEN = "statistics_placeholder"
YCLIENTS_PLACEHOLDER_SCREEN = "yclients_placeholder"
NOTIFICATION_HISTORY_SCREEN = "notification_history"
NOTIFICATION_HISTORY_FAILED_SCREEN = "notification_history_failed"
NOTIFICATION_HISTORY_DETAIL_SCREEN = "notification_history_detail"
YCLIENTS_SETTINGS_MENU_SCREEN = "yclients_settings_menu"
YCLIENTS_SETUP_COMPANY_ID_SCREEN = "yclients_setup_company_id"
YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN = "yclients_setup_partner_token"
YCLIENTS_SETUP_USER_TOKEN_SCREEN = "yclients_setup_user_token"
YCLIENTS_SETUP_TIMEZONE_SCREEN = "yclients_setup_timezone"
YCLIENTS_SETUP_BRANCH_TITLE_SCREEN = "yclients_setup_branch_title"
YCLIENTS_SETUP_CONFIRM_SCREEN = "yclients_setup_confirm"

YCLIENTS_SETTINGS_SCREENS = frozenset(
    {
        YCLIENTS_SETTINGS_MENU_SCREEN,
        YCLIENTS_SETUP_COMPANY_ID_SCREEN,
        YCLIENTS_SETUP_PARTNER_TOKEN_SCREEN,
        YCLIENTS_SETUP_USER_TOKEN_SCREEN,
        YCLIENTS_SETUP_TIMEZONE_SCREEN,
        YCLIENTS_SETUP_BRANCH_TITLE_SCREEN,
        YCLIENTS_SETUP_CONFIRM_SCREEN,
    }
)
REGISTRATION_CONSENT_SCREEN = "registration_consent"
REGISTRATION_PHONE_SCREEN = "registration_phone"
REGISTRATION_NAME_SCREEN = "registration_name"

REGISTRATION_SCREENS = frozenset(
    {
        REGISTRATION_CONSENT_SCREEN,
        REGISTRATION_PHONE_SCREEN,
        REGISTRATION_NAME_SCREEN,
    }
)


@dataclass
class UserNavigationState:
    """Current screen and previous screens for one user in one chat."""

    current_screen: str = MAIN_MENU_SCREEN
    screen_stack: list[str] = field(default_factory=list)
    state_data: dict[str, object] = field(default_factory=dict)


_user_states: dict[str, UserNavigationState] = {}


def build_state_key(platform_user_id: str | None, chat_id: str | None) -> str:
    """Build the in-memory state key from platform user id plus chat id."""

    user_part = platform_user_id or "unknown_user"
    chat_part = chat_id or "unknown_chat"
    return f"{user_part}:{chat_part}"


def get_current_screen(platform_user_id: str | None, chat_id: str | None) -> str:
    """Return the current screen for a user chat."""

    state = _get_state(platform_user_id, chat_id)
    return state.current_screen


def set_current_screen(platform_user_id: str | None, chat_id: str | None, screen_id: str) -> None:
    """Save the current screen for a user chat."""

    state = _get_state(platform_user_id, chat_id)
    state.current_screen = screen_id


def push_screen(platform_user_id: str | None, chat_id: str | None, screen_id: str) -> None:
    """Push a screen into the back navigation stack."""

    state = _get_state(platform_user_id, chat_id)
    state.screen_stack.append(screen_id)


def find_chat_id_for_current_screen(platform_user_id: str | None, screen_id: str) -> str | None:
    """Find a chat id where the user currently has the given screen."""

    if platform_user_id is None:
        return None

    user_prefix = f"{platform_user_id}:"
    for state_key, navigation_state in _user_states.items():
        if state_key.startswith(user_prefix) and navigation_state.current_screen == screen_id:
            return state_key[len(user_prefix) :]
    return None


def pop_previous_screen(platform_user_id: str | None, chat_id: str | None) -> str | None:
    """Pop and return the previous screen, or None when the stack is empty."""

    state = _get_state(platform_user_id, chat_id)
    if not state.screen_stack:
        state.current_screen = MAIN_MENU_SCREEN
        return None

    previous_screen = state.screen_stack.pop()
    state.current_screen = previous_screen
    return previous_screen


def set_state_data_value(
    platform_user_id: str | None,
    chat_id: str | None,
    key: str,
    value: object,
) -> None:
    """Store temporary in-memory data for one user chat."""

    state = _get_state(platform_user_id, chat_id)
    state.state_data[key] = value


def get_state_data_value(
    platform_user_id: str | None,
    chat_id: str | None,
    key: str,
) -> object | None:
    """Read temporary in-memory data for one user chat."""

    state = _get_state(platform_user_id, chat_id)
    return state.state_data.get(key)


def clear_state_data(platform_user_id: str | None, chat_id: str | None) -> None:
    """Clear temporary in-memory data for one user chat."""

    state = _get_state(platform_user_id, chat_id)
    state.state_data.clear()


def reset_to_home(platform_user_id: str | None, chat_id: str | None) -> None:
    """Reset navigation to the main menu and clear the back stack."""

    state = _get_state(platform_user_id, chat_id)
    state.current_screen = MAIN_MENU_SCREEN
    state.screen_stack.clear()
    state.state_data.clear()


def clear_user_state(platform_user_id: str | None, chat_id: str | None) -> None:
    """Remove the saved state for a user chat if it exists."""

    _user_states.pop(build_state_key(platform_user_id, chat_id), None)


def _get_state(platform_user_id: str | None, chat_id: str | None) -> UserNavigationState:
    key = build_state_key(platform_user_id, chat_id)
    if key not in _user_states:
        _user_states[key] = UserNavigationState()
    return _user_states[key]
