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
REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX = "repeat_visit:book:"
REPEAT_VISIT_BOOKING_BUTTON_TEXT = "✂️ Записаться"
MENU_MY_BOOKINGS_PAYLOAD = "menu:my_bookings"
MENU_MASTERS_PAYLOAD = "menu:masters"
MENU_CONTACTS_PAYLOAD = "menu:contacts"
MENU_SUPPORT_PAYLOAD = "menu:support"
ADMIN_STAFF_PAYLOAD = "admin:staff"
ADMIN_SETTINGS_PAYLOAD = "admin:settings"
ADMIN_BROADCASTS_PAYLOAD = "admin:broadcasts"
ADMIN_STATISTICS_PAYLOAD = "admin:statistics"
ADMIN_BOOKINGS_OPEN_PAYLOAD = "admbook:open"
ADMIN_YCLIENTS_PAYLOAD = "admin:yclients"
ADMIN_NOTIFICATION_HISTORY_PAYLOAD = "admin:notification_history"
ADMIN_CLIENTS_DIRECTORY_PAYLOAD = "admin:clients_directory"

SETTINGS_YCLIENTS_PAYLOAD = "settings:yclients"
SETTINGS_CONTACTS_PAYLOAD = "settings:contacts"
SETTINGS_NOTIFICATIONS_PAYLOAD = "settings:notifications"
SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD = "settings:notifications:enable"
SETTINGS_NOTIFICATIONS_DISABLE_PAYLOAD = "settings:notifications:disable"
SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD = "settings:notifications:smoke"
SETTINGS_NOTIFICATIONS_TESTS_PAYLOAD = "settings:notifications:tests"
SETTINGS_NOTIFICATIONS_TEST_IMMEDIATE_PAYLOAD = "settings:notifications:test:immediate"
SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD = "settings:notifications:test:48h"
SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD = "settings:notifications:test:2h"
SETTINGS_AUTOMATION_ROOT_PAYLOAD = "settings:automation"
SETTINGS_AUTOMATION_MODULE_PREFIX = "set:auto:mod:"
SETTINGS_AUTOMATION_TOGGLE_PREFIX = "set:auto:t:"
SETTINGS_AUTOMATION_EDIT_PREFIX = "set:auto:e:"
SETTINGS_MASTER_PHOTOS_PAYLOAD = "settings:master_photos"
SETTINGS_ROLES_PAYLOAD = "settings:roles"
SETTINGS_DIAGNOSTICS_PAYLOAD = "settings:diagnostics"
SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD = "settings:diagnostics:notification_history"
SETTINGS_DIAGNOSTICS_YCLIENTS_CHECK_PAYLOAD = "settings:diagnostics:yclients_check"
DEV_DIAGNOSTICS_REFRESH_PAYLOAD = "devdiag:refresh"
DEV_DIAGNOSTICS_FAILED_NOTIFICATIONS_PAYLOAD = "devdiag:notif_failed"
DEV_DIAGNOSTICS_BOT_LOGS_PAYLOAD = "devdiag:bot_logs"
DEV_DIAGNOSTICS_BOT_LOGS_CSV_PAYLOAD = "devdiag:bot_logs_csv"
DEV_DIAGNOSTICS_LOGS_PREV_PAYLOAD = "devdiag:logs:prev"
DEV_DIAGNOSTICS_LOGS_NEXT_PAYLOAD = "devdiag:logs:next"
DEV_DIAGNOSTICS_NOOP_PAYLOAD = "devdiag:noop"
DEV_DIAGNOSTICS_USER_LOGS_PAYLOAD = "devdiag:user_logs"
DEV_DIAGNOSTICS_EVENT_SEARCH_PAYLOAD = "devdiag:event_search"
DEV_DIAGNOSTICS_STATUS_PAYLOAD = "devdiag:status"
DEV_DIAGNOSTICS_YCLIENTS_SMOKE_PAYLOAD = "devdiag:yclients_smoke"
DEV_DIAGNOSTICS_RESTART_HELP_PAYLOAD = "devdiag:restart_help"
SETTINGS_BACK_PAYLOAD = "settings:back"
SETTINGS_HOME_PAYLOAD = "settings:home"
SETTINGS_CONTACTS_EDIT_ADDRESS_PAYLOAD = "settings:contacts:address"
SETTINGS_CONTACTS_EDIT_PHONE_PAYLOAD = "settings:contacts:phone"
SETTINGS_CONTACTS_EDIT_SCHEDULE_PAYLOAD = "settings:contacts:schedule"
SETTINGS_CONTACTS_RESET_PAYLOAD = "settings:contacts:reset"
SETTINGS_CONTACTS_PREVIEW_PAYLOAD = "settings:contacts:preview"
SETTINGS_CONTACTS_MAP_YANDEX_PAYLOAD = "settings:contacts:map:yandex"
SETTINGS_CONTACTS_MAP_TWOGIS_PAYLOAD = "settings:contacts:map:twogis"
SETTINGS_CONTACTS_MAP_GOOGLE_PAYLOAD = "settings:contacts:map:google"
SETTINGS_CONTACTS_MAP_EDIT_PREFIX = "settings:contacts:map:edit:"
SETTINGS_CONTACTS_MAP_HIDE_PREFIX = "settings:contacts:map:hide:"
SETTINGS_CONTACTS_MAP_SHOW_PREFIX = "settings:contacts:map:show:"
SETTINGS_CONTACTS_MAP_DELETE_PREFIX = "settings:contacts:map:delete:"
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

STATISTICS_TODAY_PAYLOAD = "stats:today"
STATISTICS_7_DAYS_PAYLOAD = "stats:7"
STATISTICS_30_DAYS_PAYLOAD = "stats:30"
STATISTICS_90_DAYS_PAYLOAD = "stats:90"
STATISTICS_BACK_PAYLOAD = "stats:back"
STATISTICS_HOME_PAYLOAD = "stats:home"
ADMIN_BOOKINGS_TODAY_PAYLOAD = "admbook:day:today"
ADMIN_BOOKINGS_TOMORROW_PAYLOAD = "admbook:day:tomorrow"
ADMIN_BOOKINGS_REFRESH_PAYLOAD = "admbook:refresh"
ADMIN_BOOKINGS_BACK_PAYLOAD = "admbook:back"
ADMIN_BOOKINGS_HOME_PAYLOAD = "admbook:home"
ADMIN_BOOKINGS_ITEM_PAYLOAD_PREFIX = "admbook:item:"
ADMIN_BOOKINGS_MASTER_PAYLOAD_PREFIX = "admbook:master:"
ADMIN_BOOKINGS_STATUS_PAYLOAD_PREFIX = "admbook:status:"

NOTIFICATION_HISTORY_FAILED_PAYLOAD = "notification_history:failed"
NOTIFICATION_HISTORY_REFRESH_PAYLOAD = "notification_history:refresh"
NOTIFICATION_HISTORY_BACK_PAYLOAD = "notification_history:back"
NOTIFICATION_HISTORY_DIAGNOSTICS_PAYLOAD = "notification_history:diagnostics"
NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX = "notification_history:detail:"

BROADCAST_ONE_TIME_START_PAYLOAD = "broadcast:one_time:start"
BROADCAST_PREVIEW_NEXT_PAYLOAD = "broadcast:preview:next"
BROADCAST_PREVIEW_EDIT_PAYLOAD = "broadcast:preview:edit"
BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD = "broadcast:preview:remove_attachment"
BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD = "broadcast:preview:edit_attachment"
BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD = "broadcast:aud:all"
BROADCAST_AUDIENCE_SELF_PAYLOAD = "broadcast:aud:self"
BROADCAST_SEGMENTS_PAYLOAD = "broadcast:segments"
SEGMENTS_ALL_CLIENTS_PAYLOAD = "segments:all_clients"
SEGMENTS_ACTIVE_7_PAYLOAD = "segments:active:7"
SEGMENTS_ACTIVE_30_PAYLOAD = "segments:active:30"
SEGMENTS_ACTIVE_90_PAYLOAD = "segments:active:90"
SEGMENTS_LOST_PAYLOAD = "segments:lost"
LOST_CLIENTS_OPEN_PAYLOAD = "lost_clients:open"
LOST_CLIENTS_REFRESH_PAYLOAD = "lost_clients:refresh"
LOST_CLIENTS_BROADCAST_PAYLOAD = "lost_clients:broadcast"
LOST_CLIENTS_BACK_PAYLOAD = "lost_clients:back"
LOST_CLIENTS_HOME_PAYLOAD = "lost_clients:home"
LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX = "lost_clients:book:"
LOST_CLIENTS_BOOKING_BUTTON_TEXT = "✂️ Записаться"
SEGMENTS_NO_FUTURE_BOOKINGS_PAYLOAD = "segments:no_future_bookings"
SEGMENTS_REFRESH_PAYLOAD = "segments:refresh"
SEGMENTS_BROADCAST_PAYLOAD = "segments:broadcast"
SEGMENTS_BACK_PAYLOAD = "segments:back"
SEGMENTS_HOME_PAYLOAD = "segments:home"
BROADCAST_CONFIRM_SEND_PAYLOAD = "broadcast:confirm:send"
BROADCAST_NEW_PAYLOAD = "broadcast:new"
BROADCAST_BACK_PAYLOAD = "broadcast:back"
BROADCAST_HOME_PAYLOAD = "broadcast:home"

BROADCAST_LOST_CLIENTS_PAYLOAD = "broadcast:lost_clients"
BROADCAST_EFFECTIVENESS_PAYLOAD = "broadcast:effectiveness"
BROADCAST_HISTORY_PAYLOAD = "broadcast:history"
BROADCAST_TESTS_PAYLOAD = "broadcast:tests"
DEV_TESTS_ROOT_PAYLOAD = "broadcast:dev_tests:root"
DEV_TESTS_PAYLOAD_PREFIX = "broadcast:dev_tests:"
DEV_TESTS_CLEANUP_CONFIRM_PAYLOAD = "broadcast:dev_tests:cleanup_confirm"
BROADCAST_TEST_CONFIRM_48H_PAYLOAD = "broadcast:dev_tests:booking_confirm_2d"
BROADCAST_TEST_REMINDER_2H_PAYLOAD = "broadcast:dev_tests:booking_reminder_2h"
BROADCAST_AUDIENCE_ACTIVE_30_PAYLOAD = "broadcast:aud:active_30"
BROADCAST_AUDIENCE_LOST_30_PAYLOAD = "broadcast:aud:lost_30"
BROADCAST_AUDIENCE_LOST_60_PAYLOAD = "broadcast:aud:lost_60"
BROADCAST_AUDIENCE_LOST_90_PAYLOAD = "broadcast:aud:lost_90"
BROADCAST_AUDIENCE_NO_FUTURE_PAYLOAD = "broadcast:aud:no_future"
BROADCAST_AUDIENCE_CANCELLED_PAYLOAD = "broadcast:aud:cancelled"
BROADCAST_AUDIENCE_BIRTHDAY_PAYLOAD = "broadcast:aud:birthday"
SEGMENTS_LOST_30_PAYLOAD = "segments:lost:30"
SEGMENTS_LOST_60_PAYLOAD = "segments:lost:60"
SEGMENTS_LOST_90_PAYLOAD = "segments:lost:90"
SEGMENTS_CANCELLED_PAYLOAD = "segments:cancelled"
SEGMENTS_BY_MASTER_PAYLOAD = "segments:by_master"
SEGMENTS_BY_MASTER_PREFIX = "segments:by_master:"
SEGMENTS_BY_SERVICE_PAYLOAD = "segments:by_service"
SEGMENTS_BY_SERVICE_PREFIX = "segments:by_service:"
SEGMENTS_BIRTHDAY_SOON_PAYLOAD = "segments:birthday_soon"

NAV_BACK_PAYLOAD = "nav:back"
NAV_HOME_PAYLOAD = "nav:home"

CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD = "clients:search_phone"
CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD = "clients:search_name"
CLIENTS_DIRECTORY_BACK_PAYLOAD = "clients:back"
CLIENTS_DIRECTORY_HOME_PAYLOAD = "clients:home"
CLIENTS_DIRECTORY_REFRESH_PAYLOAD = "clients:refresh"
CLIENTS_DIRECTORY_RESULT_PAYLOAD_PREFIX = "clients:result:"

YCLIENTS_SETUP_PAYLOAD = "yclients:setup"
YCLIENTS_CHECK_PAYLOAD = "yclients:check"
YCLIENTS_RESET_PAYLOAD = "yclients:reset"
YCLIENTS_RESET_YES_PAYLOAD = "yclients:reset:yes"
YCLIENTS_RESET_NO_PAYLOAD = "yclients:reset:no"
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
BOOKING_DATE_PREV_PAYLOAD = "booking:date_page:prev"
BOOKING_DATE_NEXT_PAYLOAD = "booking:date_page:next"
BOOKING_SLOT_PAYLOAD_PREFIX = "booking:slot:"
BOOKING_SLOT_PREV_PAYLOAD = "booking:slot_page:prev"
BOOKING_SLOT_NEXT_PAYLOAD = "booking:slot_page:next"
BOOKING_CONFIRM_PAYLOAD = "booking:confirm"
BOOKING_CANCEL_DRAFT_PAYLOAD = "booking:cancel_draft"
BOOKING_PHONE_USE_REGISTERED_PAYLOAD = "booking:phone:use_registered"

MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX = "my_bookings:details:"
MY_BOOKINGS_PAGE_PAYLOAD_PREFIX = "my_bookings:page:"
MY_BOOKINGS_SHOW_ALL_ACTIVE_PAYLOAD = "my_bookings:all"
MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX = "my_bookings:all_page:"
MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX = "my_bookings:history:"
MY_BOOKINGS_CANCEL_START_PAYLOAD = "my_bookings:cancel:start"
MY_BOOKINGS_CANCEL_CONFIRM_PAYLOAD = "my_bookings:cancel:confirm"
MY_BOOKINGS_REPEAT_START_PAYLOAD = "my_bookings:repeat:start"
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
        ADMIN_BOOKINGS_OPEN_PAYLOAD,
        ADMIN_YCLIENTS_PAYLOAD,
        ADMIN_NOTIFICATION_HISTORY_PAYLOAD,
        ADMIN_CLIENTS_DIRECTORY_PAYLOAD,
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



def clients_directory_menu_keyboard() -> MaxInlineKeyboard:
    """Build clients directory search mode buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="📞 По телефону", payload=CLIENTS_DIRECTORY_SEARCH_PHONE_PAYLOAD)],
            [MaxButton(text="🔎 По имени", payload=CLIENTS_DIRECTORY_SEARCH_NAME_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=CLIENTS_DIRECTORY_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=CLIENTS_DIRECTORY_HOME_PAYLOAD)],
        ]
    )


def clients_directory_search_keyboard() -> MaxInlineKeyboard:
    """Build navigation for clients directory text input."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=CLIENTS_DIRECTORY_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=CLIENTS_DIRECTORY_HOME_PAYLOAD)],
        ]
    )


def clients_directory_results_keyboard(results: list[object], *, has_next: bool = False) -> MaxInlineKeyboard:
    """Build safe state-indexed client search result buttons."""

    rows: list[list[MaxButton]] = []
    for index, item in enumerate(results[:8]):
        name = str(item.get("name") or item.get("fullname") or "Клиент") if isinstance(item, dict) else "Клиент"
        phone = str(item.get("phone") or "") if isinstance(item, dict) else ""
        yclients_client_id = str(item.get("id") or item.get("client_id") or "") if isinstance(item, dict) else ""
        suffix = f" • ID {yclients_client_id}" if yclients_client_id else ""
        phone_part = f" • 📞 {_mask_clients_directory_phone(phone)}" if phone else ""
        rows.append([MaxButton(text=f"👤 {name}{phone_part}{suffix}"[:64], payload=indexed_payload(CLIENTS_DIRECTORY_RESULT_PAYLOAD_PREFIX, index))])
    if has_next:
        rows.append([MaxButton(text="Уточните запрос, найдено больше 8", payload=CLIENTS_DIRECTORY_REFRESH_PAYLOAD)])
    rows.append([MaxButton(text="🔄 Обновить", payload=CLIENTS_DIRECTORY_REFRESH_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=CLIENTS_DIRECTORY_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=CLIENTS_DIRECTORY_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def clients_directory_card_keyboard() -> MaxInlineKeyboard:
    """Build client card navigation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=CLIENTS_DIRECTORY_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=CLIENTS_DIRECTORY_HOME_PAYLOAD)],
        ]
    )


def _mask_clients_directory_phone(phone: str | None) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if not digits:
        return "—"
    return f"+{'*' * max(1, len(digits) - 4)}{digits[-4:]}"


def settings_menu_keyboard(role: str | None = None, *, protected_developer: bool = False) -> MaxInlineKeyboard:
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
    if protected_developer:
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
            [MaxButton(text="🗺 Яндекс Карты", payload=SETTINGS_CONTACTS_MAP_YANDEX_PAYLOAD)],
            [MaxButton(text="🗺 2GIS", payload=SETTINGS_CONTACTS_MAP_TWOGIS_PAYLOAD)],
            [MaxButton(text="🗺 Google Maps", payload=SETTINGS_CONTACTS_MAP_GOOGLE_PAYLOAD)],
            [MaxButton(text="♻️ Сбросить к данным YClients", payload=SETTINGS_CONTACTS_RESET_PAYLOAD)],
            [MaxButton(text="👁️ Предпросмотр", payload=SETTINGS_CONTACTS_PREVIEW_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )



def settings_contacts_map_keyboard(*, map_key: str, enabled: bool) -> MaxInlineKeyboard:
    """Build one map-link settings submenu keyboard."""

    visibility_button = MaxButton(
        text="🙈 Скрыть кнопку" if enabled else "👁 Показать кнопку",
        payload=(
            f"{SETTINGS_CONTACTS_MAP_HIDE_PREFIX}{map_key}"
            if enabled
            else f"{SETTINGS_CONTACTS_MAP_SHOW_PREFIX}{map_key}"
        ),
    )
    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✏️ Изменить ссылку", payload=f"{SETTINGS_CONTACTS_MAP_EDIT_PREFIX}{map_key}")],
            [visibility_button],
            [MaxButton(text="🗑 Удалить ссылку", payload=f"{SETTINGS_CONTACTS_MAP_DELETE_PREFIX}{map_key}")],
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


def settings_notifications_keyboard(*, enabled: bool = True) -> MaxInlineKeyboard:
    """Build notification settings buttons."""

    toggle = MaxButton(
        text="❌ Выключить уведомления" if enabled else "✅ Включить уведомления",
        payload=SETTINGS_NOTIFICATIONS_DISABLE_PAYLOAD if enabled else SETTINGS_NOTIFICATIONS_ENABLE_PAYLOAD,
    )
    return MaxInlineKeyboard.from_rows(
        [
            [toggle],
            [MaxButton(text="⚙️ Настройки рассылок", payload=SETTINGS_AUTOMATION_ROOT_PAYLOAD)],
            [MaxButton(text="🧾 История уведомлений", payload=SETTINGS_DIAGNOSTICS_HISTORY_PAYLOAD)],
            [MaxButton(text="🔄 Проверить работу уведомлений", payload=SETTINGS_NOTIFICATIONS_SMOKE_PAYLOAD)],
            [MaxButton(text="🧪 Тест уведомлений", payload=SETTINGS_NOTIFICATIONS_TESTS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )



def settings_automation_root_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⭐ Оценка после визита", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}post_visit_review")],
            [MaxButton(text="❌ Возврат после отмены", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}cancellation_return")],
            [MaxButton(text="😔 Потерянные клиенты", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}lost_clients")],
            [MaxButton(text="🎂 День рождения", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}birthday")],
            [MaxButton(text="🔁 Повторный визит", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}repeat_visit")],
            [MaxButton(text="🔕 Антиспам", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}anti_spam")],
            [MaxButton(text="🔗 Ссылки на отзывы", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}review_links")],
            [MaxButton(text="⏰ Рабочее время / тихие часы", payload=f"{SETTINGS_AUTOMATION_MODULE_PREFIX}quiet_hours")],
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def settings_automation_module_keyboard(key: str, *, enabled: bool = False) -> MaxInlineKeyboard:
    rows: list[list[MaxButton]] = []
    if key in {"post_visit_review", "cancellation_return", "lost_clients", "birthday", "repeat_visit", "quiet_hours"}:
        text = "⛔ Выключить" if enabled else "✅ Включить"
        if key == "quiet_hours":
            text = "☀️ Выключить тихие часы" if enabled else "🌙 Включить тихие часы"
        rows.append([MaxButton(text=text, payload=f"{SETTINGS_AUTOMATION_TOGGLE_PREFIX}{key}")])
    mapping = {
        "post_visit_review": [("⏱ Изменить задержку", "delay_hours"), ("✏️ Изменить текст", "message_text"), ("🔗 Ссылки на отзывы", "go_review_links")],
        "cancellation_return": [("⏱ Изменить задержку", "delay_hours"), ("✏️ Изменить текст", "message_text")],
        "lost_clients": [("⏱ Настроить сроки", "threshold_days"), ("✏️ Текст 30 дней", "text_30"), ("✏️ Текст 60 дней", "text_60"), ("✏️ Текст 90 дней", "text_90")],
        "birthday": [("📅 Изменить срок отправки", "send_days_before"), ("✏️ Изменить текст", "message_text")],
        "repeat_visit": [("⏱ Изменить срок по умолчанию", "delay_days"), ("✏️ Текст 1", "template_1"), ("✏️ Текст 2", "template_2"), ("✏️ Текст 3", "template_3"), ("✏️ Текст 4", "template_4"), ("✏️ Текст 5", "template_5")],
        "anti_spam": [("🔢 Изменить лимит в неделю", "max_weekly_marketing"), ("⏱ Изменить минимальный интервал", "min_interval_hours"), ("ℹ️ Белые и зелёные уведомления", "white_green_info")],
        "review_links": [("🟡 Изменить ссылку Яндекс", "yandex_url"), ("🟢 Изменить ссылку 2ГИС", "two_gis_url"), ("🧹 Очистить ссылку Яндекс", "clear_yandex"), ("🧹 Очистить ссылку 2ГИС", "clear_two_gis")],
        "quiet_hours": [("⏰ Изменить тихие часы", "range"), ("📌 Режим вне рабочего времени", "outside_allowed_behavior")],
    }
    for label, field in mapping.get(key, []):
        rows.append([MaxButton(text=label, payload=f"{SETTINGS_AUTOMATION_EDIT_PREFIX}{key}:{field}")])
    rows.append([MaxButton(text="⬅️ Назад", payload=SETTINGS_AUTOMATION_ROOT_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def settings_automation_input_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([[MaxButton(text="⬅️ Назад", payload=SETTINGS_BACK_PAYLOAD)], [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)]])

def settings_diagnostics_keyboard() -> MaxInlineKeyboard:
    """Build diagnostics settings buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🧾 Логи бота (последние 200 строк)", payload=DEV_DIAGNOSTICS_BOT_LOGS_PAYLOAD)],
            [MaxButton(text="📦 Скачать логи бота (CSV)", payload=DEV_DIAGNOSTICS_BOT_LOGS_CSV_PAYLOAD)],
            [MaxButton(text="👤 Логи пользователя", payload=DEV_DIAGNOSTICS_USER_LOGS_PAYLOAD)],
            [MaxButton(text="🔎 Поиск по событиям", payload=DEV_DIAGNOSTICS_EVENT_SEARCH_PAYLOAD)],
            [MaxButton(text="💡 Статус системы", payload=DEV_DIAGNOSTICS_STATUS_PAYLOAD)],
            [MaxButton(text="🧪 YClients: client sync smoke test", payload=DEV_DIAGNOSTICS_YCLIENTS_SMOKE_PAYLOAD)],
            [MaxButton(text="♻️ Перезапустить бота (инструкция)", payload=DEV_DIAGNOSTICS_RESTART_HELP_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
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


def admin_bookings_list_keyboard(items: list[str], *, page: int, max_page: int) -> MaxInlineKeyboard:
    """Build Telegram-equivalent admin bookings list keyboard with indexed callbacks."""

    rows: list[list[MaxButton]] = [
        [
            MaxButton(text="📅 Сегодня", payload=ADMIN_BOOKINGS_TODAY_PAYLOAD),
            MaxButton(text="📅 Завтра", payload=ADMIN_BOOKINGS_TOMORROW_PAYLOAD),
        ],
        [
            MaxButton(text="👤 Мастер", payload="admbook:filter:master"),
            MaxButton(text="🧾 Статус", payload="admbook:filter:status"),
        ],
        [MaxButton(text="🔄 Обновить", payload=ADMIN_BOOKINGS_REFRESH_PAYLOAD)],
    ]
    for index, label in enumerate(items[:10]):
        rows.append([MaxButton(text=label, payload=f"{ADMIN_BOOKINGS_ITEM_PAYLOAD_PREFIX}{index}")])
    navigation: list[MaxButton] = []
    if page > 0:
        navigation.append(MaxButton(text="⬅️", payload=f"admbook:page:{page - 1}"))
    if page < max_page and page < 9:
        navigation.append(MaxButton(text="➡️", payload=f"admbook:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([MaxButton(text="⬅️ Назад", payload=ADMIN_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=ADMIN_BOOKINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def admin_bookings_master_keyboard(masters: list[tuple[str, str]]) -> MaxInlineKeyboard:
    """Build indexed master filter keyboard without raw ids in payloads."""

    rows: list[list[MaxButton]] = [[MaxButton(text="🎲 Все мастера", payload="admbook:master:all")]]
    for index, (_, name) in enumerate(masters[:30]):
        rows.append([MaxButton(text=f"👤 {name}", payload=f"{ADMIN_BOOKINGS_MASTER_PAYLOAD_PREFIX}{index}")])
    rows.append([MaxButton(text="⬅️ Назад", payload=ADMIN_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=ADMIN_BOOKINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def admin_bookings_status_keyboard(statuses: list[str]) -> MaxInlineKeyboard:
    """Build indexed status filter keyboard."""

    rows: list[list[MaxButton]] = [[MaxButton(text="📌 Все статусы", payload="admbook:status:all")]]
    for index, status in enumerate(statuses[:30]):
        rows.append([MaxButton(text=f"🧾 {status}", payload=f"{ADMIN_BOOKINGS_STATUS_PAYLOAD_PREFIX}{index}")])
    rows.append([MaxButton(text="⬅️ Назад", payload=ADMIN_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=ADMIN_BOOKINGS_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def admin_booking_detail_keyboard() -> MaxInlineKeyboard:
    """Build admin booking detail navigation keyboard."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="⬅️ Назад", payload=ADMIN_BOOKINGS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=ADMIN_BOOKINGS_HOME_PAYLOAD)],
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
        rows.append([MaxButton(text="📋 Все уведомления", payload=NOTIFICATION_HISTORY_REFRESH_PAYLOAD)])
        rows.append([MaxButton(text="🔄 Обновить", payload=NOTIFICATION_HISTORY_FAILED_PAYLOAD)])
    else:
        rows.append([MaxButton(text="❌ Только ошибки", payload=NOTIFICATION_HISTORY_FAILED_PAYLOAD)])
        rows.append([MaxButton(text="🔄 Обновить", payload=NOTIFICATION_HISTORY_REFRESH_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def notification_history_detail_keyboard() -> MaxInlineKeyboard:
    """Build notification history detail navigation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔎 Диагностика", payload=NOTIFICATION_HISTORY_DIAGNOSTICS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=NOTIFICATION_HISTORY_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def yclients_settings_keyboard(*, can_manage: bool = True) -> MaxInlineKeyboard:
    """Build YClients integration settings menu buttons."""

    rows: list[list[MaxButton]] = []
    if can_manage:
        rows.append([MaxButton(text="🧩 Настроить / Изменить", payload=YCLIENTS_SETUP_PAYLOAD)])
    rows.append([MaxButton(text="🔌 Проверить подключение", payload=YCLIENTS_CHECK_PAYLOAD)])
    if can_manage:
        rows.append([MaxButton(text="🧹 Сбросить настройки", payload=YCLIENTS_RESET_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="⬅️ Назад", payload=YCLIENTS_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=YCLIENTS_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def yclients_reset_confirm_keyboard() -> MaxInlineKeyboard:
    """Build YClients reset confirmation buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [
                MaxButton(text="✅ Да", payload=YCLIENTS_RESET_YES_PAYLOAD),
                MaxButton(text="❌ Нет", payload=YCLIENTS_RESET_NO_PAYLOAD),
            ],
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
            [MaxButton(text="😔 Потерянные клиенты", payload=LOST_CLIENTS_OPEN_PAYLOAD)],
            [MaxButton(text="📊 Эффективность", payload=BROADCAST_EFFECTIVENESS_PAYLOAD)],
            [MaxButton(text="📜 История уведомлений", payload=BROADCAST_HISTORY_PAYLOAD)],
            [MaxButton(text="🧪 Тест уведомлений", payload=BROADCAST_TESTS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def client_segments_menu_keyboard() -> MaxInlineKeyboard:
    """Build client segments selection buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="👥 Все клиенты", payload=SEGMENTS_ALL_CLIENTS_PAYLOAD)],
            [MaxButton(text="🔥 Активные за 30 дней", payload=SEGMENTS_ACTIVE_30_PAYLOAD)],
            [MaxButton(text="😴 Не были 30 дней", payload=SEGMENTS_LOST_30_PAYLOAD)],
            [MaxButton(text="😴 Не были 60 дней", payload=SEGMENTS_LOST_60_PAYLOAD)],
            [MaxButton(text="😴 Не были 90 дней", payload=SEGMENTS_LOST_90_PAYLOAD)],
            [MaxButton(text="📅 Без будущей записи", payload=SEGMENTS_NO_FUTURE_BOOKINGS_PAYLOAD)],
            [MaxButton(text="❌ Отменили запись", payload=SEGMENTS_CANCELLED_PAYLOAD)],
            [MaxButton(text="💈 По мастеру", payload=SEGMENTS_BY_MASTER_PAYLOAD)],
            [MaxButton(text="✂️ По услуге", payload=SEGMENTS_BY_SERVICE_PAYLOAD)],
            [MaxButton(text="🎂 День рождения скоро", payload=SEGMENTS_BIRTHDAY_SOON_PAYLOAD)],
            [MaxButton(text="🔄 Обновить сегменты", payload=SEGMENTS_REFRESH_PAYLOAD)],
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
        rows.append([MaxButton(text="📣 Использовать для рассылки", payload=SEGMENTS_BROADCAST_PAYLOAD)])
    rows.extend(
        [
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


def broadcast_preview_keyboard(*, has_attachment: bool = False) -> MaxInlineKeyboard:
    """Build broadcast preview action buttons."""

    rows = [
        [MaxButton(text="✅ Отправить", payload=BROADCAST_CONFIRM_SEND_PAYLOAD)],
        [MaxButton(text="✏️ Изменить текст", payload=BROADCAST_PREVIEW_EDIT_PAYLOAD)],
    ]
    rows.append([MaxButton(text="📷 Изменить фото" if has_attachment else "📷 Добавить фото", payload=BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD)])
    if has_attachment:
        rows.append([MaxButton(text="🗑 Убрать фото", payload=BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD)])
    rows.extend(
        [
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def broadcast_audience_keyboard() -> MaxInlineKeyboard:
    """Build one-time broadcast audience buttons."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="👥 Все клиенты", payload=BROADCAST_AUDIENCE_ALL_USERS_PAYLOAD)],
            [MaxButton(text="🔥 Активные за 30 дней", payload=BROADCAST_AUDIENCE_ACTIVE_30_PAYLOAD)],
            [MaxButton(text="😴 Потерянные 30 дней", payload=BROADCAST_AUDIENCE_LOST_30_PAYLOAD)],
            [MaxButton(text="😴 Потерянные 60 дней", payload=BROADCAST_AUDIENCE_LOST_60_PAYLOAD)],
            [MaxButton(text="😴 Потерянные 90 дней", payload=BROADCAST_AUDIENCE_LOST_90_PAYLOAD)],
            [MaxButton(text="📅 Без будущей записи", payload=BROADCAST_AUDIENCE_NO_FUTURE_PAYLOAD)],
            [MaxButton(text="🧪 Отправить себе", payload=BROADCAST_AUDIENCE_SELF_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=BROADCAST_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=BROADCAST_HOME_PAYLOAD)],
        ]
    )


def broadcast_confirm_keyboard(*, can_send: bool = True) -> MaxInlineKeyboard:
    """Build final broadcast confirmation buttons."""

    rows: list[list[MaxButton]] = []
    if can_send:
        rows.append([MaxButton(text="✅ Отправить", payload=BROADCAST_CONFIRM_SEND_PAYLOAD)])
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
            [MaxButton(text="✉️ Новая рассылка", payload=BROADCAST_NEW_PAYLOAD)],
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


def contacts_keyboard(route_links: dict[str, str]) -> MaxInlineKeyboard:
    """Build contacts route buttons plus Back/Home navigation."""

    rows: list[list[MaxButton]] = []
    for label in ("Яндекс Карты", "2GIS", "Google Maps"):
        url = str(route_links.get(label) or "").strip()
        if url.startswith(("https://", "http://")):
            rows.append([MaxButton(text=label, type="link", url=url)])
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def feedback_public_review_links_keyboard(*, yandex_url: str, two_gis_url: str) -> MaxInlineKeyboard:
    """Build public review link buttons with Telegram labels."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="Яндекс Карты", type="link", url=yandex_url)],
            [MaxButton(text="2GIS", type="link", url=two_gis_url)],
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
    has_previous: bool = False,
    has_next: bool = False,
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
    page_row = []
    if has_previous:
        page_row.append(MaxButton(text="⬅️", payload=BOOKING_DATE_PREV_PAYLOAD))
    if has_next:
        page_row.append(MaxButton(text="➡️", payload=BOOKING_DATE_NEXT_PAYLOAD))
    if page_row:
        rows.append(page_row)
    rows.append([MaxButton(text="⬅️ Назад", payload=back_payload)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def booking_slots_keyboard(
    slots: list[object],
    title_formatter,
    *,
    has_previous: bool = False,
    has_next: bool = False,
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
    page_row = []
    if has_previous:
        page_row.append(MaxButton(text="⬅️", payload=BOOKING_SLOT_PREV_PAYLOAD))
    if has_next:
        page_row.append(MaxButton(text="➡️", payload=BOOKING_SLOT_NEXT_PAYLOAD))
    if page_row:
        rows.append(page_row)
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


def my_bookings_rate_limit_keyboard() -> MaxInlineKeyboard:
    """Build retry/back/home buttons for temporary YClients throttling."""

    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="🔄 Повторить", payload=MENU_MY_BOOKINGS_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)],
        ]
    )


def my_bookings_keyboard(*, include_booking: bool = False) -> MaxInlineKeyboard:
    """Build My bookings navigation buttons."""

    rows: list[list[MaxButton]] = []
    if include_booking:
        rows.append([MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)])
    else:
        rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_bookings_list_keyboard(
    bookings: int | list[object],
    *,
    timezone_name: str | None = None,
    max_buttons: int = 20,
    page: int = 0,
) -> MaxInlineKeyboard:
    """Build booking selection buttons with short indexed MAX payloads."""

    if isinstance(bookings, int):
        items: list[object] = []
        bookings_count = bookings
    else:
        items = bookings
        bookings_count = len(items)

    safe_page = max(page, 0)
    page_size = max(1, max_buttons)
    start = safe_page * page_size
    end = min(start + page_size, max(bookings_count, 0))

    rows: list[list[MaxButton]] = []
    for index in range(start, end):
        label = _my_booking_button_label(items[index], index=index, timezone_name=timezone_name) if index < len(items) else f"📋 Запись {index + 1}"
        rows.append([MaxButton(text=label, payload=indexed_payload(MY_BOOKINGS_DETAILS_PAYLOAD_PREFIX, index))])
    navigation: list[MaxButton] = []
    if safe_page > 0:
        navigation.append(MaxButton(text="⬅️ Предыдущие", payload=f"{MY_BOOKINGS_PAGE_PAYLOAD_PREFIX}{safe_page - 1}"))
    if end < bookings_count:
        navigation.append(MaxButton(text="➡️ Следующие", payload=f"{MY_BOOKINGS_PAGE_PAYLOAD_PREFIX}{safe_page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_bookings_main_keyboard(
    bookings: list[object],
    *,
    timezone_name: str | None = None,
    max_buttons: int = 20,
    page: int = 0,
) -> MaxInlineKeyboard:
    """Build clean My bookings selector plus primary actions."""

    keyboard = my_bookings_list_keyboard(bookings, timezone_name=timezone_name, max_buttons=max_buttons, page=page)
    rows = [list(row) for row in keyboard.rows]
    if rows and rows[-1] and rows[-1][0].payload == NAV_HOME_PAYLOAD:
        rows.pop()
    if rows and rows[-1] and rows[-1][0].payload == NAV_BACK_PAYLOAD:
        rows.pop()
    rows.append([MaxButton(text="✂️ Записаться", payload=MENU_BOOKING_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def _my_booking_button_label(item: object, *, index: int, timezone_name: str | None = None) -> str:
    """Return a compact booking button label without putting record ids into payloads."""

    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    dt = getattr(item, "booking_datetime", None)
    if isinstance(item, dict):
        dt = item.get("booking_datetime") or item.get("datetime") or item.get("date")
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("T", " ").replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if isinstance(dt, datetime):
        try:
            zone = ZoneInfo(timezone_name or "Europe/Moscow")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=zone)
            dt = dt.astimezone(zone)
        except ZoneInfoNotFoundError:
            pass
        when = dt.strftime("%d.%m %H:%M")
    else:
        when = f"Запись {index + 1}"

    service = getattr(item, "service_name", None)
    master = getattr(item, "master_name", None)
    if isinstance(item, dict):
        service = item.get("service_name") or service
        master = item.get("master_name") or master
    details = " · ".join(str(value) for value in (service, master) if value)
    label = f"📋 {when}" + (f" · {details}" if details else "")
    return label[:80]


def my_booking_entry_keyboard(*, can_cancel: bool = True, show_all: bool = False) -> MaxInlineKeyboard:
    """Build Telegram-reference actions for the nearest My bookings card."""

    rows: list[list[MaxButton]] = [[MaxButton(text="🔁 Перенести запись", payload=MY_BOOKINGS_RESCHEDULE_START_PAYLOAD)]]
    rows.append([MaxButton(text="❌ Отменить запись", payload=MY_BOOKINGS_CANCEL_START_PAYLOAD)])
    rows.append([MaxButton(text="🔂 Повторить запись", payload=MY_BOOKINGS_REPEAT_START_PAYLOAD)])
    if show_all:
        rows.append([MaxButton(text="📋 Показать все активные записи", payload=MY_BOOKINGS_SHOW_ALL_ACTIVE_PAYLOAD)])
    rows.append([MaxButton(text="🕘 История визитов", payload=f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}0")])
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_booking_active_card_keyboard(*, index: int, total: int, can_cancel: bool = True) -> MaxInlineKeyboard:
    """Build Telegram-reference carousel controls for active bookings."""

    safe_index = min(max(index, 0), max(total - 1, 0))
    pager: list[MaxButton] = []
    if safe_index > 0:
        pager.append(MaxButton(text="◀️", payload=f"{MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}{safe_index - 1}"))
    pager.append(MaxButton(text=f"{safe_index + 1}/{max(total, 1)}", payload=f"{MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}{safe_index}"))
    if safe_index + 1 < total:
        pager.append(MaxButton(text="▶️", payload=f"{MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}{safe_index + 1}"))

    rows: list[list[MaxButton]] = [pager]
    rows.append([MaxButton(text="🔁 Перенести", payload=MY_BOOKINGS_RESCHEDULE_START_PAYLOAD)])
    rows.append([MaxButton(text="❌ Отменить", payload=MY_BOOKINGS_CANCEL_START_PAYLOAD)])
    rows.append([MaxButton(text="🔂 Повторить", payload=MY_BOOKINGS_REPEAT_START_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=MENU_MY_BOOKINGS_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_bookings_history_keyboard(*, page: int = 0, has_next: bool = False, include_repeat: bool = True) -> MaxInlineKeyboard:
    """Build Telegram-reference visit history navigation."""

    rows: list[list[MaxButton]] = []
    pager: list[MaxButton] = []
    if page > 0:
        pager.append(MaxButton(text="⬅️", payload=f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}{page - 1}"))
    if has_next:
        pager.append(MaxButton(text="➡️", payload=f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}{page + 1}"))
    if pager:
        rows.append(pager)
    if include_repeat:
        rows.append([MaxButton(text="🔂 Повторить запись", payload=MY_BOOKINGS_REPEAT_START_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=MENU_MY_BOOKINGS_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_bookings_empty_keyboard(*, show_history: bool = False) -> MaxInlineKeyboard:
    """Build Telegram-reference empty active bookings actions."""

    rows: list[list[MaxButton]] = [[MaxButton(text="🔂 Повторить запись", payload=MY_BOOKINGS_REPEAT_START_PAYLOAD)]]
    rows.append([MaxButton(text="🕘 История визитов", payload=f"{MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}0")])
    rows.append([MaxButton(text="⬅️ Назад", payload=NAV_HOME_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


def my_booking_details_keyboard(*, can_cancel: bool = True, is_active: bool = True) -> MaxInlineKeyboard:
    """Build selected booking actions."""

    rows: list[list[MaxButton]] = []
    if is_active:
        rows.append([MaxButton(text="🔁 Перенести запись", payload=MY_BOOKINGS_RESCHEDULE_START_PAYLOAD)])
        rows.append([MaxButton(text="❌ Отменить запись", payload=MY_BOOKINGS_CANCEL_START_PAYLOAD)])
    rows.append([MaxButton(text="🔂 Повторить запись", payload=MY_BOOKINGS_REPEAT_START_PAYLOAD)])
    rows.append([MaxButton(text="⬅️ Назад", payload=MY_BOOKINGS_BACK_PAYLOAD)])
    rows.append([MaxButton(text="🏠 Главное меню", payload=NAV_HOME_PAYLOAD)])
    return MaxInlineKeyboard.from_rows(rows)


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

    rows = [[MaxButton(text="👀 Показать весь персонал", payload=STAFF_LIST_PAYLOAD)]]
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
        (ROLE_DEVELOPER, STAFF_ASSIGN_DEVELOPER_PAYLOAD),
        (ROLE_ADMIN, STAFF_ASSIGN_ADMIN_PAYLOAD),
        (ROLE_MANAGER, STAFF_ASSIGN_MANAGER_PAYLOAD),
    ]
    labels = {
        ROLE_DEVELOPER: "🧑‍💻 Разработчик",
        ROLE_ADMIN: "🛠️ Администратор",
        ROLE_MANAGER: "📋 Управляющий",
    }
    rows = [
        [MaxButton(text=labels[target_role], payload=payload)]
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
    labels = {
        ROLE_DEVELOPER: "🧑‍💻 Разработчик",
        ROLE_ADMIN: "🛠️ Администратор",
        ROLE_MANAGER: "📋 Управляющий",
    }
    rows = [
        [MaxButton(text=labels[role], payload=payloads[role])]
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


DEV_TEST_BUTTONS: tuple[tuple[str, str], ...] = (
    ("⭐️ Тест оценки после визита", "post_visit_review"),
    ("❌ Тест отмены записи", "cancellation"),
    ("😔 Тест потерянного клиента 30 дней", "lost_client_30"),
    ("😔 Тест потерянного клиента 60 дней", "lost_client_60"),
    ("😔 Тест потерянного клиента 90 дней", "lost_client_90"),
    ("🎂 Тест дня рождения", "birthday"),
    ("🔁 Тест повторного визита", "repeat_visit"),
    ("✅ Тест подтверждения записи (48ч+)", "booking_confirm_2d"),
    ("⏰ Тест напоминания о записи (2ч)", "booking_reminder_2h"),
    ("📣 Тест уведомления себе", "self"),
    ("🧹 Очистить тестовые события", "cleanup"),
)


def settings_notification_tests_keyboard() -> MaxInlineKeyboard:
    """Build Telegram-equivalent developer funnel test hub keyboard."""

    rows = [[MaxButton(text=text, payload=f"{DEV_TESTS_PAYLOAD_PREFIX}{key}")] for text, key in DEV_TEST_BUTTONS]
    rows.extend(
        [
            [MaxButton(text="⬅️ Назад", payload=SETTINGS_NOTIFICATIONS_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )
    return MaxInlineKeyboard.from_rows(rows)


def dev_tests_cleanup_confirm_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✅ Очистить", payload=DEV_TESTS_CLEANUP_CONFIRM_PAYLOAD)],
            [MaxButton(text="⬅️ Назад", payload=DEV_TESTS_ROOT_PAYLOAD)],
            [MaxButton(text="🏠 Главное меню", payload=SETTINGS_HOME_PAYLOAD)],
        ]
    )


def cancellation_recovery_keyboard(event_id: int | str) -> MaxInlineKeyboard:
    """Build Telegram-equivalent cancellation recovery CTA keyboard."""

    clean_event_id = str(event_id).strip()
    return MaxInlineKeyboard.from_rows(
        [
            [MaxButton(text="✂️ Подобрать новое время", payload=f"cancel_recovery:rebook:{clean_event_id}")],
            [MaxButton(text="📅 Выбрать другую дату", payload=f"cancel_recovery:date:{clean_event_id}")],
            [MaxButton(text="Позже", payload=f"cancel_recovery:later:{clean_event_id}")],
        ]
    )


def lost_client_booking_keyboard(event_id: int) -> MaxInlineKeyboard:
    """Telegram-equivalent lost client booking CTA."""

    return MaxInlineKeyboard(rows=((MaxButton(text=LOST_CLIENTS_BOOKING_BUTTON_TEXT, payload=f"{LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX}{int(event_id)}"),),))
