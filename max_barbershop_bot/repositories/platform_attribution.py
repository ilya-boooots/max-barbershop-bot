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
    booking_phone: str | None = None
    source: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


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
        booking_phone: str | None = None,
        source: str | None = None,
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
                    marker,
                    booking_phone,
                    source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required_text(platform, "platform"),
                    _required_text(platform_user_id, "platform_user_id"),
                    _optional_text(yclients_record_id),
                    _optional_text(yclients_client_id),
                    _required_text(marker, "marker"),
                    _optional_text(booking_phone),
                    _optional_text(source),
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
        booking_phone: str | None = None,
        source: str | None = None,
    ) -> AttributionRecord:
        """Return an existing record for a YClients record id or create a new one."""

        if yclients_record_id is not None:
            existing_record = self.get_by_yclients_record_id(yclients_record_id)
            if existing_record is not None:
                self.update_record_metadata(
                    existing_record.id,
                    platform_user_id=platform_user_id,
                    yclients_client_id=yclients_client_id,
                    booking_phone=booking_phone,
                    source=source,
                )
                return self.get_by_id(existing_record.id) or existing_record

        return self.create_record(
            platform_user_id=platform_user_id,
            yclients_record_id=yclients_record_id,
            yclients_client_id=yclients_client_id,
            marker=marker,
            platform=platform,
            booking_phone=booking_phone,
            source=source,
        )


    def update_record_metadata(
        self,
        record_id: int | None,
        *,
        platform_user_id: str | None = None,
        yclients_client_id: str | None = None,
        booking_phone: str | None = None,
        source: str | None = None,
    ) -> None:
        """Safely enrich an existing attribution row without changing its YClients record id."""

        if record_id is None:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE platform_attribution
                SET platform_user_id = COALESCE(?, platform_user_id),
                    yclients_client_id = COALESCE(?, yclients_client_id),
                    booking_phone = COALESCE(?, booking_phone),
                    source = COALESCE(?, source),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    _optional_text(platform_user_id),
                    _optional_text(yclients_client_id),
                    _optional_text(booking_phone),
                    _optional_text(source),
                    record_id,
                ),
            )
            connection.commit()

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


    def list_with_yclients_record_ids(self, *, platform: str = PLATFORM_MAX, limit: int = 500) -> list[AttributionRecord]:
        """List latest attribution rows that can be verified against YClients records."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM platform_attribution
                WHERE platform = ? AND yclients_record_id IS NOT NULL AND TRIM(yclients_record_id) <> ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (_required_text(platform, "platform"), max(1, int(limit))),
            ).fetchall()
            return [_row_to_record(row) for row in rows if row is not None]


    def list_active_yclients_record_ids(
        self,
        *,
        platform: str = PLATFORM_MAX,
        yclients_record_ids: object | None = None,
    ) -> list[str]:
        """Return attributed YClients record ids for one platform, optionally limited to provided ids."""

        normalized_platform = _required_text(platform, "platform")
        candidate_ids = _normalize_record_ids(yclients_record_ids)
        with closing(self._connect()) as connection:
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                rows = connection.execute(
                    f"""
                    SELECT DISTINCT yclients_record_id
                    FROM platform_attribution
                    WHERE platform = ?
                      AND yclients_record_id IS NOT NULL
                      AND TRIM(yclients_record_id) <> ''
                      AND yclients_record_id IN ({placeholders})
                    """,
                    (normalized_platform, *candidate_ids),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT DISTINCT yclients_record_id
                    FROM platform_attribution
                    WHERE platform = ?
                      AND yclients_record_id IS NOT NULL
                      AND TRIM(yclients_record_id) <> ''
                    """,
                    (normalized_platform,),
                ).fetchall()
        return [str(row["yclients_record_id"]).strip() for row in rows if row["yclients_record_id"] is not None]

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
        _ensure_optional_columns(connection)
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


def _normalize_record_ids(values: object | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        iterable = [values]
    else:
        try:
            iterable = list(values)  # type: ignore[arg-type]
        except TypeError:
            iterable = [values]

    result: list[str] = []
    for value in iterable:
        text = str(value).strip() if value is not None else ""
        if text and text not in result:
            result.append(text)
    return result


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
        booking_phone=_row_optional_text(row, "booking_phone"),
        source=_row_optional_text(row, "source"),
        created_at=_row_optional_text(row, "created_at"),
        updated_at=_row_optional_text(row, "updated_at"),
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
    if column not in row.keys():
        return None
    value = row[column]
    return str(value) if value is not None else None


def _ensure_optional_columns(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute("PRAGMA table_info(platform_attribution)").fetchall()
    except sqlite3.OperationalError:
        return
    existing = {str(row[1]) for row in rows}
    statements = []
    if "booking_phone" not in existing:
        statements.append("ALTER TABLE platform_attribution ADD COLUMN booking_phone TEXT")
    if "source" not in existing:
        statements.append("ALTER TABLE platform_attribution ADD COLUMN source TEXT")
    if "updated_at" not in existing:
        statements.append("ALTER TABLE platform_attribution ADD COLUMN updated_at TEXT")
    for statement in statements:
        connection.execute(statement)
    if statements:
        connection.commit()
