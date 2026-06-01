from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations.yclients.endpoints import list_user_bookings
from app.integrations.yclients.service import build_yclients_client
from app.repositories.automation_settings import get_setting
from app.repositories.post_visit_feedback_events import create_event
from app.repositories.users import find_user_by_phone
from app.services.company_time import resolve_company_timezone

logger = logging.getLogger(__name__)
_COMPLETED = {"done", "completed", "visit", "paid", "show"}


def _s(v: Any) -> str:
    return str(v or "").strip()


def _is_completed(row: dict[str, Any]) -> bool:
    attendance = row.get("attendance")
    if attendance is None:
        attendance = row.get("visit_attendance")
    if attendance is not None:
        return str(attendance).strip() == "1"
    return _s(row.get("status") or row.get("record_status") or row.get("state")).strip().lower() in _COMPLETED


async def scan_completed_visits_and_create_events(company_id: str) -> int:
    settings = await get_setting("post_visit_review")
    if not settings.get("enabled"):
        return 0
    client, _ = await build_yclients_client()
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=2)).date().isoformat()
    end = now.date().isoformat()
    payload = await list_user_bookings(client, company_id=company_id, start_date=start, end_date=end, page=1, count=200)
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return 0
    tz_name = await resolve_company_timezone(company_id=company_id)
    created = 0
    for row in rows:
        if not _is_completed(row):
            continue
        phone = _s((row.get("client") or {}).get("phone") or row.get("phone"))
        record_id = _s(row.get("id") or row.get("record_id") or row.get("booking_id") or row.get("visit_id"))
        if not record_id:
            continue
        user = await find_user_by_phone(phone) if phone else None
        event_id = await create_event(
            {
                "yclients_record_id": record_id,
                "yclients_client_id": _s((row.get("client") or {}).get("id") or row.get("client_id")),
                "client_tg_id": int(user["user_id"]) if user else None,
                "client_name": _s((row.get("client") or {}).get("name") or row.get("fullname")),
                "client_phone": phone,
                "staff_id": _s((row.get("staff") or {}).get("id") or row.get("staff_id")),
                "staff_name": _s((row.get("staff") or {}).get("name") or row.get("staff_name")),
                "service_id": _s((row.get("services") or [{}])[0].get("id") if isinstance(row.get("services"), list) and row.get("services") else row.get("service_id")),
                "service_name": _s((row.get("services") or [{}])[0].get("title") if isinstance(row.get("services"), list) and row.get("services") else row.get("service_name")),
                "visit_datetime_utc": (datetime.fromisoformat(_s(row.get("datetime") or row.get("date")).replace("Z","+00:00")) + timedelta(hours=int(settings.get("delay_hours") or 2))).astimezone(timezone.utc).isoformat() if _s(row.get("datetime") or row.get("date")) else None,
                "branch_timezone": tz_name,
                "source": "yclients",
                "is_test": False,
            }
        )
        if event_id:
            created += 1
            logger.info("post_visit_feedback_event_created record_id=%s event_id=%s tg_id=%s", record_id, event_id, user.get("user_id") if user else None)
    return created
