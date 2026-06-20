"""SQLite-backed diagnostics events for MAX developer tools."""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from max_barbershop_bot.services.diagnostics import sanitize_mapping, sanitize_text


@dataclass(frozen=True)
class UserEventsSummary:
    """Compact user activity summary."""

    total_7d: int
    last_activity: str | None
    top_buttons: list[tuple[str, int]]


class DiagnosticsRepository:
    """Store and query bot logs/user events for protected developer diagnostics."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def log_bot_event(
        self,
        *,
        level: str,
        source: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> int:
        """Persist a safe bot log row and return its id."""

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO bot_logs (ts_utc, level, source, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _now_utc_iso(),
                    sanitize_text(level or "INFO")[:32],
                    sanitize_text(source or "bot")[:160],
                    sanitize_text(message or "")[:2000],
                    json.dumps(sanitize_mapping(details), ensure_ascii=False, sort_keys=True) if details else None,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_recent_bot_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return recent bot logs newest first, like Telegram reference repository."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, ts_utc, level, source, message, details_json
                FROM bot_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_user_event(
        self,
        *,
        platform_user_id: str | None,
        max_user_id: str | None,
        chat_id: str | None,
        username: str | None,
        phone: str | None,
        event_type: str,
        event_name: str,
        screen: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        """Persist a safe user event for diagnostics search."""

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO user_events (
                    ts_utc, platform_user_id, max_user_id, chat_id, username, phone,
                    event_type, event_name, screen, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_utc_iso(),
                    _optional_text(platform_user_id),
                    _optional_text(max_user_id),
                    _optional_text(chat_id),
                    _normalize_username(username),
                    _optional_text(phone),
                    sanitize_text(event_type or "event")[:80],
                    sanitize_text(event_name or "—")[:128],
                    sanitize_text(screen or "")[:160] or None,
                    json.dumps(sanitize_mapping(payload), ensure_ascii=False, sort_keys=True) if payload else None,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def find_user_events(self, query: str, *, days: int = 3650, limit: int = 500) -> list[dict[str, Any]]:
        """Search user events by MAX user id, @username or phone/name-ish text."""

        q = query.strip()
        if not q:
            return []
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        params: tuple[Any, ...]
        if q.isdigit() and len(q) >= 4:
            where = "CAST(platform_user_id AS TEXT) = ?"
            params = (q, since, max(1, min(limit, 500)))
        elif q.startswith("@"):
            where = "lower(COALESCE(username, '')) = ?"
            params = (q[1:].strip().lower(), since, max(1, min(limit, 500)))
        elif "+" in q or q.startswith(("7", "8")):
            where = "phone = ?"
            params = (_normalize_phone(q), since, max(1, min(limit, 500)))
        else:
            where = "(username LIKE ? OR event_name LIKE ? OR payload_json LIKE ?)"
            needle = f"%{sanitize_text(q)}%"
            params = (needle, needle, needle, since, max(1, min(limit, 500)))

        sql = f"""
            SELECT *
            FROM user_events
            WHERE {where}
              AND ts_utc >= ?
            ORDER BY id DESC
            LIMIT ?
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def search_events(self, query: str, *, days: int = 3650, limit: int = 500) -> list[dict[str, Any]]:
        """Search user events by keyword in event name, screen and payload."""

        q = query.strip()
        if not q:
            return []
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        needle = f"%{sanitize_text(q)}%"
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM user_events
                WHERE ts_utc >= ?
                  AND (
                    event_type LIKE ?
                    OR event_name LIKE ?
                    OR screen LIKE ?
                    OR payload_json LIKE ?
                    OR username LIKE ?
                    OR platform_user_id LIKE ?
                  )
                ORDER BY id DESC
                LIMIT ?
                """,
                (since, needle, needle, needle, needle, needle, needle, max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def summarize_events(self, events: list[dict[str, Any]]) -> UserEventsSummary:
        """Summarize user events exactly for diagnostics UX."""

        week_ago = datetime.now(UTC) - timedelta(days=7)
        events_7d = [item for item in events if _parse_iso(item.get("ts_utc")) >= week_ago]
        top = Counter(str(item.get("event_name") or "—") for item in events_7d).most_common(5)
        return UserEventsSummary(
            total_7d=len(events_7d),
            last_activity=str(events[0].get("ts_utc")) if events else None,
            top_buttons=top,
        )

    def export_bot_logs_csv(self, limit: int = 200) -> Path:
        """Write recent bot logs to a temporary CSV file."""

        rows = list(reversed(self.get_recent_bot_logs(limit)))
        tmp = tempfile.NamedTemporaryFile(prefix="bot_logs_", suffix=".csv", delete=False)
        with open(tmp.name, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["id", "ts_utc", "level", "source", "message", "details_json"])
            for row in rows:
                writer.writerow(
                    [
                        row.get("id"),
                        row.get("ts_utc"),
                        row.get("level"),
                        row.get("source"),
                        row.get("message"),
                        row.get("details_json"),
                    ]
                )
        return Path(tmp.name)

    def export_user_events_csv(self, events: list[dict[str, Any]]) -> Path:
        """Write selected user events to a temporary CSV file."""

        tmp = tempfile.NamedTemporaryFile(prefix="user_events_", suffix=".csv", delete=False)
        with open(tmp.name, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "id",
                    "ts_utc",
                    "platform_user_id",
                    "max_user_id",
                    "chat_id",
                    "username",
                    "phone",
                    "event_type",
                    "event_name",
                    "screen",
                    "payload_json",
                ]
            )
            for row in events:
                writer.writerow(
                    [
                        row.get("id"),
                        row.get("ts_utc"),
                        row.get("platform_user_id"),
                        row.get("max_user_id"),
                        row.get("chat_id"),
                        row.get("username"),
                        row.get("phone"),
                        row.get("event_type"),
                        row.get("event_name"),
                        row.get("screen"),
                        row.get("payload_json"),
                    ]
                )
        return Path(tmp.name)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: object) -> datetime:
    try:
        text = str(value or "")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        return datetime.min.replace(tzinfo=UTC)


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = sanitize_text(str(value)).strip()
    return text or None


def _normalize_username(value: str | None) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    return text.lstrip("@").lower()


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return sanitize_text(value)
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return digits
