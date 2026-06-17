"""Notification history diagnostics flow for the MAX bot."""

from __future__ import annotations

import json
import re
from os import getenv

from max_barbershop_bot.core import state
from max_barbershop_bot.core.config import DEFAULT_DATABASE_PATH
from max_barbershop_bot.core.permissions import ROLE_USER, can_view_notification_history, is_protected_developer
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.repositories.notification_history import NotificationHistoryRecord, NotificationHistoryRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.company_time import CompanyTimeService
from max_barbershop_bot.services.navigation import show_home
from max_barbershop_bot.ui.buttons import (
    ADMIN_NOTIFICATION_HISTORY_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NOTIFICATION_HISTORY_BACK_PAYLOAD,
    NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX,
    NOTIFICATION_HISTORY_DIAGNOSTICS_PAYLOAD,
    NOTIFICATION_HISTORY_FAILED_PAYLOAD,
    NOTIFICATION_HISTORY_REFRESH_PAYLOAD,
    notification_history_detail_keyboard,
    notification_history_keyboard,
)

_HISTORY_IDS_KEY = "notification_history_ids"
_HISTORY_FILTER_KEY = "notification_history_filter"
_HISTORY_SELECTED_ID_KEY = "notification_history_selected_id"
_STALE_TEXT = "Эта запись истории уже неактуальна 🙏\n\nОткройте историю уведомлений заново."
_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
_ROOT_TEXT = (
    "📜 История уведомлений\n\n"
    "Здесь видно, какие уведомления бот отправлял клиентам: автоматические воронки, "
    "ручные рассылки и результат доставки."
)
_STATUS_LABELS = {
    "sent": "✅ Отправлено",
    "failed": "❌ Ошибка",
    "scheduled": "⏳ Запланировано",
    "pending": "⏳ Ожидает отправки",
    "sending": "📤 Отправляется",
    "delivering": "📨 Доставляется",
    "blocked": "🚫 Пользователь заблокировал бота",
    "stopped": "⛔ Пользователь остановил бота",
    "rate_limited": "⏳ Лимит отправки",
    "delivery_error": "❌ Ошибка доставки",
    "skipped": "⏭ Пропущено",
}
_TYPE_LABELS = {
    "manual_broadcast": "✉️ Ручная рассылка",
    "post_visit_rating": "⭐️ Оценка после визита",
    "cancellation_recovery": "❌ Возврат после отмены",
    "lost_client": "😔 Потерянный клиент",
    "birthday": "🎂 День рождения",
    "repeat_visit": "🔁 Повторный визит",
    "booking_confirmation_immediate": "✅ Подтверждение записи",
    "booking_confirmation_2d": "✅ Подтверждение записи (2 дня)",
    "booking_reminder_48h": "⏰ Напоминание за 48 часов",
    "booking_reminder_6h": "⏰ Напоминание за 6 часов",
    "booking_reminder_2h": "⏰ Напоминание о записи (2 часа)",
}
_ERROR_KEYWORDS = ("token", "authorization", "password", "secret", "phone", "bearer")
_FAILED_STATUSES = {"failed", "blocked", "stopped", "rate_limited", "delivery_error"}


def register_notification_history_routes(router: Router) -> None:
    """Register notification history diagnostics callbacks."""

    router.on_callback(ADMIN_NOTIFICATION_HISTORY_PAYLOAD, handle_notification_history)
    router.on_callback(NOTIFICATION_HISTORY_REFRESH_PAYLOAD, handle_notification_history)
    router.on_callback(NOTIFICATION_HISTORY_FAILED_PAYLOAD, handle_notification_history_failed)
    router.on_callback(NOTIFICATION_HISTORY_BACK_PAYLOAD, handle_notification_history_back)
    router.on_callback(NOTIFICATION_HISTORY_DIAGNOSTICS_PAYLOAD, handle_notification_history_diagnostics)
    for index in range(20):
        router.on_callback(f"{NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX}{index}", handle_notification_history_detail)


async def handle_notification_history(context: RouterContext) -> None:
    """Show recent notification history with compact status summary."""

    if not _can_view(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    await _send_recent_history(context)


async def handle_notification_history_failed(context: RouterContext) -> None:
    """Show failed, blocked and stopped notification rows."""

    if not _can_view(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    records = _repository().list_recent_failed(limit=11)
    _save_history_state(context, records, "failed")
    _set_screen(context, state.NOTIFICATION_HISTORY_FAILED_SCREEN)
    await context.send_text(_format_history_list(records, failed=True), keyboard=notification_history_keyboard(records, failed=True))


async def handle_notification_history_detail(context: RouterContext) -> None:
    """Show one notification history diagnostics card."""

    if not _can_view(context):
        await _send_no_access(context)
        return
    history_id = _history_id_from_payload(context)
    if history_id is None:
        await _send_stale_history(context)
        return
    record = _repository().get_by_id(history_id)
    if record is None:
        await _send_stale_history(context)
        return
    await _answer_callback_if_needed(context)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_SELECTED_ID_KEY, record.id)
    _set_screen(context, state.NOTIFICATION_HISTORY_DETAIL_SCREEN)
    await context.send_text(_format_history_detail(record), keyboard=notification_history_detail_keyboard())


async def handle_notification_history_back(context: RouterContext) -> None:
    """Return from history detail or failed filter to the recent list."""

    if not _can_view(context):
        await _send_no_access(context)
        return
    await _answer_callback_if_needed(context)
    filter_key = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_FILTER_KEY)
    if filter_key == "failed":
        await handle_notification_history_failed(context)
    else:
        await _send_recent_history(context)


async def handle_notification_history_diagnostics(context: RouterContext) -> None:
    """Re-read and show delivery diagnostics without resending."""

    if not _can_view(context):
        await _send_no_access(context)
        return
    raw_id = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_SELECTED_ID_KEY)
    try:
        history_id = int(raw_id)
    except (TypeError, ValueError):
        await _send_stale_history(context)
        return
    record = _repository().get_by_id(history_id)
    if record is None:
        await _send_stale_history(context)
        return
    await _answer_callback_if_needed(context)
    await context.send_text(_format_diagnostics(record), keyboard=notification_history_detail_keyboard())


async def handle_notification_history_home(context: RouterContext) -> None:
    """Return to the role-based home menu."""

    await show_home(context)


async def _send_recent_history(context: RouterContext) -> None:
    records = _repository().list_recent(limit=11)
    _save_history_state(context, records, "recent")
    _set_screen(context, state.NOTIFICATION_HISTORY_SCREEN)
    await context.send_text(
        _format_history_list(records, failed=False),
        keyboard=notification_history_keyboard(records, back_payload=NAV_BACK_PAYLOAD),
    )


def format_notification_status_label(status: str | None) -> str:
    """Return a friendly status label for diagnostics UI."""

    clean = str(status or "").strip()
    if not clean:
        return "⏳ Ожидает отправки"
    if clean.startswith("skipped"):
        return "⏭ Пропущено"
    return _STATUS_LABELS.get(clean, clean)


def format_notification_type_label(notification_type: str | None) -> str:
    """Return a readable notification type label."""

    clean = str(notification_type or "").strip()
    return _TYPE_LABELS.get(clean, clean or "📩 Уведомление")


def _format_history_list(records: list[NotificationHistoryRecord], *, failed: bool) -> str:
    counts = _repository().count_by_status()
    sent_count = counts.get("sent", 0)
    failed_count = sum(counts.get(status, 0) for status in _FAILED_STATUSES)
    blocked_count = counts.get("blocked", 0) + counts.get("stopped", 0)
    title = "❌ Ошибки уведомлений" if failed else _ROOT_TEXT
    lines = [
        title,
        "",
        f"Всего последних событий: {len(records)}",
        "",
        f"✅ Отправлено: {sent_count}",
        f"❌ Ошибки: {failed_count}",
        f"🚫 Заблокировано/остановлено: {blocked_count}",
        "",
        "Последние уведомления:" if not failed else "Последние ошибки:",
    ]
    if not records:
        lines.append("\nПока записей нет.")
        return "\n".join(lines)
    for record in records:
        lines.extend(("", _format_history_list_item(record)))
    return "\n".join(lines)[:3900]


def _format_history_list_item(record: NotificationHistoryRecord) -> str:
    return (
        f"#{record.id} — {format_notification_type_label(record.notification_type)}\n"
        f"Статус: {_effective_status_label(record)}\n"
        f"Пользователь: {_safe_value(record.platform_user_id)}\n"
        f"Запись: {_safe_value(record.yclients_record_id)}\n"
        f"Время: {_format_dt(record.sent_at or record.updated_at or record.created_at)}"
        f"{_short_error_marker(record)}"
    )


def _format_history_detail(record: NotificationHistoryRecord) -> str:
    flags = _flags_text(record)
    metadata = _metadata_summary(record.metadata_json)
    lines = [
        f"🔔 Уведомление #{record.id}",
        "",
        f"Тип: {format_notification_type_label(record.notification_type)}",
        f"Статус: {_effective_status_label(record)}",
        f"Получатель: {_safe_value(record.platform_user_id)}",
        f"Платформа: {_safe_value(record.platform)}",
        f"Запись YClients: {_safe_value(record.yclients_record_id)}",
        "",
        f"Запланировано: {_safe_value(record.scheduled_for)}",
        f"Отправлено: {_format_dt(record.sent_at)}",
        f"Попыток: {record.attempts}",
        "",
        f"HTTP статус: {_safe_value(record.delivery_status_code)}",
        f"Категория ошибки: {_safe_value(record.delivery_error_code)}",
        f"message_id: {_safe_value(record.message_id)}",
        f"Флаги: {flags}",
        "",
        f"Ошибка: {_error_text(record)}",
        f"trace_id: {_trace_id(record) or '—'}",
        f"Метаданные: {metadata}",
        "",
        f"Создано: {_format_dt(record.created_at)}",
        f"Обновлено: {_format_dt(record.updated_at)}",
    ]
    return "\n".join(lines)[:3900]


def _effective_status_label(record: NotificationHistoryRecord) -> str:
    if record.is_blocked:
        return format_notification_status_label("blocked")
    if record.is_stopped:
        return format_notification_status_label("stopped")
    return format_notification_status_label(record.status)


def _error_text(record: NotificationHistoryRecord) -> str:
    parts: list[str] = []
    if record.delivery_status_code is not None:
        parts.append(f"status {record.delivery_status_code}")
    if record.delivery_error_code:
        parts.append(record.delivery_error_code[:80])
    if record.delivery_error_message:
        parts.append(_sanitize_error_message(record.delivery_error_message))
    if not parts:
        return "Ошибок нет ✅"
    return "; ".join(parts)


def _sanitize_error_message(message: str) -> str:
    clean = " ".join(str(message).split())
    lowered = clean.lower()
    if "traceback" in lowered or "file \"" in lowered:
        return "техническая ошибка отправки"
    if any(keyword in lowered for keyword in _ERROR_KEYWORDS):
        return "скрыто из соображений безопасности"
    return clean[:240]


def _flags_text(record: NotificationHistoryRecord) -> str:
    flags = []
    if record.is_blocked:
        flags.append("🚫 Пользователь заблокировал бота")
    if record.is_stopped:
        flags.append("⛔ Пользователь остановил бота")
    return ", ".join(flags) if flags else "нет ✅"


def _metadata_summary(metadata_json: str | None) -> str:
    if not metadata_json:
        return "—"
    try:
        loaded = json.loads(metadata_json)
    except json.JSONDecodeError:
        return "есть, но не показаны целиком"
    if not isinstance(loaded, dict) or not loaded:
        return "—"
    safe_keys = [str(key) for key in loaded if not any(word in str(key).lower() for word in _ERROR_KEYWORDS)]
    if not safe_keys:
        return "скрыто из соображений безопасности"
    return "ключи: " + ", ".join(safe_keys[:8])


def _history_id_from_payload(context: RouterContext) -> int | None:
    payload = context.event.callback_payload or ""
    if not payload.startswith(NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX):
        return None
    try:
        index = int(payload.removeprefix(NOTIFICATION_HISTORY_DETAIL_PAYLOAD_PREFIX))
    except ValueError:
        return None
    history_ids = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_IDS_KEY)
    if not isinstance(history_ids, list) or index < 0 or index >= len(history_ids):
        return None
    try:
        return int(history_ids[index])
    except (TypeError, ValueError):
        return None


def _save_history_state(context: RouterContext, records: list[NotificationHistoryRecord], filter_key: str) -> None:
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_IDS_KEY, [record.id for record in records])
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, _HISTORY_FILTER_KEY, filter_key)


def _set_screen(context: RouterContext, screen_id: str) -> None:
    current = state.get_current_screen(context.event.platform_user_id, context.event.chat_id)
    if current != screen_id:
        state.push_screen(context.event.platform_user_id, context.event.chat_id, current)
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, screen_id)


def _can_view(context: RouterContext) -> bool:
    if is_protected_developer(
        context.event.platform_user_id,
        getenv("DEV_MAX_USER_ID"),
        max_user_id=context.event.max_user_id,
    ):
        return True
    return can_view_notification_history(_actor_role(context))


def _actor_role(context: RouterContext) -> str:
    platform_user_id = context.event.platform_user_id
    if platform_user_id is None:
        return ROLE_USER
    return StaffRolesRepository(_database_path()).get_highest_role(platform_user_id, platform=PLATFORM_MAX)


def _repository() -> NotificationHistoryRepository:
    return NotificationHistoryRepository(_database_path())


def _database_path() -> str:
    return getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH).strip() or DEFAULT_DATABASE_PATH


async def _send_stale_history(context: RouterContext) -> None:
    await _answer_callback_if_needed(context)
    await context.send_text(_STALE_TEXT, keyboard=notification_history_keyboard([], back_payload=ADMIN_NOTIFICATION_HISTORY_PAYLOAD))


def _format_dt(value: object | None) -> str:
    return CompanyTimeService().format_datetime(value)


def _short_error_marker(record: NotificationHistoryRecord) -> str:
    if _status_key(record) not in _FAILED_STATUSES and not record.delivery_error_code and not record.delivery_error_message:
        return ""
    reason = _actionable_reason(record)
    return f"\nПричина: {reason}" if reason else "\nПричина: причина не указана"


def _format_diagnostics(record: NotificationHistoryRecord) -> str:
    if not _has_diagnostics(record):
        return "Диагностика по этой отправке недоступна 🙏"
    lines = [
        f"🔎 Диагностика уведомления #{record.id}",
        "",
        f"Статус: {_effective_status_label(record)}",
        f"Причина: {_actionable_reason(record)}",
        f"HTTP статус: {_safe_value(record.delivery_status_code)}",
        f"Категория ошибки: {_safe_value(record.delivery_error_code)}",
        f"Ошибка: {_error_text(record)}",
        f"trace_id: {_trace_id(record) or '—'}",
        "",
        "Уведомление не отправлялось повторно — данные только перечитаны из истории доставки ✅",
    ]
    return "\n".join(lines)[:3900]


def _has_diagnostics(record: NotificationHistoryRecord) -> bool:
    return any(
        (
            record.delivery_status_code is not None,
            bool(record.delivery_error_code),
            bool(record.delivery_error_message),
            bool(_trace_id(record)),
            _status_key(record) in _FAILED_STATUSES,
            record.is_blocked,
            record.is_stopped,
        )
    )


def _status_key(record: NotificationHistoryRecord) -> str:
    if record.is_blocked:
        return "blocked"
    if record.is_stopped:
        return "stopped"
    return str(record.status or "").strip().lower()


def _actionable_reason(record: NotificationHistoryRecord) -> str:
    status = _status_key(record)
    raw = " ".join(
        part
        for part in (
            str(record.delivery_error_code or ""),
            str(record.delivery_error_message or ""),
            str(record.delivery_status_code or ""),
        )
        if part
    ).lower()
    if status == "blocked" or "blocked" in raw or "forbidden" in raw:
        return "пользователь заблокировал бота"
    if status == "stopped" or "stopped" in raw:
        return "пользователь остановил бота или отключил уведомления"
    if status == "rate_limited" or "429" in raw or "rate" in raw:
        return "сработал лимит отправки"
    if "401" in raw or "403" in raw or "auth" in raw:
        return "ошибка авторизации доставки"
    if "400" in raw or "404" in raw or "recipient" in raw or "chat" in raw:
        return "некорректный получатель или чат"
    if "timeout" in raw or "network" in raw or "503" in raw or "500" in raw:
        return "сетевая или серверная ошибка"
    if record.delivery_error_message:
        return _sanitize_error_message(record.delivery_error_message)
    if status in _FAILED_STATUSES:
        return "причина не указана"
    return "ошибок нет"


def _trace_id(record: NotificationHistoryRecord) -> str | None:
    if record.metadata_json:
        try:
            metadata = json.loads(record.metadata_json)
        except json.JSONDecodeError:
            metadata = None
        if isinstance(metadata, dict):
            for key in ("trace_id", "trace", "request_id"):
                value = metadata.get(key)
                if value:
                    return str(value)[:80]
    source = " ".join(str(part or "") for part in (record.delivery_error_message, record.metadata_json))
    match = re.search(r'(?:trace[_-]?id|trace)[\s"\']*[:=][\s"\']*([a-zA-Z0-9_.:-]{4,80})', source, flags=re.IGNORECASE)
    return match.group(1) if match else None


async def _send_no_access(context: RouterContext) -> None:
    await _answer_callback_if_needed(context, _NO_ACCESS_TEXT)
    await context.send_text(_NO_ACCESS_TEXT)


async def _answer_callback_if_needed(context: RouterContext, notification: str | None = None) -> None:
    if context.event.callback_id:
        await context.answer_callback()


def _safe_value(value: object | None) -> str:
    if value is None:
        return "—"
    clean = str(value).strip()
    return clean or "—"
