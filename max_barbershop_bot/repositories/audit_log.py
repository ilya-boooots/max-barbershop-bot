"""Generic safe audit log repository for MAX operational events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from max_barbershop_bot.repositories.users import PLATFORM_MAX


@dataclass(frozen=True)
class AuditLogEntry:
    """Persisted audit log entry."""

    id: int | None
    event_type: str
    actor_platform_user_id: str | None
    target_platform_user_id: str | None
    target_max_user_id: str | None
    old_role: str | None
    new_role: str | None
    metadata: dict[str, Any]
    created_at: str | None


class AuditLogRepository:
    """SQLite-backed generic audit log repository."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create(
        self,
        *,
        event_type: str,
        actor_platform_user_id: str | None = None,
        target_platform_user_id: str | None = None,
        target_max_user_id: str | None = None,
        old_role: str | None = None,
        new_role: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        platform: str = PLATFORM_MAX,
    ) -> AuditLogEntry:
        """Store a safe audit event and return it."""

        del platform  # Reserved for future multi-platform filters; table stores platform-neutral ids.
        metadata_json = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_log (
                    event_type,
                    actor_platform_user_id,
                    target_platform_user_id,
                    target_max_user_id,
                    old_role,
                    new_role,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required_text(event_type, "event_type"),
                    _optional_text(actor_platform_user_id),
                    _optional_text(target_platform_user_id),
                    _optional_text(target_max_user_id),
                    _optional_text(old_role),
                    _optional_text(new_role),
                    metadata_json,
                ),
            )
            connection.commit()
            return self._get_by_id(connection, cursor.lastrowid)

    def list_recent(self, *, limit: int = 20) -> list[AuditLogEntry]:
        """Return latest audit events for smoke checks and future UI."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _get_by_id(self, connection: sqlite3.Connection, row_id: int) -> AuditLogEntry:
        row = connection.execute("SELECT * FROM audit_log WHERE id = ?", (row_id,)).fetchone()
        return _row_to_entry(row)


def _row_to_entry(row: sqlite3.Row) -> AuditLogEntry:
    metadata_raw = _optional_text(row["metadata_json"])
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return AuditLogEntry(
        id=int(row["id"]) if row["id"] is not None else None,
        event_type=str(row["event_type"]),
        actor_platform_user_id=_optional_text(row["actor_platform_user_id"]),
        target_platform_user_id=_optional_text(row["target_platform_user_id"]),
        target_max_user_id=_optional_text(row["target_max_user_id"]),
        old_role=_optional_text(row["old_role"]),
        new_role=_optional_text(row["new_role"]),
        metadata=metadata,
        created_at=_optional_text(row["created_at"]),
    )


def _required_text(value: str | None, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} не может быть пустым")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} не может быть пустым")
    return normalized


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
