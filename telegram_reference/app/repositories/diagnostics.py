from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import get_db_path
from app.db.sqlite import execute, fetchall, fetchone


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return value
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return digits


async def log_user_event(
    *,
    user_id: int,
    username: str | None,
    phone: str | None,
    event_type: str,
    event_name: str,
    screen: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await execute(
        """
        INSERT INTO user_events (
            ts_utc,
            user_id,
            username,
            phone,
            event_type,
            event_name,
            screen,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_utc_iso(),
            user_id,
            username,
            phone,
            event_type,
            event_name[:128],
            screen,
            json.dumps(payload, ensure_ascii=False) if payload else None,
        ),
    )


async def log_bot_event(
    *,
    level: str,
    source: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> int:
    await execute(
        """
        INSERT INTO bot_logs (ts_utc, level, source, message, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            now_utc_iso(),
            level,
            source,
            message,
            json.dumps(details, ensure_ascii=False) if details else None,
        ),
    )
    row = await fetchone("SELECT id FROM bot_logs ORDER BY id DESC LIMIT 1")
    return int(row["id"]) if row else 0


async def get_recent_bot_logs(limit: int = 200) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT id, ts_utc, level, source, message
        FROM bot_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]


async def export_bot_logs_csv() -> Path:
    rows = await fetchall(
        "SELECT id, ts_utc, level, source, message, details_json FROM bot_logs ORDER BY id DESC"
    )
    tmp = tempfile.NamedTemporaryFile(prefix="bot_logs_", suffix=".csv", delete=False)
    with open(tmp.name, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "ts_utc", "level", "source", "message", "details_json"])
        for row in rows:
            writer.writerow([row["id"], row["ts_utc"], row["level"], row["source"], row["message"], row["details_json"]])
    return Path(tmp.name)


async def find_user_events(query: str, days: int = 3650) -> list[dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if q.isdigit() and len(q) >= 6:
        rows = await fetchall(
            """
            SELECT *
            FROM user_events
            WHERE CAST(user_id AS TEXT) = ?
              AND ts_utc >= ?
            ORDER BY id DESC
            LIMIT 500
            """,
            (q, since),
        )
        return [dict(r) for r in rows]
    if q.startswith("@"):
        username = q[1:].strip().lower()
        rows = await fetchall(
            """
            SELECT *
            FROM user_events
            WHERE lower(COALESCE(username, '')) = ?
              AND ts_utc >= ?
            ORDER BY id DESC
            LIMIT 500
            """,
            (username, since),
        )
        return [dict(r) for r in rows]
    if "+" in q or q.startswith(("7", "8")):
        phone = normalize_phone(q)
        rows = await fetchall(
            "SELECT * FROM user_events WHERE phone = ? AND ts_utc >= ? ORDER BY id DESC LIMIT 500",
            (phone, since),
        )
        return [dict(r) for r in rows]
    rows = await fetchall(
        """
        SELECT ue.*
        FROM user_events ue
        LEFT JOIN users u ON u.user_id = ue.user_id
        WHERE ue.ts_utc >= ?
          AND (
            ue.username LIKE ?
            OR u.name LIKE ?
            OR u.display_name LIKE ?
          )
        ORDER BY ue.id DESC
        LIMIT 500
        """,
        (since, f"%{q}%", f"%{q}%", f"%{q}%"),
    )
    return [dict(r) for r in rows]


async def get_user_events_by_tg_id(tg_id_text: str, days: int = 3650) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await fetchall(
        """
        SELECT *
        FROM user_events
        WHERE CAST(user_id AS TEXT) = ?
          AND ts_utc >= ?
        ORDER BY id DESC
        LIMIT 500
        """,
        (tg_id_text, since),
    )
    return [dict(r) for r in rows]


async def find_user_card_in_events(query: str) -> dict[str, Any] | None:
    q = query.strip()
    if not q:
        return None
    row = None
    if q.isdigit() and len(q) >= 6:
        row = await fetchone(
            """
            SELECT user_id, username, phone, ts_utc
            FROM user_events
            WHERE CAST(user_id AS TEXT) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (q,),
        )
    elif q.startswith("@"):
        row = await fetchone(
            """
            SELECT user_id, username, phone, ts_utc
            FROM user_events
            WHERE lower(COALESCE(username, '')) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (q[1:].strip().lower(),),
        )
    elif "+" in q or q.startswith(("7", "8")):
        row = await fetchone(
            """
            SELECT user_id, username, phone, ts_utc
            FROM user_events
            WHERE phone = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalize_phone(q),),
        )
    if row is None:
        return None
    return dict(row)


async def get_db_debug_info() -> dict[str, Any]:
    users_count_row = await fetchone("SELECT COUNT(*) AS cnt FROM users")
    events_count_row = await fetchone("SELECT COUNT(*) AS cnt FROM user_events")
    return {
        "db_path": str(get_db_path()),
        "users_count": int(users_count_row["cnt"]) if users_count_row else 0,
        "user_events_count": int(events_count_row["cnt"]) if events_count_row else 0,
    }


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    events_7d = [e for e in events if _parse_iso(e.get("ts_utc")) >= week_ago]
    top = Counter(e.get("event_name") or "-" for e in events_7d).most_common(5)
    return {
        "total_7d": len(events_7d),
        "last_activity": events[0]["ts_utc"] if events else None,
        "top_buttons": top,
    }


async def export_user_events_csv(events: list[dict[str, Any]]) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="user_events_", suffix=".csv", delete=False)
    with open(tmp.name, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id",
            "ts_utc",
            "user_id",
            "username",
            "phone",
            "event_type",
            "event_name",
            "screen",
            "payload_json",
        ])
        for row in events:
            writer.writerow([
                row.get("id"),
                row.get("ts_utc"),
                row.get("user_id"),
                row.get("username"),
                row.get("phone"),
                row.get("event_type"),
                row.get("event_name"),
                row.get("screen"),
                row.get("payload_json"),
            ])
    return Path(tmp.name)


async def search_events_text(query: str, days: int) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await fetchall(
        """
        SELECT id, ts_utc, user_id, username, event_name
        FROM user_events
        WHERE ts_utc >= ?
          AND event_name LIKE ?
        ORDER BY id DESC
        LIMIT 30
        """,
        (since, f"%{query.strip()}%"),
    )
    return [dict(r) for r in rows]


async def db_healthcheck() -> bool:
    row = await fetchone("SELECT 1 AS ok")
    return bool(row and row["ok"] == 1)


async def upsert_error_event(
    *,
    fingerprint: str,
    error_type: str,
    where: str,
    count: int,
    first_seen: str,
    last_seen: str,
    last_context_json: str | None,
) -> None:
    await execute(
        """
        INSERT INTO error_events (
            fingerprint,
            error_type,
            where_text,
            count,
            first_seen,
            last_seen,
            last_context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            error_type = excluded.error_type,
            where_text = excluded.where_text,
            count = excluded.count,
            first_seen = excluded.first_seen,
            last_seen = excluded.last_seen,
            last_context_json = excluded.last_context_json
        """,
        (fingerprint, error_type, where, count, first_seen, last_seen, last_context_json),
    )


async def list_recent_error_events(limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT fingerprint, error_type, where_text, count, first_seen, last_seen, last_context_json
        FROM error_events
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        {
            "fingerprint": row["fingerprint"],
            "error_type": row["error_type"],
            "where": row["where_text"],
            "count": row["count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "last_context_json": row["last_context_json"],
        }
        for row in rows
    ]


async def clear_error_events() -> None:
    await execute("DELETE FROM error_events")


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
