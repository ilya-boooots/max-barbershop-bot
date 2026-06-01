from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any

import aiosqlite

from app.config import get_db_path

GMT_PLUS_4 = timezone(timedelta(hours=4))


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return bool(row)


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    await cursor.close()
    return {str(row[1]) for row in rows}


def _format_gmt4(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(GMT_PLUS_4).strftime("%d.%m.%Y %H:%M")


def _booking_status_ru(status: str | None) -> str:
    mapping = {
        "approved": "подтверждена",
        "cancelled": "отменена",
        "pending": "ожидает",
    }
    return mapping.get(status or "", status or "—")


async def _build_clients_query(db: aiosqlite.Connection) -> str:
    users_columns = await _table_columns(db, "users")

    notes_expr = "''"
    if "notes" in users_columns:
        notes_expr = "COALESCE(u.notes, '')"
    elif "tags" in users_columns:
        notes_expr = "COALESCE(u.tags, '')"

    visits_expr = "0"
    if await _table_exists(db, "visits"):
        visits_columns = await _table_columns(db, "visits")
        if "user_id" in visits_columns:
            visits_expr = "COALESCE(vs.visits_count, 0)"
        elif "client_id" in visits_columns:
            visits_expr = "COALESCE(vs.visits_count, 0)"

    if visits_expr != "0":
        visit_fk = "user_id" if "user_id" in await _table_columns(db, "visits") else "client_id"
        visits_join = (
            "LEFT JOIN (SELECT {fk} AS uid, COUNT(*) AS visits_count FROM visits GROUP BY {fk}) vs "
            "ON vs.uid = u.user_id"
        ).format(fk=visit_fk)
    else:
        visits_join = ""

    notes_join = ""
    if notes_expr == "''":
        if await _table_exists(db, "user_notes"):
            note_cols = await _table_columns(db, "user_notes")
            fk = "user_id" if "user_id" in note_cols else ("client_id" if "client_id" in note_cols else "")
            val = "note" if "note" in note_cols else ("text" if "text" in note_cols else "")
            if fk and val:
                notes_expr = "COALESCE(un.notes, '')"
                notes_join = (
                    "LEFT JOIN (SELECT {fk} AS uid, GROUP_CONCAT({val}, '; ') AS notes FROM user_notes GROUP BY {fk}) un "
                    "ON un.uid = u.user_id"
                ).format(fk=fk, val=val)

    return f"""
        SELECT
            u.user_id AS tg_id,
            COALESCE(u.username, '') AS username,
            TRIM(COALESCE(u.display_name, '') || CASE WHEN COALESCE(u.display_name, '') != '' AND COALESCE(u.name, '') != '' THEN ' / ' ELSE '' END || COALESCE(u.name, '')) AS name,
            COALESCE(u.phone, '') AS phone,
            COALESCE(u.created_at, '') AS registered_at_utc,
            COALESCE(u.last_activity_ts_utc, '') AS last_activity_utc,
            {visits_expr} AS visits_count,
            COALESCE(u.bonus_balance, 0) AS bonus_balance,
            COALESCE(bs.bookings_total, 0) AS bookings_total,
            COALESCE(bs.bookings_approved, 0) AS bookings_approved,
            COALESCE(bs.bookings_cancelled, 0) AS bookings_cancelled,
            COALESCE(lb.ts_value, '') AS last_booking_utc,
            COALESCE(lb.status, '') AS last_booking_status,
            {notes_expr} AS notes
        FROM users u
        LEFT JOIN (
            SELECT
                user_id,
                COUNT(*) AS bookings_total,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS bookings_approved,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS bookings_cancelled
            FROM bookings
            GROUP BY user_id
        ) bs ON bs.user_id = u.user_id
        LEFT JOIN (
            SELECT b.user_id, COALESCE(b.created_ts_utc, b.created_ts_local) AS ts_value, b.status
            FROM bookings b
            INNER JOIN (
                SELECT user_id, MAX(created_ts_utc) AS max_created_ts_utc
                FROM bookings
                GROUP BY user_id
            ) latest ON latest.user_id = b.user_id AND latest.max_created_ts_utc = b.created_ts_utc
        ) lb ON lb.user_id = u.user_id
        {visits_join}
        {notes_join}
        WHERE u.is_registered = 1
    """


def _normalize_client_row(row: sqlite3.Row | aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    full_name = (payload.get("name") or "").strip()
    if not full_name:
        full_name = payload.get("username") or str(payload.get("tg_id") or "—")
    last_booking_ts = _format_gmt4(payload.get("last_booking_utc"))
    booking_status = _booking_status_ru(payload.get("last_booking_status"))
    last_booking = f"{last_booking_ts} — {booking_status}" if last_booking_ts else ""
    return {
        "tg_id": int(payload.get("tg_id") or 0),
        "username": payload.get("username") or "",
        "name": full_name,
        "phone": payload.get("phone") or "",
        "registered_at": _format_gmt4(payload.get("registered_at_utc")),
        "last_activity": _format_gmt4(payload.get("last_activity_utc")),
        "visits_count": int(payload.get("visits_count") or 0),
        "bonus_balance": int(payload.get("bonus_balance") or 0),
        "bookings_total": int(payload.get("bookings_total") or 0),
        "bookings_approved": int(payload.get("bookings_approved") or 0),
        "bookings_cancelled": int(payload.get("bookings_cancelled") or 0),
        "last_booking": last_booking,
        "notes": payload.get("notes") or "",
    }


async def get_reports_summary() -> dict[str, int]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN last_activity_ts_utc >= datetime('now', '-7 day') THEN 1 ELSE 0 END) AS active_7,
                SUM(CASE WHEN last_activity_ts_utc >= datetime('now', '-30 day') THEN 1 ELSE 0 END) AS active_30
            FROM users
            WHERE is_registered = 1
        """
        cursor = await db.execute(query)
        row = await cursor.fetchone()
        await cursor.close()
    return {
        "total": int(row["total"] or 0) if row else 0,
        "active_7": int(row["active_7"] or 0) if row else 0,
        "active_30": int(row["active_30"] or 0) if row else 0,
    }


async def get_clients_page(page: int, page_size: int = 10) -> tuple[list[dict[str, Any]], int]:
    safe_page = max(1, page)
    offset = (safe_page - 1) * page_size
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        base_query = await _build_clients_query(db)
        count_cursor = await db.execute("SELECT COUNT(*) AS total FROM users WHERE is_registered = 1")
        count_row = await count_cursor.fetchone()
        await count_cursor.close()
        total = int(count_row["total"] or 0) if count_row else 0

        query = f"{base_query} ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
        cursor = await db.execute(query, (page_size, offset))
        rows = await cursor.fetchall()
        await cursor.close()
    return [_normalize_client_row(row) for row in rows], total


async def get_all_clients() -> list[dict[str, Any]]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = f"{await _build_clients_query(db)} ORDER BY u.created_at DESC"
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        await cursor.close()
    return [_normalize_client_row(row) for row in rows]


async def find_clients(query_text: str, limit: int = 10) -> list[dict[str, Any]]:
    lookup = (query_text or "").strip()
    if not lookup:
        return []

    tg_id: int | None = int(lookup) if lookup.isdigit() else None
    phone_query = f"%{lookup}%"
    username = lookup[1:] if lookup.startswith("@") else lookup
    username_query = f"%{username.lower()}%"

    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        base_query = await _build_clients_query(db)
        query = f"""
            {base_query}
            AND (
                (? IS NOT NULL AND u.user_id = ?)
                OR u.phone LIKE ?
                OR LOWER(COALESCE(u.username, '')) LIKE ?
            )
            ORDER BY u.created_at DESC
            LIMIT ?
        """
        cursor = await db.execute(query, (tg_id, tg_id, phone_query, username_query, limit))
        rows = await cursor.fetchall()
        await cursor.close()
    return [_normalize_client_row(row) for row in rows]


async def get_client_by_tg_id(tg_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        query = f"{await _build_clients_query(db)} AND u.user_id = ? LIMIT 1"
        cursor = await db.execute(query, (tg_id,))
        row = await cursor.fetchone()
        await cursor.close()
    return _normalize_client_row(row) if row else None
