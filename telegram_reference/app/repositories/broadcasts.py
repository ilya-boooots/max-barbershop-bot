from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone, time as dt_time
import time
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
import logging

from app.db.sqlite import execute, fetchall, fetchone
from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import get_company
from app.repositories.yclients_settings import get_yclients_settings
from app.services.company_time import resolve_company_timezone
from app.services.client_segments import SEGMENTS, segment_service


logger = logging.getLogger(__name__)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_daily_schedule(schedule: str) -> tuple[dt_time, dt_time] | None:
    import re
    m = re.search(r'(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})', schedule)
    if not m:
        return None
    start = datetime.strptime(m.group(1), '%H:%M').time()
    end = datetime.strptime(m.group(2), '%H:%M').time()
    return start, end


def _next_working_start_local(now_local: datetime, start: dt_time, end: dt_time) -> datetime:
    if start <= now_local.time() <= end:
        return now_local
    if now_local.time() < start:
        return now_local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    next_day = now_local + timedelta(days=1)
    return next_day.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)


async def get_audience_user_ids(audience: str, actor_id: int) -> list[int]:
    if audience == "self_test":
        return [actor_id]
    if audience == "all":
        rows = await fetchall("SELECT user_id FROM users WHERE user_id IS NOT NULL")
    elif audience == "active_30":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rows = await fetchall("SELECT user_id FROM users WHERE user_id IS NOT NULL AND COALESCE(last_seen_at,last_activity_ts_utc,created_at)>=?", (cutoff,))
    elif audience == "no_future_booking":
        rows = await fetchall("SELECT u.user_id FROM users u WHERE u.user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.user_id=u.user_id AND datetime(b.date||' '||b.time) > datetime('now'))")
    elif audience in {"inactive_30", "inactive_60", "inactive_90"}:
        days = int(audience.split("_")[-1])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = await fetchall("SELECT user_id FROM users WHERE user_id IS NOT NULL AND COALESCE(last_seen_at,last_activity_ts_utc,created_at) < ?", (cutoff,))
    else:
        rows = []
    return [int(r["user_id"]) for r in rows]


def audience_name(audience_key: str) -> str:
    mapping = {
        "all_clients": "👥 Все клиенты",
        "active_30": "🔥 Активные за 30 дней",
        "lost_30": "😴 Потерянные 30 дней",
        "lost_60": "😴 Потерянные 60 дней",
        "lost_90": "😴 Потерянные 90 дней",
        "no_future_booking": "📅 Без будущей записи",
        "self_test": "🧪 Отправить себе",
        "send_to_self": "🧪 Отправить себе",
        "by_service_category": "✂️ По категории услуг",
    }
    return mapping.get(audience_key, audience_key)


def _phone_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def _map_yclients_clients_to_telegram(
    clients: list[dict[str, Any]],
    *,
    actor_id: int,
    audience_key: str,
) -> list[dict[str, Any]]:
    yc_ids = {str(c.get("yclients_client_id") or c.get("id") or c.get("client_id")).strip() for c in clients if str(c.get("yclients_client_id") or c.get("id") or c.get("client_id") or "").strip()}
    phones_raw = {str(c.get("phone") or "").strip() for c in clients if str(c.get("phone") or "").strip()}
    phone_digits = {_phone_digits(phone) for phone in phones_raw if _phone_digits(phone)}
    if not yc_ids and not phones_raw and not phone_digits:
        for c in clients:
            logger.info("broadcast_recipient_skipped actor_tg_id=%s audience_key=%s yclients_client_id=%s phone=%s skip_reason=%s", actor_id, audience_key, c.get("yclients_client_id"), c.get("phone"), "нет Telegram-связки")
        return []

    where_parts: list[str] = []
    params: list[Any] = []
    if yc_ids:
        where_parts.append(f"CAST(u.yclients_client_id AS TEXT) IN ({','.join(['?'] * len(yc_ids))})")
        params.extend(sorted(yc_ids))
    if phones_raw:
        placeholders = ",".join(["?"] * len(phones_raw))
        where_parts.append(f"(u.phone IN ({placeholders}) OR u.phone_e164 IN ({placeholders}))")
        params.extend(sorted(phones_raw))
        params.extend(sorted(phones_raw))
    if phone_digits:
        placeholders = ",".join(["?"] * len(phone_digits))
        where_parts.append(f"(u.phone_digits IN ({placeholders}) OR u.phone_ru_7 IN ({placeholders}) OR u.phone_ru_8 IN ({placeholders}))")
        sorted_digits = sorted(phone_digits)
        params.extend(sorted_digits)
        params.extend(sorted_digits)
        params.extend(sorted_digits)

    rows = await fetchall(
        f"""
        SELECT u.user_id, u.user_id AS tg_id, u.user_id AS local_user_id, u.yclients_client_id, u.name, u.phone,
               u.phone_e164, u.phone_digits, u.phone_ru_7, u.phone_ru_8,
               COALESCE(mp.marketing_unsubscribed, 0) AS marketing_unsubscribed,
               0 AS blocked, COALESCE(u.notifications_enabled,1) AS notifications_enabled
        FROM users u
        LEFT JOIN client_marketing_preferences mp ON mp.client_tg_id = u.user_id
        WHERE u.user_id IS NOT NULL AND COALESCE(u.is_registered,0)=1 AND ({' OR '.join(where_parts)})
        """,
        tuple(params),
    )
    by_yc: dict[str, dict[str, Any]] = {}
    by_phone: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = {**dict(row)}
        yc_id = str(item.get("yclients_client_id") or "").strip()
        if yc_id and yc_id not in by_yc:
            by_yc[yc_id] = item
        for value in (item.get("phone"), item.get("phone_e164"), item.get("phone_digits"), item.get("phone_ru_7"), item.get("phone_ru_8")):
            digits = _phone_digits(value)
            if digits and digits not in by_phone:
                by_phone[digits] = item

    recipients: list[dict[str, Any]] = []
    seen_tg: set[int] = set()
    for client in clients:
        yc_id = str(client.get("yclients_client_id") or client.get("id") or client.get("client_id") or "").strip()
        phone = _phone_digits(client.get("phone"))
        row = by_yc.get(yc_id) if yc_id else None
        if row is None and phone:
            row = by_phone.get(phone)
        if row is None:
            logger.info("broadcast_recipient_skipped actor_tg_id=%s audience_key=%s yclients_client_id=%s phone=%s skip_reason=%s", actor_id, audience_key, yc_id or None, client.get("phone"), "нет Telegram-связки")
            continue
        tg_id = int(row.get("tg_id") or row.get("user_id"))
        if tg_id in seen_tg:
            continue
        seen_tg.add(tg_id)
        recipients.append(row)
    logger.info("broadcast_yclients_telegram_mapping_finished actor_tg_id=%s audience_key=%s business_clients_count=%s telegram_mapped_recipients_count=%s skipped_no_telegram_mapping_count=%s mapping_table=users mapping_columns=yclients_client_id,phone,phone_e164,phone_digits,phone_ru_7,phone_ru_8,user_id", actor_id, audience_key, len(clients), len(recipients), max(0, len(clients) - len(recipients)))
    return recipients



async def resolve_yclients_audience_with_mapping(
    audience_key: str,
    actor_id: int,
    payload: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Resolve business audience in YClients and map it to Telegram recipients."""
    if audience_key == "all_clients":
        clients, diag = await segment_service.resolve_all_clients_from_yclients(actor_tg_id=actor_id)
        endpoint = "/api/v1/clients/{company_id}"
    elif audience_key == "active_30":
        clients, diag = await segment_service.resolve_active_clients_from_yclients(30, actor_tg_id=actor_id)
        endpoint = "/api/v1/records/{company_id}"
    elif audience_key == "no_future_booking":
        clients, diag = await segment_service.resolve_no_future_booking_clients_from_yclients(actor_tg_id=actor_id)
        endpoint = "/api/v1/clients/{company_id} + /api/v1/records/{company_id}"
    elif audience_key == "by_master":
        master_id = str(payload or "").strip()
        if not master_id:
            return [], [], {"error_summary": "master_id_missing"}
        clients, diag = await segment_service.fetch_master_segment_clients(master_id, actor_tg_id=actor_id)
        endpoint = "/api/v1/records/{company_id}"
        diag = {**diag, "master_id": master_id}
    else:
        return [], [], {"error_summary": "unsupported_yclients_audience"}

    recipients = await _map_yclients_clients_to_telegram(clients, actor_id=actor_id, audience_key=audience_key)
    diag = {**diag, "endpoint": endpoint, "business_clients_count": len(clients), "telegram_recipients_count": len(recipients)}
    return clients, recipients, diag

async def resolve_one_time_audience(audience_key: str, actor_id: int, payload: str | None = None) -> list[dict[str, Any]]:
    started = time.perf_counter()
    logger.info("broadcast_audience_resolve_started actor_tg_id=%s audience_key=%s", actor_id, audience_key)
    if audience_key in {"self_test", "send_to_self"}:
        logger.info("broadcast_audience_resolve_local_db_finished actor_tg_id=%s audience_key=%s count=1 source_used=self elapsed_ms=%s", actor_id, audience_key, int((time.perf_counter() - started) * 1000))
        return [{"user_id": actor_id, "tg_id": actor_id, "local_user_id": actor_id, "yclients_client_id": None, "name": None, "phone": None, "marketing_unsubscribed": False, "blocked": False, "notifications_enabled": 1}]

    segment_map = {
        "all_clients": "all_clients",
        "active_30": "active_30",
        "lost_30": "inactive_30",
        "lost_60": "inactive_60",
        "lost_90": "inactive_90",
        "no_future_booking": "no_future_booking",
        "cancelled_recent": "cancelled_30",
        "birthday_soon": "birthday_soon",
    }
    segment_key = segment_map.get(audience_key)
    if segment_key in {"all_clients", "active_30", "no_future_booking"} or audience_key == "by_master":
        clients, recipients, diag = await resolve_yclients_audience_with_mapping(audience_key, actor_id, payload=payload)
        logger.info(
            "broadcast_audience_resolve_yclients_finished actor_tg_id=%s audience_key=%s company_id=%s endpoint=%s business_clients_count=%s telegram_mapped_recipients_count=%s elapsed_ms=%s",
            actor_id,
            audience_key,
            diag.get("company_id"),
            diag.get("endpoint"),
            len(clients),
            len(recipients),
            int((time.perf_counter() - started) * 1000),
        )
        return recipients
    if audience_key == "by_service_category":
        raw = str(payload or "")
        category_id, category_name = (raw.split("|", 1) + [""])[:2]
        if not category_id:
            return []
        if not category_name:
            categories, _ = await segment_service.list_service_categories(actor_tg_id=actor_id)
            category_name = next((item["name"] for item in categories if str(item.get("id")) == str(category_id)), "")
        clients, diag = await segment_service.fetch_service_category_segment_clients(category_id, category_name, actor_tg_id=actor_id)
        yc_ids = {str(c.get("yclients_client_id")) for c in clients if c.get("yclients_client_id")}
        phones = {str(c.get("phone")) for c in clients if c.get("phone")}
        if not yc_ids and not phones:
            logger.info("service_category_broadcast_recipients_resolved actor_tg_id=%s category_id=%s category_name=%s company_id=%s records_count=%s unique_yclients_clients_count=%s telegram_mapped_recipients_count=0", actor_id, category_id, category_name, diag.get("company_id"), diag.get("records_count"), len(clients))
            return []
        where_parts = []
        params: list[Any] = []
        if yc_ids:
            where_parts.append(f"CAST(u.yclients_client_id AS TEXT) IN ({','.join(['?'] * len(yc_ids))})")
            params.extend(sorted(yc_ids))
        if phones:
            where_parts.append(f"u.phone IN ({','.join(['?'] * len(phones))})")
            params.extend(sorted(phones))
        rows = await fetchall(
            f"""
            SELECT u.user_id, u.user_id AS tg_id, u.user_id AS local_user_id, u.yclients_client_id, u.name, u.phone,
                   COALESCE(mp.marketing_unsubscribed, 0) AS marketing_unsubscribed,
                   0 AS blocked, COALESCE(u.notifications_enabled,1) AS notifications_enabled
            FROM users u
            LEFT JOIN client_marketing_preferences mp ON mp.client_tg_id = u.user_id
            WHERE u.user_id IS NOT NULL AND COALESCE(u.is_registered,0)=1 AND ({' OR '.join(where_parts)})
            """,
            tuple(params),
        )
        recipients = [{**dict(r)} for r in rows]
        logger.info("service_category_broadcast_recipients_resolved actor_tg_id=%s category_id=%s category_name=%s company_id=%s service_ids=%s records_count=%s unique_yclients_clients_count=%s telegram_mapped_recipients_count=%s", actor_id, category_id, category_name, diag.get("company_id"), diag.get("service_ids"), diag.get("records_count"), len(clients), len(recipients))
        return recipients
    if audience_key == "birthday_soon":
        logger.info("birthday_audience_mapping_started actor_tg_id=%s audience_key=%s mapping_table=users mapping_columns=user_id,yclients_client_id,phone", actor_id, audience_key)
        clients, diag = await segment_service.resolve_birthday_soon_clients(actor_tg_id=actor_id)
        yc_ids = {str(c.get("yclients_client_id")) for c in clients if c.get("yclients_client_id")}
        phones = {str(c.get("phone")) for c in clients if c.get("phone")}
        tg_ids: set[int] = set()
        for client in clients:
            raw_tg_id = client.get("tg_id")
            if raw_tg_id in (None, ""):
                continue
            try:
                tg_ids.add(int(str(raw_tg_id).strip()))
            except (TypeError, ValueError):
                continue
        where_parts = []
        params: list[Any] = []
        if yc_ids:
            where_parts.append(f"CAST(u.yclients_client_id AS TEXT) IN ({','.join(['?'] * len(yc_ids))})")
            params.extend(sorted(yc_ids))
        if phones:
            where_parts.append(f"u.phone IN ({','.join(['?'] * len(phones))})")
            params.extend(sorted(phones))
        if tg_ids:
            where_parts.append(f"u.user_id IN ({','.join(['?'] * len(tg_ids))})")
            params.extend(sorted(tg_ids))
        if not where_parts:
            logger.info("birthday_audience_mapping_finished actor_tg_id=%s audience_key=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s birthday_clients_matched=%s local_clients_checked=%s unique_birthday_clients_count=%s telegram_mapped_recipients_count=0 mapping_table=users mapping_columns=user_id,yclients_client_id,phone", actor_id, audience_key, None, diag.get("branch_timezone"), diag.get("date_from"), diag.get("date_to"), diag.get("yclients_clients_checked"), len(clients), diag.get("local_clients_checked"), len(clients))
            return []
        rows = await fetchall(
            f"""
            SELECT u.user_id, u.user_id AS tg_id, u.user_id AS local_user_id, u.yclients_client_id, u.name, u.phone,
                   COALESCE(mp.marketing_unsubscribed, 0) AS marketing_unsubscribed,
                   0 AS blocked, COALESCE(u.notifications_enabled,1) AS notifications_enabled
            FROM users u
            LEFT JOIN client_marketing_preferences mp ON mp.client_tg_id = u.user_id
            WHERE u.user_id IS NOT NULL AND COALESCE(u.is_registered,0)=1 AND ({' OR '.join(where_parts)})
            """,
            tuple(params),
        )
        recipients = [{**dict(r)} for r in rows]
        logger.info("birthday_audience_mapping_finished actor_tg_id=%s audience_key=%s company_id=%s branch_timezone=%s today_branch_date=%s window_end_branch_date=%s yclients_clients_checked=%s birthday_clients_matched=%s local_clients_checked=%s unique_birthday_clients_count=%s telegram_mapped_recipients_count=%s mapping_table=users mapping_columns=user_id,yclients_client_id,phone", actor_id, audience_key, None, diag.get("branch_timezone"), diag.get("date_from"), diag.get("date_to"), diag.get("yclients_clients_checked"), len(clients), diag.get("local_clients_checked"), len(clients), len(recipients))
        return recipients
    if not segment_key:
        return []
    if segment_key in {"inactive_30", "inactive_60", "inactive_90"}:
        days = int(segment_key.split("_")[-1])
        clients, diag = await segment_service.resolve_lost_clients_from_yclients(days, actor_tg_id=actor_id)
        recipients = await _map_yclients_clients_to_telegram(clients, actor_id=actor_id, audience_key=audience_key)
        logger.info("lost_clients_broadcast_recipients_resolved actor_tg_id=%s company_id=%s days=%s endpoint=%s method=%s date_from=%s date_to=%s records_count=%s unique_yclients_clients_count=%s telegram_mapped_recipients_count=%s", actor_id, diag.get("company_id"), days, "/api/v1/records/{company_id}", "GET", diag.get("date_from"), diag.get("date_to"), diag.get("records_count"), len(clients), len(recipients))
        return recipients
    logger.info("broadcast_audience_auto_refresh_started actor_tg_id=%s audience_key=%s segment_key=%s", actor_id, audience_key, segment_key)
    try:
        fresh_row, refreshed = await segment_service.ensure_segment_fresh(segment_key)
        logger.info(
            "broadcast_audience_auto_refresh_finished actor_tg_id=%s audience_key=%s segment_key=%s refreshed=%s new_count=%s source_used=%s calculated_at_utc=%s",
            actor_id,
            audience_key,
            segment_key,
            refreshed,
            int(fresh_row["client_count"]) if fresh_row else None,
            "local_db" if refreshed else "cache",
            fresh_row["calculated_at_utc"] if fresh_row else None,
        )
    except Exception as exc:
        logger.exception("broadcast_audience_auto_refresh_failed actor_tg_id=%s audience_key=%s segment_key=%s error_summary=%s", actor_id, audience_key, segment_key, str(exc)[:200])

    cache_count = None
    try:
        row = await fetchone("SELECT client_count FROM client_segment_cache WHERE segment_key=? ORDER BY updated_at_utc DESC LIMIT 1", (segment_key,))
        cache_count = int(row["client_count"]) if row else None
        if cache_count is not None:
            logger.info("broadcast_audience_resolve_cache_hit actor_tg_id=%s audience_key=%s count=%s", actor_id, audience_key, cache_count)
        else:
            logger.info("broadcast_audience_resolve_cache_miss actor_tg_id=%s audience_key=%s", actor_id, audience_key)
    except Exception:
        logger.exception("broadcast_audience_resolve_cache_miss actor_tg_id=%s audience_key=%s", actor_id, audience_key)

    logger.info("broadcast_audience_resolve_local_db_started actor_tg_id=%s audience_key=%s", actor_id, audience_key)
    now_utc = datetime.now(timezone.utc)
    now_local_sql = now_utc.strftime("%Y-%m-%d %H:%M")
    base_sql = """
        SELECT u.user_id, u.user_id AS tg_id, u.user_id AS local_user_id, u.yclients_client_id, u.name, u.phone,
               COALESCE(mp.marketing_unsubscribed, 0) AS marketing_unsubscribed,
               mp.unsubscribed_at_utc AS unsubscribed_at_utc,
               mp.unsubscribe_source AS unsubscribe_source,
               0 AS blocked,
               COALESCE(u.notifications_enabled,1) AS notifications_enabled,
               COALESCE(u.last_seen_at,u.last_activity_ts_utc,u.created_at) AS last_activity
        FROM users u
        LEFT JOIN client_marketing_preferences mp ON mp.client_tg_id = u.user_id
        WHERE u.user_id IS NOT NULL AND COALESCE(u.is_registered,0)=1
    """
    params: list[Any] = []
    if segment_key == "active_30":
        cutoff = (now_utc - timedelta(days=30)).isoformat()
        base_sql += " AND COALESCE(u.last_seen_at,u.last_activity_ts_utc,u.created_at)>=?"
        params.append(cutoff)
    elif segment_key in {"inactive_30", "inactive_60", "inactive_90"}:
        days = int(segment_key.split("_")[-1])
        cutoff = (now_utc - timedelta(days=days)).isoformat()
        base_sql += " AND COALESCE(u.last_seen_at,u.last_activity_ts_utc,u.created_at)<?"
        params.append(cutoff)
        base_sql += " AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.user_id=u.user_id AND datetime(b.date || ' ' || b.time)>datetime(?) AND b.status IN ('pending','confirmed','booked','new'))"
        params.append(now_local_sql)
    elif segment_key == "no_future_booking":
        base_sql += " AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.user_id=u.user_id AND datetime(b.date || ' ' || b.time)>datetime(?) AND b.status IN ('pending','confirmed','booked','new'))"
        params.append(now_local_sql)
    elif segment_key == "cancelled_30":
        cutoff = (now_utc - timedelta(days=30)).isoformat()
        base_sql += " AND EXISTS (SELECT 1 FROM cancellation_recovery_events cre WHERE cre.client_tg_id=u.user_id AND cre.cancellation_detected_at_utc>=? AND COALESCE(cre.is_test,0)=0)"
        params.append(cutoff)
        base_sql += " AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.user_id=u.user_id AND datetime(b.date || ' ' || b.time)>datetime(?) AND b.status IN ('pending','confirmed','booked','new'))"
        params.append(now_local_sql)

    rows = await fetchall(base_sql, tuple(params))
    recipients = [{**dict(r)} for r in rows]
    logger.info("broadcast_audience_resolve_local_db_finished actor_tg_id=%s audience_key=%s count=%s source_used=local_db elapsed_ms=%s", actor_id, audience_key, len(recipients), int((time.perf_counter() - started) * 1000))
    if recipients:
        return recipients

    logger.info("broadcast_audience_resolve_yclients_started actor_tg_id=%s audience_key=%s", actor_id, audience_key)
    logger.info("broadcast_audience_resolve_yclients_finished actor_tg_id=%s audience_key=%s count=0 source_used=yclients elapsed_ms=%s", actor_id, audience_key, int((time.perf_counter() - started) * 1000))
    logger.info("broadcast_audience_resolve_empty actor_tg_id=%s audience_key=%s count=0 source_used=empty elapsed_ms=%s", actor_id, audience_key, int((time.perf_counter() - started) * 1000))
    return []


async def create_one_time_broadcast(*, created_by_tg_id: int, audience_type: str, text: str, photo_file_id: str | None, branch_timezone: str) -> int:
    await execute("INSERT INTO broadcasts (created_by_tg_id, segment, message_type, text, file_id, status, created_at, started_at) VALUES (?,?,?,?,?,'sending',?,?)", (created_by_tg_id, audience_type, 'photo' if photo_file_id else 'text', text, photo_file_id, now_iso(), now_iso()))
    row = await fetchone("SELECT id FROM broadcasts ORDER BY id DESC LIMIT 1")
    return int(row["id"])


async def send_broadcast_now(bot: Bot, broadcast_id: int, recipient_ids: list[int]) -> dict[str, int]:
    b = await fetchone("SELECT text, file_id FROM broadcasts WHERE id=?", (broadcast_id,))
    text = (b["text"] if b else "") or ""
    file_id = b["file_id"] if b else None
    sent = failed = blocked = 0
    for uid in recipient_ids:
        status = "sent"; err = None
        try:
            if file_id:
                await bot.send_photo(uid, photo=file_id, caption=text or None)
            else:
                await bot.send_message(uid, text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1; failed += 1; status = "blocked"; err = "forbidden"
        except TelegramBadRequest as e:
            failed += 1; status = "failed"; err = str(e)[:120]
        except Exception as e:
            failed += 1; status = "failed"; err = str(e)[:120]
        await execute("INSERT INTO broadcast_recipients (broadcast_id,tg_user_id,status,error_short,updated_at) VALUES (?,?,?,?,?)", (broadcast_id, uid, status, err, now_iso()))
        await asyncio.sleep(0.05)
    await execute("UPDATE broadcasts SET status='done', finished_at=? WHERE id=?", (now_iso(), broadcast_id))
    return {"total_selected": len(recipient_ids), "sent": sent, "failed": failed, "blocked": blocked}


async def check_working_hours() -> tuple[bool, str, str, str | None, str]:
    settings = await get_yclients_settings()
    if not settings or not settings.company_id:
        return False, "unavailable", "—", None, "Europe/Moscow"
    tz = (await resolve_company_timezone(settings.company_id)).timezone_name
    now_local = datetime.now(ZoneInfo(tz))
    client, _ = await build_yclients_client()
    try:
        payload = await get_company(client, company_id=settings.company_id)
    except YClientsError:
        return False, "unavailable", now_local.strftime('%d.%m.%Y %H:%M'), None, tz
    finally:
        await client.close()
    company = payload.get('data') if isinstance(payload, dict) and isinstance(payload.get('data'), dict) else (payload if isinstance(payload, dict) else {})
    schedule = str(company.get('schedule') or company.get('work_time') or '').lower()
    if not schedule:
        return False, "unavailable", now_local.strftime('%d.%m.%Y %H:%M'), None, tz
    parsed = _parse_daily_schedule(schedule)
    if not parsed:
        return False, "unavailable", now_local.strftime('%d.%m.%Y %H:%M'), None, tz
    start, end = parsed
    is_open = start <= now_local.time() <= end
    next_start = _next_working_start_local(now_local, start, end)
    return is_open, "ok" if is_open else "closed", now_local.strftime('%d.%m.%Y %H:%M'), next_start.strftime('%d.%m.%Y %H:%M'), tz


async def next_working_start_utc() -> tuple[str | None, str | None, str]:
    ok, reason, now_local, next_window, tz = await check_working_hours()
    if reason == 'unavailable' or not next_window:
        return None, None, tz
    if ok:
        return datetime.now(timezone.utc).isoformat(), now_local, tz
    local_dt = datetime.strptime(next_window, '%d.%m.%Y %H:%M').replace(tzinfo=ZoneInfo(tz))
    return local_dt.astimezone(timezone.utc).isoformat(), next_window, tz


async def create_campaign(
    *,
    created_by_tg_id: int,
    created_by_role: str | None,
    audience_key: str,
    text: str,
    photo_file_id: str | None,
    total_count: int,
    branch_timezone: str,
    is_test: bool,
) -> int:
    await execute(
        """
        INSERT INTO broadcast_campaigns (
            created_by_tg_id, created_by_role, audience_key, audience_name, text, photo_file_id, status,
            total_count, sent_count, failed_count, blocked_count, skipped_count, created_at_utc, branch_timezone, is_test
        ) VALUES (?,?,?,?,?,?,'draft',?,0,0,0,0,?,?,?)
        """,
        (
            created_by_tg_id,
            created_by_role,
            audience_key,
            audience_name(audience_key),
            text,
            photo_file_id,
            total_count,
            now_iso(),
            branch_timezone,
            1 if is_test else 0,
        ),
    )
    row = await fetchone("SELECT id FROM broadcast_campaigns ORDER BY id DESC LIMIT 1")
    return int(row["id"])
