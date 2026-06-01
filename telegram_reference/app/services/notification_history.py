from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.db.sqlite import fetchall
from app.services.notification_delivery import get_notification_delivery_type

logger = logging.getLogger(__name__)

TYPE_LABELS = {
    "manual_broadcast": "✉️ Ручная рассылка",
    "post_visit_rating": "⭐️ Оценка после визита",
    "cancellation_recovery": "❌ Возврат после отмены",
    "lost_client": "😔 Потерянный клиент",
    "birthday": "🎂 День рождения",
    "repeat_visit": "🔁 Повторный визит",
    "booking_confirmation_2d": "✅ Подтверждение записи (2 дня)",
    "booking_reminder_2h": "⏰ Напоминание о записи (2 часа)",
}

STATUS_LABELS = {
    "sent": "✅ Отправлено",
    "failed": "❌ Ошибка",
    "blocked": "🚫 Заблокировал бота",
    "pending": "⏳ Ожидает отправки",
    "skipped": "⏭ Пропущено",
    "delivering": "📨 Доставляется",
}
DELIVERY_LABELS = {"white": "⚪ Сервисное", "green": "🟢 Маркетинговое"}


def map_event_type_to_label(event_type: str) -> str:
    return TYPE_LABELS.get(event_type, "📩 Уведомление")


def map_event_status_to_label(status: str, is_test: bool = False) -> str:
    if is_test:
        return "🧪 Тест"
    if status.startswith("skipped"):
        return "⏭ Пропущено"
    return STATUS_LABELS.get(status, "⏳ Ожидает отправки")


def map_error_summary(raw: str | None, status: str | None = None) -> str | None:
    if status == "blocked":
        return "пользователь заблокировал бота"
    raw_text = str(raw or "").strip()
    status_text = str(status or "").strip()
    if not raw_text and not status_text.startswith("skipped"):
        return None

    reason_key = raw_text.lower().strip()
    status_key = status_text.lower().strip()
    reason_map = {
        "no_telegram_mapping": "нет Telegram-связки с клиентом",
        "disabled": "автоматизация выключена",
        "skipped_has_future_booking": "у клиента уже есть будущая запись",
        "skipped_has_new_booking": "у клиента уже есть будущая запись",
        "anti_spam": "сработал антиспам",
        "antiflood": "сработал антиспам",
        "duplicate": "дубликат уведомления",
        "skipped_antispam": "сработал антиспам",
        "skipped_duplicate": "дубликат уведомления",
        "skipped_disabled": "автоматизация выключена",
    }
    for key in (reason_key, status_key):
        if key in reason_map:
            return reason_map[key]
    if "anti_spam" in reason_key or "antiflood" in reason_key or "antispam" in reason_key:
        return "сработал антиспам"
    if "duplicate" in reason_key:
        return "дубликат уведомления"
    if "future_booking" in reason_key or "new_booking" in reason_key:
        return "у клиента уже есть будущая запись"
    if "telegram_mapping" in reason_key:
        return "нет Telegram-связки с клиентом"
    if "disabled" in reason_key:
        return "автоматизация выключена"

    if not raw_text:
        return "причина не указана"
    low = raw_text.lower()
    if "traceback" in low or "\n" in raw_text or "file \"" in low:
        return "техническая ошибка отправки"
    if "forbidden" in low or "blocked" in low:
        return "пользователь заблокировал бота"
    if "telegram" in low and "id" in low:
        return "нет Telegram ID"
    if "unsubscribe" in low:
        return "клиент отписался от маркетинга"
    if "working" in low or "time" in low:
        return "отправка отложена из-за нерабочего времени"
    if "yclients" in low:
        return "ошибка синхронизации с YClients"
    return "причина не указана"


def _fmt_dt(iso_value: str | None, tz_name: str | None) -> str:
    if not iso_value:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        logger.warning("history_timezone_fallback tz=%s", tz_name)
        tz = ZoneInfo("UTC")
    return dt.astimezone(tz).strftime("%d.%m.%Y в %H:%M")


def _client_text(row: dict) -> str:
    username = str(row.get("client_username") or "").strip().lstrip("@")
    if username:
        return f"клиенту @{username}"
    return "клиенту без username"


def _sender_text(row: dict) -> str:
    role = str(row.get("launched_by_role") or "").strip()
    name = str(row.get("launched_by_name") or "").strip()
    if role == "developer":
        prefix = "Разработчик"
    elif role in {"admin", "manager"}:
        prefix = "Администратор"
    else:
        prefix = "Сотрудник"
    return f"{prefix} {name}" if name else prefix


def _manual_broadcast_text(row: dict) -> str:
    audience = str(row.get("audience_name") or "аудитория").strip()
    sent = int(row.get("sent_count") or 0)
    errors = int(row.get("failed_count") or 0) + int(row.get("blocked_count") or 0)
    skipped = int(row.get("skipped_count") or 0)
    if bool(row.get("is_test")):
        return f"{_sender_text(row)} отправил тестовую рассылку себе {row.get('display_time')}."
    tail = f"Отправлено: {sent}, ошибок: {errors}"
    if skipped:
        tail += f", пропущено: {skipped}"
    return f"{_sender_text(row)} отправил рассылку по аудитории «{audience}» {row.get('display_time')}. {tail}."


def _event_text(row: dict) -> str:
    when = row.get("display_time") or "—"
    client = _client_text(row)
    event_type = str(row.get("event_type") or "")
    if event_type == "manual_broadcast":
        return _manual_broadcast_text(row)
    if event_type in {"booking_confirmation_2d", "booking_reminder_2h"}:
        return f"Бот отправил напоминание о записи {client} {when}."
    if event_type == "birthday":
        return f"Бот отправил поздравление с днём рождения {client} {when}."
    if event_type == "lost_client":
        return f"Бот отправил сообщение потерянному клиенту {client} {when}."
    if event_type == "repeat_visit":
        return f"Бот отправил напоминание о повторном визите {client} {when}."
    if event_type == "post_visit_rating":
        return f"Бот отправил просьбу оценить визит {client} {when}."
    if event_type == "cancellation_recovery":
        return f"Бот отправил сообщение для возврата после отмены записи {client} {when}."
    return f"Бот отправил уведомление {client} {when}."


async def get_notification_history(filter_type: str | None, page: int, page_size: int, *, include_test: bool, default_timezone: str = "Europe/Moscow") -> list[dict]:
    normalized_filter = None if filter_type in (None, "all") else str(filter_type)
    allowed_filters = {
        "manual_broadcast",
        "post_visit_rating",
        "cancellation_recovery",
        "lost_client",
        "birthday",
        "repeat_visit",
        "booking_confirmation_2d",
        "booking_reminder_2h",
    }
    if normalized_filter and normalized_filter not in allowed_filters:
        return []

    columns = """
        source_table, source_id, event_type, recipient_tg_id, yclients_client_id,
        client_name, client_username, client_phone, status, error_summary,
        message_text_preview, photo_file_id, created_at_utc, branch_timezone,
        launched_by_tg_id, launched_by_role, launched_by_name, audience_key, audience_name,
        sent_count, failed_count, blocked_count, skipped_count, is_test
    """
    sql = f"""
        WITH notification_history({columns}) AS (
            SELECT 'broadcast_campaigns' AS source_table,
                   c.id AS source_id,
                   'manual_broadcast' AS event_type,
                   NULL AS recipient_tg_id,
                   NULL AS yclients_client_id,
                   NULL AS client_name,
                   NULL AS client_username,
                   NULL AS client_phone,
                   c.status AS status,
                   COALESCE(
                       c.error_summary,
                       (SELECT rl.error_summary
                        FROM broadcast_recipient_logs rl
                        WHERE rl.campaign_id = c.id
                          AND COALESCE(rl.error_summary, '') != ''
                        ORDER BY rl.created_at_utc DESC, rl.id DESC
                        LIMIT 1)
                   ) AS error_summary,
                   c.text AS message_text_preview,
                   c.photo_file_id AS photo_file_id,
                   COALESCE(c.sent_at_utc,c.created_at_utc) AS created_at_utc,
                   COALESCE(c.branch_timezone, ?) AS branch_timezone,
                   c.created_by_tg_id AS launched_by_tg_id,
                   c.created_by_role AS launched_by_role,
                   su.name AS launched_by_name,
                   c.audience_key AS audience_key,
                   c.audience_name AS audience_name,
                   c.sent_count AS sent_count,
                   c.failed_count AS failed_count,
                   c.blocked_count AS blocked_count,
                   c.skipped_count AS skipped_count,
                   c.is_test AS is_test
            FROM broadcast_campaigns c
            LEFT JOIN users su ON su.user_id=c.created_by_tg_id

            UNION ALL

            SELECT 'post_visit_feedback_events' AS source_table,
                   e.id AS source_id,
                   'post_visit_rating' AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   e.client_name AS client_name,
                   u.username AS client_username,
                   e.client_phone AS client_phone,
                   e.status AS status,
                   NULL AS error_summary,
                   NULL AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   COALESCE(e.branch_timezone, ?) AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   e.is_test AS is_test
            FROM post_visit_feedback_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id

            UNION ALL

            SELECT 'cancellation_recovery_events' AS source_table,
                   e.id AS source_id,
                   'cancellation_recovery' AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   u.name AS client_name,
                   u.username AS client_username,
                   COALESCE(u.phone_e164,u.phone,'') AS client_phone,
                   e.status AS status,
                   e.error_summary AS error_summary,
                   NULL AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   COALESCE(e.branch_timezone, ?) AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   e.is_test AS is_test
            FROM cancellation_recovery_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id

            UNION ALL

            SELECT 'lost_client_events' AS source_table,
                   e.id AS source_id,
                   'lost_client' AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   u.name AS client_name,
                   u.username AS client_username,
                   COALESCE(u.phone_e164,u.phone,'') AS client_phone,
                   e.status AS status,
                   e.error_summary AS error_summary,
                   NULL AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   ? AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   e.is_test AS is_test
            FROM lost_client_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id

            UNION ALL

            SELECT 'birthday_funnel_events' AS source_table,
                   e.id AS source_id,
                   'birthday' AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   u.name AS client_name,
                   u.username AS client_username,
                   COALESCE(u.phone_e164,u.phone,'') AS client_phone,
                   e.status AS status,
                   e.error_summary AS error_summary,
                   NULL AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   COALESCE(e.branch_timezone, ?) AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   e.is_test AS is_test
            FROM birthday_funnel_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id

            UNION ALL

            SELECT 'repeat_visit_events' AS source_table,
                   e.id AS source_id,
                   'repeat_visit' AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   u.name AS client_name,
                   u.username AS client_username,
                   COALESCE(u.phone_e164,u.phone,'') AS client_phone,
                   e.status AS status,
                   e.error_summary AS error_summary,
                   e.selected_template_text AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   COALESCE(e.branch_timezone, ?) AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   e.is_test AS is_test
            FROM repeat_visit_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id

            UNION ALL

            SELECT 'booking_reminder_events' AS source_table,
                   e.id AS source_id,
                   CASE WHEN e.reminder_type='confirm_2d' THEN 'booking_confirmation_2d' ELSE 'booking_reminder_2h' END AS event_type,
                   e.client_tg_id AS recipient_tg_id,
                   e.yclients_client_id AS yclients_client_id,
                   u.name AS client_name,
                   u.username AS client_username,
                   COALESCE(e.client_phone,u.phone_e164,u.phone,'') AS client_phone,
                   e.status AS status,
                   e.error AS error_summary,
                   NULL AS message_text_preview,
                   NULL AS photo_file_id,
                   COALESCE(e.sent_at_utc,e.created_at_utc) AS created_at_utc,
                   COALESCE(e.branch_timezone, ?) AS branch_timezone,
                   NULL AS launched_by_tg_id,
                   NULL AS launched_by_role,
                   NULL AS launched_by_name,
                   NULL AS audience_key,
                   NULL AS audience_name,
                   NULL AS sent_count,
                   NULL AS failed_count,
                   NULL AS blocked_count,
                   NULL AS skipped_count,
                   0 AS is_test
            FROM booking_reminder_events e
            LEFT JOIN users u ON u.user_id=e.client_tg_id
        )
        SELECT source_table, source_id, event_type, recipient_tg_id, yclients_client_id,
               client_name, client_username, client_phone, status, error_summary,
               message_text_preview, photo_file_id, created_at_utc, branch_timezone,
               launched_by_tg_id, launched_by_role, launched_by_name, audience_key, audience_name,
               sent_count, failed_count, blocked_count, skipped_count, is_test
        FROM notification_history
        WHERE (? IS NULL OR event_type = ?)
          AND (? = 1 OR COALESCE(is_test,0) = 0)
        ORDER BY created_at_utc DESC
        LIMIT ? OFFSET ?
    """
    params = [default_timezone] * 7 + [normalized_filter, normalized_filter, 1 if include_test else 0, page_size, max(page - 1, 0) * page_size]
    try:
        rows = await fetchall(sql, tuple(params))
    except Exception:
        logger.exception("notification_history_query_failed filter=%s", filter_type)
        return []
    out=[]
    for r in rows:
        d=dict(r)
        delivery_type = get_notification_delivery_type(str(d.get("event_type") or ""))
        d["delivery_type"] = delivery_type
        d["delivery_type_label"] = DELIVERY_LABELS.get(delivery_type, "🟢 Маркетинговое")
        d["display_time"]=_fmt_dt(d.get("created_at_utc"), d.get("branch_timezone") or default_timezone)
        d["human_text"] = _event_text(d)
        out.append(d)
    return out
