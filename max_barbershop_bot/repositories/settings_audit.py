"""SQLite repository for safe settings audit events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Mapping

from max_barbershop_bot.repositories.users import PLATFORM_MAX


@dataclass(frozen=True)
class SettingsAuditRecord:
    """One settings audit log row."""

    id: int
    platform: str
    actor_platform_user_id: str | None
    actor_role: str | None
    action: str
    section: str | None
    target_platform_user_id: str | None
    metadata_json: str | None
    created_at: str


class SettingsAuditRepository:
    """Persist settings audit events without exposing secrets."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create(
        self,
        *,
        actor_platform_user_id: str | None = None,
        actor_role: str | None = None,
        action: str,
        section: str | None = None,
        target_platform_user_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        platform: str = PLATFORM_MAX,
    ) -> SettingsAuditRecord:
        """Insert one audit row and return it."""

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO settings_audit_log (
                    platform, actor_platform_user_id, actor_role, action,
                    section, target_platform_user_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _optional_text(platform) or PLATFORM_MAX,
                    _optional_text(actor_platform_user_id),
                    _optional_text(actor_role),
                    _required_text(action),
                    _optional_text(section),
                    _optional_text(target_platform_user_id),
                    _safe_metadata_json(metadata),
                ),
            )
            connection.commit()
            record = self._get_by_id(connection, int(cursor.lastrowid))
            if record is None:
                raise RuntimeError("Created settings audit row was not found")
            return record

    def _get_by_id(self, connection: sqlite3.Connection, record_id: int) -> SettingsAuditRecord | None:
        row = connection.execute("SELECT * FROM settings_audit_log WHERE id = ? LIMIT 1", (record_id,)).fetchone()
        return _row_to_record(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_record(row: sqlite3.Row | None) -> SettingsAuditRecord | None:
    if row is None:
        return None
    return SettingsAuditRecord(
        id=int(row["id"]),
        platform=str(row["platform"]),
        actor_platform_user_id=_optional_text(row["actor_platform_user_id"]),
        actor_role=_optional_text(row["actor_role"]),
        action=str(row["action"]),
        section=_optional_text(row["section"]),
        target_platform_user_id=_optional_text(row["target_platform_user_id"]),
        metadata_json=_optional_text(row["metadata_json"]),
        created_at=str(row["created_at"]),
    )


def _safe_metadata_json(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, default=str)


def _required_text(value: str) -> str:
    clean = str(value).strip()
    if not clean:
        raise ValueError("Settings audit action must not be empty")
    return clean


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None
