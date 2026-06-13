"""Inline buttons for the MAX bot UI."""

from __future__ import annotations

from max_barbershop_bot.core.permissions import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGER,
    can_assign_role,
    can_manage_roles,
    can_view_broadcasts,
    can_view_contacts_settings,
    can_view_diagnostics_settings,
    can_view_notification_settings,
    can_view_settings,
    can_view_staff,
    can_view_statistics,
    can_view_yclients,
    can_view_yclients_settings,
    normalize_role,
)
from max_barbershop_bot.core.payloads import indexed_payload
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
ADMIN_NOTIFICATION_HISTORY_PAYLOAD = "admin:notification_history"

SETTINGS_YCLIENTS_PAYLOAD = "settings:yclients"
SETTINGS_CONTACTS_PAYLOAD = "settings:contacts"
SETTINGS_NOTIFICATIONS_PAYLOAD = "settings:notifications"
SETTINGS_MASTER_PHOTOS_PAYLOAD = "settings:master_photos"
SETTINGS_ROLES_PAYLOAD = "settings:roles"
SETTINGS_DIAGNOSTICS_PAYLOAD = "settings:diagnostics"
SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD = "settings:diagnostics:notification_history"
SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD = "settings:diagnostics:yclients_check"
SETTINGS_BACK_PAYLOAD = "settings:back"
SETTINGS_HOME_PAYLOAD = "settings:home"
SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD = "settings:contacts:address"
SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD = "settings:contacts:phone"
SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD = "settings:contacts:schedule"
SETTINGS_CONTACTS_RESET_PAYLOAD = "settings:contacts:reset"
SETTINGS_CONTACTS_PREVIEW_PAYLOAD = "settings:contacts:preview"
SETTINGS_SUPPORT_PAYLOAD = "settings:support"
SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD = "settings:support:username"
SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD = "settings:support:description"
SETTINGS_SUPPORT_PREVIEW_PAYLOAD = "settings:support:preview"
MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX = "settings:mp:select:"
MASTER_PHOTOS_UPLOAD_PAYLOAD = "settings:mp:upload"
MASTER_PHOTOS_DELETE_PAYLOAD = "settings:mp:delete"
MASTER_PHOTOS_DELETE_CONFIRM_PAYLOAD = "settings:mp:delete:confirm"
MASTER_PHOTOS_BACK_PAYLOAD = "settings:mp:back"
MASTER_PHOTOS_HOME_PAYLOAD = "settings:mp:home"

STATISTICS_TODAY_PAYLOAD = "stats:period:today"
STATISTICS_7_DAYS_PAYLOAD = "stats:period:7"
STATISTICS_30_DAYS_PAYLOAD = "stats:period:30"
STATISTICS_90_DAYS_PAYLOAD = "stats:period:90"
STATISTICS_BACK_PAYLOAD = "stats:back"
STATISTICS_HOME_PAYLOAD = "stats:home"

NOTIFICATION_HISTORY_FAILED_PAYLOAD = "notification_history:failed"
NOTIFICATION_HISTORY_REFRESH_PAYLOAD = "notification_history:refresh"
NOTIFICATION_HISTORY_BACK_PAYLOAD = "notification_history:back"
NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX = "notification_history:detail:"

BROADCAST_ONE_TIME_START_PAYLOAD = "broadcast:one_time:start"
BROADCAST_PREVIEW_NEXT_PAYLOAD = "broadcast:preview:next"
BROADCAST_PREVIEW_EDIT_PAYLOAD = "broadcast:preview:edit"
BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD = "broadcast:audience:all_users"
BROADCAST_SEGMENTS_PAYLOAD = "broadcast:segments"
SEGMENTS_ACTIVE_7_PAYLOAD = "segments:active:7"
SEGMENTS_ACTIVE_30_PAYLOAD = "segments:active:30"
SEGMENTS_ACTIVE_90_PAYLOAD = "segments:active:90"
SEGMENTS_LOST_PAYLOAD = "segments:lost"
LOST_CLIENTS_OPEN_PAYLOAD = "lost_clients:open"
LOST_CLIENTS_REFRESH_PAYLOAD = "lost_clients:refresh"
LOST_CLIENTS_BROADCAST_PAYLOAD = "lost_clients:broadcast"
LOST_CLIENTS_BACK_PAYLOAD = "lost_clients:back"
LOST_CLIENTS_HOME_PAYLOAD = "lost_clients:home"
SEGMENTS_NO_FUTURE_BOOKINGS_PAYLOAD = "segments:no_future_bookings"
SEGMENTS_REFRESH_PAYLOAD = "segments:refresh"
SEGMENTS_BROADCAST_PAYLOAD = "segments:broadcast"
SEGMENTS_BACK_PAYLOAD = "segments:back"
SEGMENTS_HOME_PAYLOAD = "segments:home"
BROADCAST_CONFIRM_SEND_PAYLOAD = "broadcast:confirm:send"
BROADCAST_NEW_PAYLOAD = "broadcast:new"
BROADCAST_BACK_PAYLOAD = "broadcast:back"
BROADCAST_HOME_PAYLOAD = "broadcast:home"

NAV_BACK_PAYLOAD = "nav:back"
NAV_HOME_PAYLOAD = "nav:home"

YCLIENTS_SETUP_PAYLOAD = "yclients:setup"
YCLIENTS_CHECK_PAYLOAD = "yclients:check"
YCLIENTS_SAVE_PAYLOAD = "yclients:save"
YCLIENTS_SKIP_BRANCH_TITLE_PAYLOAD = "yclients:branch_title:skip"
YCLIENTS_BACK_PAYLOAD = "yclients:back"
YCLIENTS_HOME_PAYLOAD = "yclients:home"

BOOKING_BACK_PAYLOAD = "booking:back"
BOOKING_HUB_SERVICE_PAYLOAD = "booking:hub:service"
BOOKING_HUB_STAFF_PAYLOAD = "booking:hub:staff"
BOOKING_HUB_DATETIME_PAYLOAD = "booking:hub:datetime"
BOOKING_CATEGORY_PAYLOAD_PREFIX = "booking:cat:"
BOOKING_SERVICE_PAYLOAD_PREFIX = "booking:svc:"
BOOKING_CATEGORY_PREV_PAYLOAD = "booking:category_page:prev"
BOOKING_CATEGORY_NEXT_PAYLOAD = "booking:category_page:next"
BOOKING_SERVICE_PREV_PAYLOAD = "booking:service_page:prev"
BOOKING_SERVICE_NEXT_PAYLOAD = "booking:service_page:next"
BOOKING_MASTER_PAYLOAD_PREFIX = "booking:master:"
BOOKING_MASTER_PREV_PAYLOAD = "booking:master_page:prev"
BOOKING_MASTER_NEXT_PAYLOAD = "booking:master_page:next"
BOOKING_DATE_PAYLOAD_PREFIX = "booking:date:"
BOOKING_SLOT_PAYLOAD_PREFIX = "booking:slot:"
BOOKING_CONFIRM_PAYLOAD = "booking:confirm"
BOOKING_CANCEL_DRAFT_PAYLOAD = "booking:cancel_draft"
BOOKING_PHONE_USE_REGISTERED_PAYLOAD = "booking:phone:use_registered"

MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX = "my_bookings:details:"
MY_BOOKINGS_CANCEL_START_PAYLOAD = "my_bookings:cancel:start"
MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD = "my_bookings:cancel:confirm"
MY_BOOKINGS_RESCHEDULE_START_PAYLOAD = "my_bookings:reschedule:start"
MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD = "my_bookings:reschedule:confirm"
MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX = "my_bookings:reschedule:date:"
MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX = "my_bookings:reschedule:slot:"
MY_BOOKINGS_BACK_PAYLOAD = "my_bookings:back"

STAFF_LIST_PAYLOAD = "staff:list"
STAFF_ASSIGN_START_PAYLOAD = "staff:assign:start"
STAFF_REMOVE_START_PAYLOAD = "staff:remove:start"
STAFF_ASSIGN_MANAGER_PAYLOAD = "staff:assign:role:manager"
STAFF_ASSIGN_ADMIN_PAYLOAD = "staff:assign:role:admin"
STAFF_ASSIGN_DEVELOPER_PAYLOAD = "staff:assign:role:developer"
STAFF_REMOVE_MANAGER_PAYLOAD = "staff:remove:role:manager"
STAFF_REMOVE_ADMIN_PAYLOAD = "staff:remove:role:admin"
STAFF_REMOVE_DEVELOPER_PAYLOAD = "staff:remove:role:developer"

REGISTRATION_OPEN_PRIVACY_PAYLOAD = "registration:policy:open:privacy"
REGISTRATION_OPEN_PERSONAL_PAYLOAD = "registration:policy:open:personal"
REGISTRATION_TOGGLE_PRIVACY_PAYLOAD = "registration:policy:toggle:privacy"
REGISTRATION_TOGGLE_PERSONAL_PAYLOAD = "registration:policy:toggle:personal"
REGISTRATION_CONTINUE_PAYLOAD = "registration:policy:continue"
REGISTRATION_NAME_YES_PAYLOAD = "registration:name:yes"
REGISTRATION_NAME_NO_PAYLOAD = "registration:name:no"
REGISTRATION_BACK_PAYLOAD = "registration:nav:back"
REGISTRATION_HOME_PAYLOAD = "registration:nav:home"

MENU_PAYLOADS = frozenset(
    {
        MENU_BOOKING_PAYLOAD,
        MENU_MY_BOOKINGS_PAYLOAD,
        MENU_CONTACTS_PAYLOAD,
        MENU_SUPPORT_PAYLOAD,
        ADMIN_STAFF_PAYLOAD,
        ADMIN_SETTINGS_PAYLOAD,
        ADMIN_BROADCASTS_PAYLOAD,
        ADMIN_STATISTICS_PAYLOAD,
        ADMIN_YCLIENTS_PAYLOAD,
        ADMIN_NOTIFICATION_HISTORY_PAYLOAD,
    }
)


def main_menu_keyboard(role: str | None = None) -> MaxInlineKeyboard:
    """Build the main menu inline keyboard for the current role."""

    normalized_role = normalize_role(role)
    rows = [
        [MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)],
        [MaxButton(text="📅 Мои записи", payload=MENU_MY_BOOKINGS_PAYLOAD)],
        [MaxButton(text="📍 Контакты", payload=MENU_CONTACTS_PAYLOAD)],
        [MaxButton(text="🆘 Поддержка", payload=MENU_SUPPORT_PAYLOAD)],
    ]
    if can_view_statistics(normalized_role):
        rows.append([MaxButton(text="📊 Статистика", payload=ADMIN_STATISTICS_PAYLOAD)])
    if can_view_staff(normalized_role):
        rows.append([MaxButton(text="👥 Персонал", payload=ADMIN_STAFF_PAYLOAD)])
    if can_view_settings(normalized_role):
        rows.append([MaxButton(text="⚙️ Настройки", payload=ADMIN_SETTINGS_PAYLOAD)])
    if can_view_broadcasts(normalized_role):
        rows.append([MaxButton(text="📣 Рассылка", payload=ADMIN_BROADCASTS_PAYLOAD)])
    if can_view_yclients(normalized_role):
        rows.append([MaxButton(text="🧩 YClients", payload=ADMIN_YCLIENTS_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)



def settings_menu_keyboard(role: str | None = None) -> MaxInlineKeyboard:
    """Build settings hub buttons for the current role."""

    normalized_role = normalize_role(role)
    rows: list[list[MaxButton]] = []
    if can_view_yclients_settings(normalized_role):
        rows.append([MaxButton(text="🧩 YClients", payload=SETTINGS_YCLIENTS_PAYLOAD)])
    if can_view_contacts_settings(normalized_role):
        rows.append([MaxButton(text="🖼️ Редактировать фото мастеров", payload=SETTINGS_MASTER_PHOTOS_PAYLOAD)])
        rows.append([MaxButton(text="✏️ Редактировать контакты", payload=SETTINGS_CONTACTS_PAYLOAD)])
        rows.append([MaxButton(text="🆘 Редактировать поддержку", payload=SETTINGS_SUPPORT_PAYLOAD)])
    if can_view_notification_settings(normalized_role):
        rows.append([MaxButton(text="🔔 Уведомления", payload=SETTINGS_NOTIFICATIONS_PAYLOAD)])
    if can_manage_roles(normalized_role):
        rows.append([MaxButton(text="👥 Роли", payload=SETTINGS_ROLES_PAYLOAD)])
    if can_view_diagnostics_settings(normalized_role):
        rows.append([MaxButton(text="🛠 Диагностика", payload=SETTINGS_DIAGNOSTICS_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)



def master_photos_list_keyboard(masters: list[object]) -> MaxInlineKeyboard:
    """Build master photo settings master list."""

    rows = [
        [
            MaxButton(
                text=_master_photo_button_text(master),
                payload=indexed_payload(MASTER_PHOTOS_SELECT_PAYLOAD_PREFIX, index),
            )
        ]
        for index, master in enumerate(masters[:20])
    ]
    rows.append([MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)



def _master_photo_button_text(master: object) -> str:
    name = str(getattr(master, "name", "—") or "—").strip() or "—"
    specialization = str(getattr(master, "specialization", "") or "").strip()
    status = "✅" if getattr(master, "has_photo", False) else "—"
    title = f"{name} · {specialization}" if specialization else name
    return f"{title} {status}"

def master_photo_detail_keyboard(*, has_photo: bool) -> MaxInlineKeyboard:
    """Build actions for one master photo card."""

    upload_text = "📤 Загрузить / заменить фото"
    rows = [[MaxButton(text=upload_text, payload=MASTER_PHOTOS_UPLOAD_PAYLOAD)]]
    if has_photo:
        rows.append([MaxButton(text="🗑️ Удалить фото", payload=MASTER_PHOTOS_DELETE_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=MASTER_PHOTOS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=MASTER_PHOTOS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def master_photo_wait_keyboard() -> MaxInlineKeyboard:
    """Build navigation while waiting for a master photo upload."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=MASTER_PHOTOS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=MASTER_PHOTOS_HOME_PAYLOAD)],
        ]
    )


def master_photo_delete_confirm_keyboard() -> MaxInlineKeyboard:
    """Build master photo deletion confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Удалить", payload=MASTER_PHOTOS_DELETE_CONFIRM_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=MASTER_PHOTOS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=MASTER_PHOTOS_HOME_PAYLOAD)],
        ]
    )

def settings_status_keyboard(*, include_contacts: bool = False) -> MaxInlineKeyboard:
    """Build settings subsection navigation buttons."""

    rows: list[list[MaxButton]] = []
    if include_contacts:
        rows.append([MaxButton(text="📍 Открыть контакты", payload=MENU_CONTACTS_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def settings_contacts_keyboard() -> MaxInlineKeyboard:
    """Build contacts override editor buttons from Telegram reference UX."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🏠 Изменить адрес", payload=SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD)],
            [MaxButton(text="📞 Изменить телефон", payload=SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD)],
            [MaxButton(text="⏰ Изменить режим работы", payload=SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD)],
            [MaxButton(text="♻️ Сбросить к данным YClients", payload=SETTINGS_CONTACTS_RESET_PAYLOAD)],
            [MaxButton(text="👁️ Предпросмотр", payload=SETTINGS_CONTACTS_PREVIEW_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def support_screen_keyboard(*, support_url: str | None) -> MaxInlineKeyboard:
    """Build public support keyboard with Telegram-style action and navigation."""

    rows: list[list[MaxButton]] = []
    if support_url:
        rows.append([MaxButton(text="🆘 Написать в поддержку", type="link", url=support_url)])
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def settings_support_keyboard() -> MaxInlineKeyboard:
    """Build support settings editor buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="👤 Изменить username", payload=SETTINGS_SUPPORT_EDIT_USERNAME_PAYLOAD)],
            [MaxButton(text="📝 Изменить текст", payload=SETTINGS_SUPPORT_EDIT_DESCRIPTION_PAYLOAD)],
            [MaxButton(text="👁️ Предпросмотр", payload=SETTINGS_SUPPORT_PREVIEW_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def settings_support_input_keyboard() -> MaxInlineKeyboard:
    """Build Back/Home navigation while waiting for support settings text input."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def settings_contacts_input_keyboard() -> MaxInlineKeyboard:
    """Build Back/Home navigation while waiting for contacts text input."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def settings_notifications_keyboard() -> MaxInlineKeyboard:
    """Build notification settings buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔔 История уведомлений", payload=SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def settings_diagnostics_keyboard() -> MaxInlineKeyboard:
    """Build diagnostics settings buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔔 История уведомлений", payload=SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD)],
            [MaxButton(text="🧩 Проверить YClients", payload=SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def statistics_period_keyboard() -> MaxInlineKeyboard:
    """Build statistics period selection buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="Сегодня", payload=STATISTICS_TODAY_PAYLOAD),
                MaxButton(text="7 дней", payload=STATISTICS_7_DAYS_PAYLOAD),
            ],
            [
                MaxButton(text="30 дней", payload=STATISTICS_30_DAYS_PAYLOAD),
                MaxButton(text="90 дней", payload=STATISTICS_90_DAYS_PAYLOAD),
            ],
            [MaxButton(text="⬅️ Назад", payload=STATISTICS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=STATISTICS_HOME_PAYLOAD)],
        ]
    )


def statistics_result_keyboard() -> MaxInlineKeyboard:
    """Build statistics result navigation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=STATISTICS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=STATISTICS_HOME_PAYLOAD)],
        ]
    )


def notification_history_keyboard(
    records: list[object],
    *,
    failed: bool = False,
    back_payload: str = NOTIFICATION_HISTORY_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build notification history diagnostics buttons."""

    rows: list[list[MaxButton]] = []
    for index, record in enumerate(records[:20]):
        rows.append(
            [
                MaxButton(
                    text=f"#{getattr(record, 'id')}",
                    payload=indexed_payload(NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX, index),
                )
            ]
        )
    if failed:
        rows.append([MaxButton(text="🔄 Обновить", payload=NOTIFICATION_HISTORY_FAILED_PAYLOAD)])
    else:
        rows.append([MaxButton(text="❌ Ошибки", payload=NOTIFICATION_HISTORY_FAILED_PAYLOAD)])
        rows.append([MaxButton(text="🔄 Обновить", payload=NOTIFICATION_HISTORY_REFRESH_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def notification_history_detail_keyboard() -> MaxInlineKeyboard:
    """Build notification history detail navigation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=NOTIFICATION_HISTORY_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def yclients_settings_keyboard() -> MaxInlineKeyboard:
    """Build YClients integration settings menu buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⚙️ Настроить подключение", payload=YCLIENTS_SETUP_PAYLOAD)],
            [MaxButton(text="🔍 Проверить подключение", payload=YCLIENTS_CHECK_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=YCLIENTS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=YCLIENTS_HOME_PAYLOAD)],
        ]
    )


def yclients_setup_navigation_keyboard(*, include_skip: bool = False) -> MaxInlineKeyboard:
    """Build YClients setup wizard navigation buttons."""

    rows: list[list[MaxButton]] = []
    if include_skip:
        rows.append([MaxButton(text="⏭️ Пропустить", payload=YCLIENTS_SKIP_BRANCH_TITLE_PAYLOAD)])
    rows.append(
        [
            MaxButton(text="⬅️ Назад", payload=YCLIENTS_BACK_PAYLOAD),
            MaxButton(text="🏠 Главное меню", payload=YCLIENTS_HOME_PAYLOAD),
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def yclients_confirm_keyboard() -> MaxInlineKeyboard:
    """Build YClients setup confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Сохранить", payload=YCLIENTS_SAVE_PAYLOAD)],
            [
                MaxButton(text="⬅️ Назад", payload=YCLIENTS_BACK_PAYLOAD),
                MaxButton(text="🏠 Главное меню", payload=YCLIENTS_HOME_PAYLOAD),
            ],
        ]
    )


def broadcast_menu_keyboard() -> MaxInlineKeyboard:
    """Build one-time broadcast menu buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✉️ Разовая рассылка", payload=BROADCAST_ONE_TIME_START_PAYLOAD)],
            [MaxButton(text="🎯 Сегменты клиентов", payload=BROADCAST_SEGMENTS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def client_segments_menu_keyboard() -> MaxInlineKeyboard:
    """Build client segments selection buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔥 Активные за 7 дней", payload=SEGMENTS_ACTIVE_7_PAYLOAD)],
            [MaxButton(text="📆 Активные за 30 дней", payload=SEGMENTS_ACTIVE_30_PAYLOAD)],
            [MaxButton(text="🗓 Активные за 90 дней", payload=SEGMENTS_ACTIVE_90_PAYLOAD)],
            [MaxButton(text="😔 Потерянные клиенты", payload=LOST_CLIENTS_OPEN_PAYLOAD)],
            [MaxButton(text="📭 Без будущих записей", payload=SEGMENTS_NO_FUTURE_BOOKINGS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SEGMENTS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SEGMENTS_HOME_PAYLOAD)],
        ]
    )


def lost_clients_result_keyboard(*, can_broadcast: bool = False) -> MaxInlineKeyboard:
    """Build buttons for the dedicated lost clients screen."""

    rows: list[list[MaxButton]] = []
    if can_broadcast:
        rows.append([MaxButton(text="📣 Запустить рассылку", payload=LOST_CLIENTS_BROADCAST_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="🔄 Обновить", payload=LOST_CLIENTS_REFRESH_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=LOST_CLIENTS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=LOST_CLIENTS_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def client_segment_result_keyboard(*, can_broadcast: bool = False) -> MaxInlineKeyboard:
    """Build buttons for a calculated client segment."""

    rows: list[list[MaxButton]] = []
    if can_broadcast:
        rows.append([MaxButton(text="📣 Сделать рассылку по сегменту", payload=SEGMENTS_BROADCAST_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="🔄 Обновить", payload=SEGMENTS_REFRESH_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SEGMENTS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SEGMENTS_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def broadcast_text_keyboard() -> MaxInlineKeyboard:
    """Build navigation buttons for broadcast text input."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def broadcast_preview_keyboard() -> MaxInlineKeyboard:
    """Build broadcast preview action buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Далее", payload=BROADCAST_PREVIEW_NEXT_PAYLOAD)],
            [MaxButton(text="✏️ Изменить текст", payload=BROADCAST_PREVIEW_EDIT_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def broadcast_audience_keyboard() -> MaxInlineKeyboard:
    """Build one-time broadcast audience buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="👥 Все пользователи", payload=BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def broadcast_confirm_keyboard(*, can_send: bool = True) -> MaxInlineKeyboard:
    """Build final broadcast confirmation buttons."""

    rows: list[list[MaxButton]] = []
    if can_send:
        rows.append([MaxButton(text="🚀 Отправить", payload=BROADCAST_CONFIRM_SEND_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="✏️ Изменить текст", payload=BROADCAST_PREVIEW_EDIT_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def broadcast_report_keyboard() -> MaxInlineKeyboard:
    """Build final broadcast report buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📣 Новая рассылка", payload=BROADCAST_NEW_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def stale_screen_keyboard() -> MaxInlineKeyboard:
    """Build a safe return button for stale or unknown callback screens."""

    return MaxInlineKeyboard.from_rows([[MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)]])


def booking_stale_keyboard() -> MaxInlineKeyboard:
    """Build safe restart buttons for stale booking callbacks."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


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


def booking_hub_keyboard(*, back_payload: str = BOOKING_BACK_PAYLOAD) -> MaxInlineKeyboard:
    """Build booking entry-mode picker in the Telegram reference order."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="👨‍🔧 Выбрать специалиста", payload=BOOKING_HUB_STAFF_PAYLOAD)],
            [MaxButton(text="📅 Выбрать дату и время", payload=BOOKING_HUB_DATETIME_PAYLOAD)],
            [MaxButton(text="🧾 Выбрать услуги", payload=BOOKING_HUB_SERVICE_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=back_payload)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
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
        [
            MaxButton(
                text=getattr(category, "title"),
                payload=indexed_payload(BOOKING_CATEGORY_PAYLOAD_PREFIX, index),
            )
        ]
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
        [
            MaxButton(
                text=title_formatter(service),
                payload=indexed_payload(BOOKING_SERVICE_PAYLOAD_PREFIX, index),
            )
        ]
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


def booking_masters_keyboard(
    masters: list[object],
    title_formatter,
    *,
    page: int = 0,
    has_previous: bool = False,
    has_next: bool = False,
    back_payload: str = BOOKING_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build MAX-compatible master picker buttons."""

    rows = [
        [
            MaxButton(
                text=title_formatter(master),
                payload=indexed_payload(BOOKING_MASTER_PAYLOAD_PREFIX, index),
            )
        ]
        for index, master in enumerate(masters)
    ]
    page_row = []
    if has_previous:
        page_row.append(MaxButton(text="⬅️", payload=BOOKING_MASTER_PREV_PAYLOAD))
    if has_next:
        page_row.append(MaxButton(text="➡️", payload=BOOKING_MASTER_NEXT_PAYLOAD))
    if page_row:
        rows.append(page_row)
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_dates_keyboard(
    dates: list[object],
    title_formatter,
    *,
    back_payload: str = BOOKING_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build MAX-compatible date picker buttons."""

    rows: list[list[MaxButton]] = []
    date_buttons = [
        MaxButton(
            text=title_formatter(value),
            payload=indexed_payload(BOOKING_DATE_PAYLOAD_PREFIX, index),
        )
        for index, value in enumerate(dates)
    ]
    for index in range(0, len(date_buttons), 2):
        rows.append(date_buttons[index : index + 2])
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_slots_keyboard(
    slots: list[object],
    title_formatter,
    *,
    back_payload: str = BOOKING_BACK_PAYLOAD,
) -> MaxInlineKeyboard:
    """Build MAX-compatible slot picker buttons."""

    rows: list[list[MaxButton]] = []
    slot_buttons = [
        MaxButton(
            text=title_formatter(value),
            payload=indexed_payload(BOOKING_SLOT_PAYLOAD_PREFIX, index),
        )
        for index, value in enumerate(slots)
    ]
    for index in range(0, len(slot_buttons), 3):
        rows.append(slot_buttons[index : index + 3])
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_phone_keyboard(*, include_registered_phone: bool, back_payload: str = BOOKING_BACK_PAYLOAD) -> MaxInlineKeyboard:
    """Build booking phone-step buttons in the reference UX order."""

    rows: list[list[MaxButton]] = []
    if include_registered_phone:
        rows.append([MaxButton(text="📱 Использовать номер из регистрации", payload=BOOKING_PHONE_USE_REGISTERED_PAYLOAD)])
    rows.append([MaxButton(text="📞 Поделиться контактом", type="request_contact")])
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_confirmation_keyboard(*, back_payload: str = BOOKING_BACK_PAYLOAD) -> MaxInlineKeyboard:
    """Build final booking confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Подтвердить запись", payload=BOOKING_CONFIRM_PAYLOAD)],
            [MaxButton(text="❌ Отменить", payload=BOOKING_CANCEL_DRAFT_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=back_payload)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def booking_success_keyboard() -> MaxInlineKeyboard:
    """Build booking success navigation buttons."""

    return MaxInlineKeyboard.from_rows([[MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)]])


def my_bookings_keyboard(*, include_booking: bool = False) -> MaxInlineKeyboard:
    """Build My bookings navigation buttons."""

    rows: list[list[MaxButton]] = []
    if include_booking:
        rows.append([MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_bookings_list_keyboard(bookings_count: int, *, max_buttons: int = 20) -> MaxInlineKeyboard:
    """Build future booking selection buttons with short MAX payloads."""

    rows: list[list[MaxButton]] = [
        [
            MaxButton(
                text=f"📋 Запись {index + 1}",
                payload=indexed_payload(MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX, index),
            )
        ]
        for index in range(min(max(bookings_count, 0), max_buttons))
    ]
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_booking_details_keyboard() -> MaxInlineKeyboard:
    """Build selected booking actions."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔁 Перенести запись", payload=MY_BOOKINGS_RESCHEDULE_START_PAYLOAD)],
            [MaxButton(text="❌ Отменить запись", payload=MY_BOOKINGS_CANCEL_START_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def my_booking_cancel_confirmation_keyboard() -> MaxInlineKeyboard:
    """Build cancellation confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Да, отменить", payload=MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def my_booking_cancel_result_keyboard() -> MaxInlineKeyboard:
    """Build buttons shown after cancellation result."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📅 Мои записи", payload=MENU_MY_BOOKINGS_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )



def my_booking_reschedule_dates_keyboard(dates: list[object], title_formatter) -> MaxInlineKeyboard:
    """Build date picker buttons for selected booking reschedule."""

    rows: list[list[MaxButton]] = []
    buttons = [
        MaxButton(
            text=title_formatter(value),
            payload=indexed_payload(MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX, index),
        )
        for index, value in enumerate(dates)
    ]
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index : index + 2])
    rows.append([MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_booking_reschedule_slots_keyboard(slots: list[object], title_formatter) -> MaxInlineKeyboard:
    """Build slot picker buttons for selected booking reschedule."""

    rows: list[list[MaxButton]] = []
    buttons = [
        MaxButton(
            text=title_formatter(value),
            payload=indexed_payload(MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX, index),
        )
        for index, value in enumerate(slots)
    ]
    for index in range(0, len(buttons), 3):
        rows.append(buttons[index : index + 3])
    rows.append([MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_booking_reschedule_confirmation_keyboard() -> MaxInlineKeyboard:
    """Build reschedule confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Подтвердить перенос", payload=MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def my_booking_reschedule_result_keyboard() -> MaxInlineKeyboard:
    """Build buttons shown after reschedule result."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📅 Мои записи", payload=MENU_MY_BOOKINGS_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )

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


def registration_consent_keyboard(*, privacy_accepted: bool = False, personal_accepted: bool = False) -> MaxInlineKeyboard:
    """Build policy acceptance buttons for the registration start screen."""

    privacy_label = (
        "✅ Принять политику конфиденциальности"
        if privacy_accepted
        else "⬜ Принять политику конфиденциальности"
    )
    personal_label = (
        "✅ Принять политику обработки персональных данных"
        if personal_accepted
        else "⬜ Принять политику обработки персональных данных"
    )
    rows = [
        [MaxButton(text="🔐 Политика конфиденциальности", payload=REGISTRATION_OPEN_PRIVACY_PAYLOAD)],
        [MaxButton(text="🔐 Политика обработки персональных данных", payload=REGISTRATION_OPEN_PERSONAL_PAYLOAD)],
        [MaxButton(text=privacy_label, payload=REGISTRATION_TOGGLE_PRIVACY_PAYLOAD)],
        [MaxButton(text=personal_label, payload=REGISTRATION_TOGGLE_PERSONAL_PAYLOAD)],
    ]
    if privacy_accepted and personal_accepted:
        rows.append([MaxButton(text="Перейти к регистрации.", payload=REGISTRATION_CONTINUE_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=REGISTRATION_HOME_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=REGISTRATION_BACK_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def registration_name_confirmation_keyboard() -> MaxInlineKeyboard:
    """Build Telegram-style name confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Да", payload=REGISTRATION_NAME_YES_PAYLOAD)],
            [MaxButton(text="❌ Нет", payload=REGISTRATION_NAME_NO_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=REGISTRATION_BACK_PAYLOAD)],
        ]
    )


def registration_phone_keyboard() -> MaxInlineKeyboard:
    """Build phone step buttons with contact request and safe navigation."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📞 Поделиться контактом", type="request_contact")],
            [MaxButton(text="⬅️ Назад", payload=REGISTRATION_BACK_PAYLOAD)],
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
