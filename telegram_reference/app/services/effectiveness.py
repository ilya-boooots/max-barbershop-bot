from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.sqlite import fetchall, fetchone

FUNNELS = {
    "manual_broadcast": "✉️ Ручная рассылка",
    "post_visit_rating": "⭐️ Оценка после визита",
    "cancellation_recovery": "❌ Возврат после отмены",
    "lost_client": "😔 Потерянные клиенты",
    "birthday": "🎂 День рождения",
    "repeat_visit": "🔁 Повторный визит",
}


def period_bounds(days: int, tz_name: str) -> tuple[str, str]:
    now_local = datetime.now(ZoneInfo(tz_name))
    start_local = (now_local - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).isoformat(), now_local.astimezone(timezone.utc).isoformat()


async def build_metrics(days: int, tz_name: str) -> dict:
    start_utc, end_utc = period_bounds(days, tz_name)
    delivered = errors = sent = 0
    funnel_rows = {}
    for key, table in {
        "lost_client": "lost_client_events",
        "birthday": "birthday_funnel_events",
        "repeat_visit": "repeat_visit_events",
        "cancellation_recovery": "cancellation_recovery_events",
        "post_visit_rating": "post_visit_feedback_events",
    }.items():
        row = await fetchone(f"SELECT COUNT(1) c, SUM(CASE WHEN status LIKE 'sent%' OR status LIKE 'rated%' OR status LIKE 'clicked%' THEN 1 ELSE 0 END) d, SUM(CASE WHEN status IN ('failed','blocked','error') THEN 1 ELSE 0 END) e FROM {table} WHERE is_test=0 AND created_at_utc>=? AND created_at_utc<?", (start_utc, end_utc))
        s = int((row or {}).get("c") or 0); d = int((row or {}).get("d") or 0); e = int((row or {}).get("e") or 0)
        sent += s; delivered += d; errors += e
        funnel_rows[key] = {"sent": s, "delivered": d, "errors": e}

    c_row = await fetchone("SELECT COUNT(1) c FROM notification_attributions WHERE is_test=0 AND status='clicked' AND created_at_utc>=? AND created_at_utc<?", (start_utc, end_utc))
    b_row = await fetchone("SELECT COUNT(1) c, COALESCE(SUM(attributed_revenue),0) rev FROM notification_attributions WHERE is_test=0 AND status='attributed_booking' AND booking_created_at_utc>=? AND booking_created_at_utc<?", (start_utc, end_utc))
    clicks = int((c_row or {}).get("c") or 0)
    bookings = int((b_row or {}).get("c") or 0)
    revenue = float((b_row or {}).get("rev") or 0)
    bad = await fetchone("SELECT COUNT(1) c FROM post_visit_feedback_events WHERE is_test=0 AND rating BETWEEN 1 AND 3 AND client_comment IS NOT NULL AND created_at_utc>=? AND created_at_utc<?", (start_utc, end_utc))
    bad_reviews = int((bad or {}).get("c") or 0)
    conv = (bookings / delivered * 100) if delivered else None
    return {"start": start_utc, "end": end_utc, "days": days, "sent": sent, "delivered": delivered, "errors": errors, "clicks": clicks, "bookings": bookings, "returned_clients": bookings, "revenue": revenue, "bad_reviews": bad_reviews, "conversion": conv, "avg_check": (revenue / bookings) if bookings else 0, "funnels": funnel_rows}
