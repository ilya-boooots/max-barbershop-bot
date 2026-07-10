"""Telegram-equivalent safe developer notification test event storage for MAX."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class NotificationTestEvent:
    id: int
    event_type: str
    target_platform_user_id: str | None
    target_max_user_id: str | None
    target_chat_id: str | None
    target_tg_id: str | None
    source: str
    is_test: bool
    payload_json: str
    status: str
    created_at_utc: str
    sent_at_utc: str | None
    error_summary: str | None


class NotificationTestEventsRepository:
    """Persist one safe test lifecycle row per developer test click."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_test_event(
        self,
        *,
        event_type: str,
        target_platform_user_id: int | str | None,
        target_max_user_id: int | str | None = None,
        target_chat_id: int | str | None = None,
        target_tg_id: int | str | None = None,
        payload: dict[str, Any] | None = None,
        status: str = "created",
    ) -> int:
        created_at = _now_iso()
        payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO notification_test_events (
                    event_type, target_platform_user_id, target_max_user_id, target_chat_id, target_tg_id,
                    source, is_test, payload_json, status, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, 'dev_test', 1, ?, ?, ?)
                """,
                (
                    str(event_type),
                    _clean(target_platform_user_id),
                    _clean(target_max_user_id),
                    _clean(target_chat_id),
                    _clean(target_tg_id),
                    payload_json,
                    str(status or "created"),
                    created_at,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid or 0)

    def mark_sent(self, event_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE notification_test_events SET status='sent', sent_at_utc=? WHERE id=?",
                (_now_iso(), int(event_id)),
            )
            connection.commit()

    def mark_failed(self, event_id: int, error_summary: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE notification_test_events SET status='failed', error_summary=? WHERE id=?",
                (_sanitize_error(error_summary), int(event_id)),
            )
            connection.commit()

    def cleanup_test_events(self) -> int:
        with closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM notification_test_events WHERE is_test=1 OR source='dev_test'")
            connection.commit()
            return int(cursor.rowcount or 0)

    def get_event(self, event_id: int) -> NotificationTestEvent | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM notification_test_events WHERE id=?", (int(event_id),)).fetchone()
        if row is None:
            return None
        return NotificationTestEvent(
            id=int(row["id"]),
            event_type=str(row["event_type"]),
            target_platform_user_id=row["target_platform_user_id"],
            target_max_user_id=row["target_max_user_id"],
            target_chat_id=row["target_chat_id"],
            target_tg_id=row["target_tg_id"],
            source=str(row["source"]),
            is_test=bool(row["is_test"]),
            payload_json=str(row["payload_json"]),
            status=str(row["status"]),
            created_at_utc=str(row["created_at_utc"]),
            sent_at_utc=row["sent_at_utc"],
            error_summary=row["error_summary"],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def create_test_event(database_path: str, **kwargs: Any) -> int:
    return NotificationTestEventsRepository(database_path).create_test_event(**kwargs)


def mark_sent(database_path: str, event_id: int) -> None:
    NotificationTestEventsRepository(database_path).mark_sent(event_id)


def mark_failed(database_path: str, event_id: int, error_summary: str) -> None:
    NotificationTestEventsRepository(database_path).mark_failed(event_id, error_summary)


def cleanup_test_events(database_path: str) -> int:
    return NotificationTestEventsRepository(database_path).cleanup_test_events()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _sanitize_error(error_summary: str) -> str:
    text = re.sub(r"(?i)(token|authorization|password|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=<hidden>", str(error_summary or "send_failed"))
    text = " ".join(text.replace("\n", " ").replace("\r", " ").split())
    return text[:200]
