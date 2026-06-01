from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def log_click(
    *,
    funnel_type: str,
    client_tg_id: int | None,
    yclients_client_id: str | None,
    notification_event_id: int | None = None,
    campaign_id: int | None = None,
    is_test: bool = False,
    source: str | None = None,
) -> None:
    ts = _now()
    await execute(
        """INSERT INTO notification_attributions (
            notification_event_id, campaign_id, funnel_type, client_tg_id, yclients_client_id,
            click_at_utc, status, is_test, source, created_at_utc, updated_at_utc
        ) VALUES (?,?,?,?,?,?,'clicked',?,?,?,?)""",
        (notification_event_id, campaign_id, funnel_type, client_tg_id, yclients_client_id, ts, 1 if is_test else 0, source, ts, ts),
    )


async def has_booking_attribution(booking_id: str) -> bool:
    row = await fetchone("SELECT id FROM notification_attributions WHERE yclients_booking_id=? LIMIT 1", (booking_id,))
    return row is not None


async def find_last_click(*, client_tg_id: int | None, yclients_client_id: str | None, booking_created_at_utc: str, window_days: int = 7) -> dict[str, Any] | None:
    if not client_tg_id and not yclients_client_id:
        return None
    where = []
    params: list[Any] = [booking_created_at_utc, booking_created_at_utc, window_days]
    if client_tg_id:
        where.append("client_tg_id=?")
        params.append(client_tg_id)
    if yclients_client_id:
        where.append("yclients_client_id=?")
        params.append(yclients_client_id)
    logger.info(
        "notification_attribution_find_last_click context=%s client_tg_id=%s yclients_client_id=%s booking_created_at_utc=%s window_days=%s",
        "booking_create_notification_attribution",
        client_tg_id,
        yclients_client_id or "n/a",
        booking_created_at_utc,
        window_days,
    )
    row = await fetchone(
        f"""SELECT * FROM notification_attributions
            WHERE status='clicked' AND is_test=0
              AND click_at_utc IS NOT NULL
              AND click_at_utc <= ?
              AND datetime(click_at_utc) >= datetime(?, '-' || ? || ' days')
              AND ({' OR '.join(where)})
            ORDER BY click_at_utc DESC, id DESC
            LIMIT 1""",
        tuple(params),
    )
    return dict(row) if row else None


async def mark_attributed(*, attribution_id: int, booking_id: str, booking_created_at_utc: str, revenue: float | None) -> None:
    await execute(
        """UPDATE notification_attributions
           SET status='attributed_booking', yclients_booking_id=?, booking_created_at_utc=?, attributed_revenue=?, updated_at_utc=?
           WHERE id=?""",
        (booking_id, booking_created_at_utc, revenue, _now(), attribution_id),
    )


async def aggregate_summary(period_start_utc: str, period_end_utc: str) -> dict[str, Any]:
    rows = await fetchall("SELECT * FROM notification_attributions WHERE is_test=0 AND created_at_utc>=? AND created_at_utc<?", (period_start_utc, period_end_utc))
    return {"clicks": len(rows)}
