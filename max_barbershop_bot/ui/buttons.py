"""Inline buttons for the MAX bot UI."""

from __future__ import annotations

from max_barbershop_bot.core.permissions import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGER,
    can_assign_role,
    can_manage_roles,
    can_view_broadcasts,
    can_view_settings,
    can_view_staff,
    can_view_statistics,
    can_view_yclients,
    normalize_role,
)
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard

MENU_BOOKING_PAYLOAD = "menu:booking"
MENU_MY_BOOKINGS_PAYLOAD = "menu:my_bookings"
MENU_MASTERS_PAYLOAD = "menu:masters"
MENU_CONTACTS_PAYLOAD = "menu:contacts"
MENU_SUPPORT_PAYLOAD = "menu:support"
ADMIN_STAFF_PAYLOAD = "admin:staff"
ADMIN_SETTINGS_PAYLOAD = "admin:settings"
ADMIN_BROADCASTS_PAYLOAD = "admin:broadcasts"
ADMIN_STATISTICS_PAYLOAD = "admin:statistics"
ADMIN_YCLIENTS_PAYLOAD = "admin:yclients"

NAV_BACK_PAYLOAD = "nav:back"
NAV_HOME_PAYLOAD = "nav:home"

BOOKING_BACK_PAYLOAD = "booking:back"
BOOKING_CATEGORY_PAYLOAD_PREFIX = "booking:category:"
BOOKING_SERVICE_PAYLOAD_PREFIX = "booking:service:"
BOOKING_CATEGORY_PREV_PAYLOAD = "booking:category_page:prev"
BOOKING_CATEGORY_NEXT_PAYLOAD = "booking:category_page:next"
BOOKING_SERVICE_PREV_PAYLOAD = "booking:service_page:prev"
BOOKING_SERVICE_NEXT_PAYLOAD = "booking:service_page:next"

STAFF_LIST_PAYLOAD = "staff:list"
STAFF_ASSIGN_START_PAYLOAD = "staff:assign:start"
STAFF_REMOVE_START_PAYLOAD = "staff:remove:start"
STAFF_ASSIGN_MANAGER_PAYLOAD = "staff:assign:role:manager"
STAFF_ASSIGN_ADMIN_PAYLOAD = "staff:assign:role:admin"
STAFF_ASSIGN_DEVELOPER_PAYLOAD = "staff:assign:role:developer"
STAFF_REMOVE_MANAGER_PAYLOAD = "staff:remove:role:manager"
STAFF_REMOVE_ADMIN_PAYLOAD = "staff:remove:role:admin"
STAFF_REMOVE_DEVELOPER_PAYLOAD = "staff:remove:role:developer"

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
        ADMIN_STAFF_PAYLOAD,
        ADMIN_SETTINGS_PAYLOAD,
        ADMIN_BROADCASTS_PAYLOAD,
        ADMIN_STATISTICS_PAYLOAD,
        ADMIN_YCLIENTS_PAYLOAD,
    }
)


def main_menu_keyboard(role: str | None = None) -> MaxInlineKeyboard:
    """Build the main menu inline keyboard for the current role."""

    normalized_role = normalize_role(role)
    rows = [
        [MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)],
        [MaxButton(text="📅 Мои записи", payload=MENU_MY_BOOKINGS_PAYLOAD)],
        [MaxButton(text="👥 Мастера", payload=MENU_MASTERS_PAYLOAD)],
        [MaxButton(text="📍 Контакты", payload=MENU_CONTACTS_PAYLOAD)],
        [MaxButton(text="🆘 Поддержка", payload=MENU_SUPPORT_PAYLOAD)],
    ]
    if can_view_staff(normalized_role):
        rows.append([MaxButton(text="👥 Персонал", payload=ADMIN_STAFF_PAYLOAD)])
    if can_view_settings(normalized_role):
        rows.append([MaxButton(text="⚙️ Настройки", payload=ADMIN_SETTINGS_PAYLOAD)])
    if can_view_broadcasts(normalized_role):
        rows.append([MaxButton(text="📣 Рассылка", payload=ADMIN_BROADCASTS_PAYLOAD)])
    if can_view_statistics(normalized_role):
        rows.append([MaxButton(text="📊 Статистика", payload=ADMIN_STATISTICS_PAYLOAD)])
    if can_view_yclients(normalized_role):
        rows.append([MaxButton(text="🧩 YClients", payload=ADMIN_YCLIENTS_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def navigation_keyboard(*, back_payload: str = NAV_BACK_PAYLOAD) -> MaxInlineKeyboard:
    """Build Back/Home navigation buttons for section screens."""

    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="⬅️ Назад", payload=back_payload),
                MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD),
            ]
        ]
    )


def booking_categories_keyboard(
    categories: list[object],
    *,
    page: int = 0,
    has_previous: bool = False,
    has_next: bool = False,
    back_payload: str = BOOKING_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build MAX-compatible category picker buttons."""

    rows = [
        [MaxButton(text=getattr(category, "title"), payload=f"{BOOKING_CATEGORY_PAYLOAD_PREFIX}{index}")]
        for index, category in enumerate(categories)
    ]
    page_row = []
    if has_previous:
        page_row.append(MaxButton(text="⬅️", payload=BOOKING_CATEGORY_PREV_PAYLOAD))
    if has_next:
        page_row.append(MaxButton(text="➡️", payload=BOOKING_CATEGORY_NEXT_PAYLOAD))
    if page_row:
        rows.append(page_row)
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_services_keyboard(
    services: list[object],
    title_formatter,
    *,
    page: int = 0,
    has_previous: bool = False,
    has_next: bool = False,
    back_payload: str = BOOKING_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build MAX-compatible service picker buttons."""

    rows = [
        [MaxButton(text=title_formatter(service), payload=f"{BOOKING_SERVICE_PAYLOAD_PREFIX}{index}")]
        for index, service in enumerate(services)
    ]
    page_row = []
    if has_previous:
        page_row.append(MaxButton(text="⬅️", payload=BOOKING_SERVICE_PREV_PAYLOAD))
    if has_next:
        page_row.append(MaxButton(text="➡️", payload=BOOKING_SERVICE_NEXT_PAYLOAD))
    if page_row:
        rows.append(page_row)
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def staff_menu_keyboard(role: str | None = None) -> MaxInlineKeyboard:
    """Build staff management menu buttons."""

    rows = [[MaxButton(text="📋 Список сотрудников", payload=STAFF_LIST_PAYLOAD)]]
    if can_manage_roles(normalize_role(role)):
        rows.extend(
            [
                [MaxButton(text="➕ Назначить роль", payload=STAFF_ASSIGN_START_PAYLOAD)],
                [MaxButton(text="➖ Снять роль", payload=STAFF_REMOVE_START_PAYLOAD)],
            ]
        )
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def staff_role_assign_keyboard(role: str | None = None) -> MaxInlineKeyboard:
    """Build role picker for assigning staff roles."""

    normalized_role = normalize_role(role)
    role_payloads = [
        (ROLE_MANAGER, STAFF_ASSIGN_MANAGER_PAYLOAD),
        (ROLE_ADMIN, STAFF_ASSIGN_ADMIN_PAYLOAD),
        (ROLE_DEVELOPER, STAFF_ASSIGN_DEVELOPER_PAYLOAD),
    ]
    rows = [
        [MaxButton(text=target_role, payload=payload)]
        for target_role, payload in role_payloads
        if can_assign_role(normalized_role, target_role)
    ]
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def staff_role_remove_keyboard(roles: list[str]) -> MaxInlineKeyboard:
    """Build role picker for removing staff roles."""

    payloads = {
        ROLE_MANAGER: STAFF_REMOVE_MANAGER_PAYLOAD,
        ROLE_ADMIN: STAFF_REMOVE_ADMIN_PAYLOAD,
        ROLE_DEVELOPER: STAFF_REMOVE_DEVELOPER_PAYLOAD,
    }
    rows = [
        [MaxButton(text=role, payload=payloads[role])]
        for role in roles
        if role in payloads
    ]
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


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
