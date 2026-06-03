"""Platform-neutral attribution repository for YClients records."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

PLATFORM_MAX = "max"
DEFAULT_ATTRIBUTION_MARKER = "Клиент записался из MAX бота"


@dataclass(frozen=True)
class AttributionRecord:
    """Persisted link between a platform user and a YClients record."""

    id: int | None
    platform: str = PLATFORM_MAX
    platform_user_id: str = ""
    yclients_record_id: str | None = None
    yclients_client_id: str | None = None
    marker: str = DEFAULT_ATTRIBUTION_MARKER
    created_at: str | None = None


class PlatformAttributionRepository:
    """SQLite-backed repository for platform attribution rows."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    def create_record(
        self,
        platform_user_id: str,
        yclients_record_id: str | None,
        yclients_client_id: str | None = None,
        marker: str = DEFAULT_ATTRIBUTION_MARKER,
        platform: str = PLATFORM_MAX,
    ) -> AttributionRecord:
        """Create an attribution row and return the persisted record."""

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO platform_attribution (
                    platform,
                    platform_user_id,
                    yclients_record_id,
                    yclients_client_id,
                    marker
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _required_text(platform, "platform"),
                    _required_text(platform_user_id, "platform_user_id"),
                    _optional_text(yclients_record_id),
                    _optional_text(yclients_client_id),
                    _required_text(marker, "marker"),
                ),
            )
            connection.commit()
            return self._get_by_id(connection, cursor.lastrowid)

    def create_if_missing(
        self,
        platform_user_id: str,
        yclients_record_id: str | None,
        yclients_client_id: str | None = None,
        marker: str = DEFAULT_ATTRIBUTION_MARKER,
        platform: str = PLATFORM_MAX,
    ) -> AttributionRecord:
        """Return an existing record for a YClients record id or create a new one."""

        if yclients_record_id is not None:
            existing_record = self.get_by_yclients_record_id(yclients_record_id)
            if existing_record is not None:
                return existing_record

        return self.create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=yclients_client_id,
            marker=marker,
            platform=platform,
        )

    def get_by_id(self, record_id: int) -> AttributionRecord | None:
        """Find an attribution row by its database id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM platform_attribution
                WHERE id = ?
                LIMIT 1
                """,
                (record_id,),
            ).fetchone()
            return _row_to_record(row)

    def get_by_yclients_record_id(self, yclients_record_id: str) -> AttributionRecord | None:
        """Find the latest attribution row for a YClients record id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM platform_attribution
                WHERE yclients_record_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (_required_text(yclients_record_id, "yclients_record_id"),),
            ).fetchone()
            return _row_to_record(row)

    def list_by_platform_user_id(
        self,
        platform_user_id: str,
        platform: str = PLATFORM_MAX,
    ) -> list[AttributionRecord]:
        """List attribution rows for one platform-scoped user."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM platform_attribution
                WHERE platform = ? AND platform_user_id = ?
                ORDER BY id DESC
                """,
                (
                    _required_text(platform, "platform"),
                    _required_text(platform_user_id, "platform_user_id"),
                ),
            ).fetchall()
            return [_row_to_record(row) for row in rows if row is not None]

    def list_by_yclients_client_id(self, yclients_client_id: str) -> list[AttributionRecord]:
        """List attribution rows for one YClients client id."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM platform_attribution
                WHERE yclients_client_id = ?
                ORDER BY id DESC
                """,
                (_required_text(yclients_client_id, "yclients_client_id"),),
            ).fetchall()
            return [_row_to_record(row) for row in rows if row is not None]

    def exists_for_yclients_record(self, yclients_record_id: str) -> bool:
        """Return True when attribution exists for a YClients record id."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM platform_attribution
                WHERE yclients_record_id = ?
                LIMIT 1
                """,
                (_required_text(yclients_record_id, "yclients_record_id"),),
            ).fetchone()
            return row is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def _get_by_id(self, connection: sqlite3.Connection, record_id: int) -> AttributionRecord:
        row = connection.execute(
            "SELECT * FROM platform_attribution WHERE id = ?",
            (record_id,),
        ).fetchone()
        record = _row_to_record(row)
        if record is None:
            raise RuntimeError("Созданная запись атрибуции не найдена в базе данных")
        return record


def _row_to_record(row: sqlite3.Row | None) -> AttributionRecord | None:
    if row is None:
        return None

    return AttributionRecord(
        id=int(row["id"]),
        platform=str(row["platform"]),
        platform_user_id=str(row["platform_user_id"]),
        yclients_record_id=_row_optional_text(row, "yclients_record_id"),
        yclients_client_id=_row_optional_text(row, "yclients_client_id"),
        marker=str(row["marker"]),
        created_at=_row_optional_text(row, "created_at"),
    )


def _required_text(value: str, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} не может быть пустым")
    return value


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _row_optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = row[column]
    return str(value) if value is not None else None
