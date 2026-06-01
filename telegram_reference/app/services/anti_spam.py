from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.sqlite import execute, fetchone
from app.repositories.automation_settings import get_setting
from app.repositories.marketing_preferences import is_unsubscribed
from app.repositories.broadcasts import check_working_hours
from app.services.notification_delivery import get_notification_delivery_type

logger = logging.getLogger(__name__)


async def record_delivery_decision(*, client_tg_id: int | None, notification_type: str, category: str, decision: str, reason_summary: str | None = None, funnel_type: str | None = None, source_event_id: str | None = None, yclients_client_id: str | None = None, branch_timezone: str | None = None, is_test: bool = False) -> None:
    delivery_type = get_notification_delivery_type(notification_type)
    await execute(
        "INSERT INTO notification_delivery_decisions (client_tg_id,yclients_client_id,notification_type,delivery_type,category,funnel_type,source_event_id,decision,reason_summary,created_at_utc,branch_timezone,is_test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (client_tg_id, yclients_client_id, notification_type, delivery_type, category, funnel_type, source_event_id, decision, reason_summary, datetime.now(timezone.utc).isoformat(), branch_timezone, 1 if is_test else 0),
    )


async def can_send_notification(*, client_tg_id: int | None, notification_type: str, category: str, funnel_type: str | None = None, source_event_id: str | None = None, is_test: bool = False, actor_tg_id: int | None = None) -> tuple[bool, str]:
    delivery_type = get_notification_delivery_type(notification_type)
    if delivery_type == "white" or category == "service":
        return True, "allowed_white_service_notification"
    if not client_tg_id:
        return False, "skipped_no_telegram"
    settings = await get_setting("anti_spam")
    if settings.get("respect_marketing_unsubscribe", True) and await is_unsubscribed(client_tg_id):
        return False, "blocked_unsubscribed"
    if not is_test:
        if not (actor_tg_id == 378881880):
            ok, reason, *_ = await check_working_hours()
            if not ok:
                return False, "blocked_outside_working_hours"
            quiet = await get_setting("quiet_hours")
            if quiet.get("enabled", True):
                tz = _[-1] if _ else "Europe/Moscow"
                if _in_quiet_hours(datetime.now(ZoneInfo(tz)).time(), quiet.get("start", "21:00"), quiet.get("end", "09:00")):
                    return False, "blocked_quiet_hours"
        min_interval_h = int(settings.get("min_interval_hours", 48))
        row = await fetchone("SELECT created_at_utc FROM notification_delivery_decisions WHERE client_tg_id=? AND category='marketing' AND decision='allowed' AND is_test=0 ORDER BY id DESC LIMIT 1", (client_tg_id,))
        if row:
            last = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last < timedelta(hours=min_interval_h):
                return False, "blocked_min_interval"
        weekly = await fetchone("SELECT COUNT(1) c FROM notification_delivery_decisions WHERE client_tg_id=? AND category='marketing' AND decision='allowed' AND is_test=0 AND created_at_utc>=?", (client_tg_id, (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()))
        if int(weekly[0] or 0) >= int(settings.get("max_weekly_marketing", 2)):
            return False, "blocked_frequency_limit"
    if source_event_id and await is_duplicate(client_tg_id, notification_type, source_event_id):
        return False, "blocked_duplicate"
    return True, "allowed"


async def is_duplicate(client_tg_id: int, notification_type: str, source_event_id: str) -> bool:
    row = await fetchone("SELECT 1 FROM notification_delivery_decisions WHERE client_tg_id=? AND notification_type=? AND source_event_id=? AND decision='allowed' LIMIT 1", (client_tg_id, notification_type, source_event_id))
    return bool(row)


def _in_quiet_hours(now_t, start: str, end: str) -> bool:
    sh, sm = [int(x) for x in start.split(":")]
    eh, em = [int(x) for x in end.split(":")]
    s = sh * 60 + sm
    e = eh * 60 + em
    n = now_t.hour * 60 + now_t.minute
    return (n >= s or n < e) if s > e else (s <= n < e)
