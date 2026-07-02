"""Persistence for omnichannel one-time broadcast drafts and recipient history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from typing import Any


class OmnichannelBroadcastRepository:
    """SQLite-backed storage for omnichannel broadcast runs."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS omnichannel_broadcasts (
                    broadcast_id TEXT PRIMARY KEY,
                    origin_platform TEXT NOT NULL,
                    text TEXT NOT NULL,
                    attachment_type TEXT,
                    attachment_json TEXT,
                    audience_source TEXT NOT NULL DEFAULT 'yclients_all_clients',
                    created_by_platform TEXT,
                    created_by_user_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT,
                    report_json TEXT
                );
                CREATE TABLE IF NOT EXISTS omnichannel_broadcast_delivery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    broadcast_id TEXT NOT NULL,
                    yclients_client_id TEXT,
                    selected_platform TEXT,
                    platform_user_id TEXT,
                    delivery_status TEXT NOT NULL,
                    reason TEXT,
                    origin_platform TEXT,
                    priority_decision TEXT,
                    error_short TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_omni_delivery_broadcast
                    ON omnichannel_broadcast_delivery(broadcast_id);
                CREATE INDEX IF NOT EXISTS idx_omni_delivery_yclients
                    ON omnichannel_broadcast_delivery(broadcast_id, yclients_client_id);
                """
            )
            connection.commit()

    def upsert_broadcast(self, *, broadcast_id: str, origin_platform: str, text: str, attachment_type: str | None, attachment: Mapping[str, Any] | None, created_by_user_id: str | None, status: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO omnichannel_broadcasts (broadcast_id, origin_platform, text, attachment_type, attachment_json, created_by_platform, created_by_user_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(broadcast_id) DO UPDATE SET
                    text=excluded.text, attachment_type=excluded.attachment_type, attachment_json=excluded.attachment_json,
                    status=excluded.status, updated_at=CURRENT_TIMESTAMP
                """,
                (broadcast_id, origin_platform, text, attachment_type, json.dumps(dict(attachment or {}), ensure_ascii=False), origin_platform, created_by_user_id, status),
            )
            connection.commit()

    def mark_status(self, broadcast_id: str, status: str, *, report: Mapping[str, Any] | None = None, started: bool = False, finished: bool = False) -> None:
        parts = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        values: list[Any] = [status]
        if started:
            parts.append("started_at = CURRENT_TIMESTAMP")
        if finished:
            parts.append("finished_at = CURRENT_TIMESTAMP")
        if report is not None:
            parts.append("report_json = ?")
            values.append(json.dumps(dict(report), ensure_ascii=False, sort_keys=True))
        values.append(broadcast_id)
        with closing(self._connect()) as connection:
            connection.execute(f"UPDATE omnichannel_broadcasts SET {', '.join(parts)} WHERE broadcast_id = ?", tuple(values))
            connection.commit()

    def add_delivery(self, *, broadcast_id: str, yclients_client_id: str | None, selected_platform: str | None, platform_user_id: str | None, delivery_status: str, reason: str | None, origin_platform: str, priority_decision: str | None, error_short: str | None = None, metadata: Mapping[str, Any] | None = None, sent: bool = False) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO omnichannel_broadcast_delivery (
                    broadcast_id, yclients_client_id, selected_platform, platform_user_id, delivery_status,
                    reason, origin_platform, priority_decision, error_short, metadata_json, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
                """,
                (broadcast_id, yclients_client_id, selected_platform, platform_user_id, delivery_status, reason, origin_platform, priority_decision, error_short, json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True), 1 if sent else 0),
            )
            connection.commit()


    def list_recent_broadcasts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent manual omnichannel broadcasts for admin history/effectiveness."""

        safe_limit = max(1, min(int(limit), 100))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT b.*,
                       SUM(CASE WHEN d.delivery_status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                       SUM(CASE WHEN d.delivery_status LIKE 'skipped%' THEN 1 ELSE 0 END) AS skipped_count,
                       SUM(CASE WHEN d.delivery_status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                       COUNT(d.id) AS delivery_count
                FROM omnichannel_broadcasts b
                LEFT JOIN omnichannel_broadcast_delivery d ON d.broadcast_id = b.broadcast_id
                GROUP BY b.broadcast_id
                ORDER BY COALESCE(b.finished_at, b.started_at, b.updated_at, b.created_at) DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def count_delivery_statuses(self) -> dict[str, int]:
        """Return delivery status counters for broadcast effectiveness."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT delivery_status, COUNT(*) AS count
                FROM omnichannel_broadcast_delivery
                GROUP BY delivery_status
                """
            ).fetchall()
        return {str(row['delivery_status']): int(row['count'] or 0) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection
