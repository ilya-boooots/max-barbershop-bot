from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute
from app.db.sqlite import fetchall


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_booking_link(
    *,
    tg_user_id: int,
    yclients_record_id: str,
    company_id: str,
    service_id: str,
    staff_id: str | None,
    datetime_iso: str,
    status: str,
    raw_payload: dict[str, Any] | list[Any] | None = None,
) -> None:
    ts = _now_iso()
    payload_json = json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None
    await execute(
        """
        INSERT INTO booking_links (
            tg_user_id,
            yclients_record_id,
            company_id,
            service_id,
            staff_id,
            datetime_iso,
            status,
            created_at,
            updated_at,
            raw_payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tg_user_id,
            yclients_record_id,
            company_id,
            service_id,
            staff_id,
            datetime_iso,
            status,
            ts,
            ts,
            payload_json,
        ),
    )


async def list_user_booking_links(*, tg_user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT id,
               tg_user_id,
               yclients_record_id,
               company_id,
               service_id,
               staff_id,
               datetime_iso,
               status,
               created_at,
               updated_at,
               raw_payload_json
        FROM booking_links
        WHERE tg_user_id = ?
        ORDER BY datetime_iso DESC, id DESC
        LIMIT ?
        """,
        (tg_user_id, limit),
    )
    return [dict(row) for row in rows]


async def update_booking_link_status(
    *,
    tg_user_id: int,
    yclients_record_id: str,
    status: str,
    raw_payload: dict[str, Any] | list[Any] | None = None,
) -> None:
    ts = _now_iso()
    payload_json = json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None
    await execute(
        """
        UPDATE booking_links
        SET status = ?,
            updated_at = ?,
            raw_payload_json = COALESCE(?, raw_payload_json)
        WHERE tg_user_id = ?
          AND yclients_record_id = ?
        """,
        (status, ts, payload_json, tg_user_id, yclients_record_id),
    )


async def upsert_booking_link_snapshot(
    *,
    tg_user_id: int,
    yclients_record_id: str,
    company_id: str | None,
    service_id: str | None,
    staff_id: str | None,
    datetime_iso: str,
    status: str,
    raw_payload: dict[str, Any] | list[Any] | None = None,
) -> None:
    ts = _now_iso()
    payload_json = json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None
    await execute(
        """
        INSERT INTO booking_links (
            tg_user_id,
            yclients_record_id,
            company_id,
            service_id,
            staff_id,
            datetime_iso,
            status,
            created_at,
            updated_at,
            raw_payload_json
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM booking_links
            WHERE tg_user_id = ?
              AND yclients_record_id = ?
        )
        """,
        (
            tg_user_id,
            yclients_record_id,
            company_id,
            service_id,
            staff_id,
            datetime_iso,
            status,
            ts,
            ts,
            payload_json,
            tg_user_id,
            yclients_record_id,
        ),
    )
