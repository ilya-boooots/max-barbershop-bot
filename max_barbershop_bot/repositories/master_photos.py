"""SQLite repository for MAX master photos."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from max_barbershop_bot.repositories.users import PLATFORM_MAX


@dataclass(frozen=True)
class MasterPhoto:
    """Persisted MAX-compatible master photo reference."""

    id: int
    platform: str
    yclients_staff_id: str
    master_name: str | None
    photo_file_id: str | None
    photo_url: str | None
    photo_attachment_json: str | None
    is_active: bool
    created_by_platform_user_id: str | None
    updated_by_platform_user_id: str | None
    created_at: str
    updated_at: str


class MasterPhotosRepository:
    """Store reusable MAX photo references for YClients staff ids."""

    def __init__(self, database_path: str, *, platform: str = PLATFORM_MAX) -> None:
        self._database_path = database_path
        self._platform = platform

    def get_by_staff_id(self, yclients_staff_id: str) -> MasterPhoto | None:
        """Return an active photo row for one YClients staff id."""

        staff_id = _required_text(yclients_staff_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM master_photos
                WHERE platform = ? AND yclients_staff_id = ? AND is_active = 1
                LIMIT 1
                """,
                (self._platform, staff_id),
            ).fetchone()
            return _row_to_photo(row)

    def upsert_photo(
        self,
        yclients_staff_id: str,
        master_name: str | None,
        *,
        photo_file_id: str | None = None,
        photo_url: str | None = None,
        photo_attachment_json: str | None = None,
        actor_platform_user_id: str | None = None,
    ) -> MasterPhoto:
        """Create or replace an active photo reference for a YClients staff id."""

        staff_id = _required_text(yclients_staff_id)
        if not any(_optional_text(value) for value in (photo_file_id, photo_url, photo_attachment_json)):
            raise ValueError("Master photo reference must not be empty")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO master_photos (
                    platform, yclients_staff_id, master_name, photo_file_id, photo_url,
                    photo_attachment_json, is_active, created_by_platform_user_id,
                    updated_by_platform_user_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform, yclients_staff_id) DO UPDATE SET
                    master_name = excluded.master_name,
                    photo_file_id = excluded.photo_file_id,
                    photo_url = excluded.photo_url,
                    photo_attachment_json = excluded.photo_attachment_json,
                    is_active = 1,
                    updated_by_platform_user_id = excluded.updated_by_platform_user_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self._platform,
                    staff_id,
                    _optional_text(master_name),
                    _optional_text(photo_file_id),
                    _optional_text(photo_url),
                    _optional_text(photo_attachment_json),
                    _optional_text(actor_platform_user_id),
                    _optional_text(actor_platform_user_id),
                ),
            )
            connection.commit()
            record = self.get_by_staff_id(staff_id)
            if record is None:
                record = self._get_by_id(connection, int(cursor.lastrowid))
            if record is None:
                raise RuntimeError("Saved master photo row was not found")
            return record

    def delete_photo(self, yclients_staff_id: str, *, actor_platform_user_id: str | None = None) -> None:
        """Deactivate a master photo without storing binary data."""

        staff_id = _required_text(yclients_staff_id)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE master_photos
                SET is_active = 0,
                    updated_by_platform_user_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE platform = ? AND yclients_staff_id = ?
                """,
                (_optional_text(actor_platform_user_id), self._platform, staff_id),
            )
            connection.commit()

    def list_all(self) -> list[MasterPhoto]:
        """List all active master photo rows."""

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM master_photos
                WHERE platform = ? AND is_active = 1
                ORDER BY master_name COLLATE NOCASE, yclients_staff_id
                """,
                (self._platform,),
            ).fetchall()
            photos = [_row_to_photo(row) for row in rows]
            return [photo for photo in photos if photo is not None]

    def has_photo(self, yclients_staff_id: str) -> bool:
        """Return whether an active photo exists for a YClients staff id."""

        return self.get_by_staff_id(yclients_staff_id) is not None

    def _get_by_id(self, connection: sqlite3.Connection, row_id: int) -> MasterPhoto | None:
        row = connection.execute("SELECT * FROM master_photos WHERE id = ? LIMIT 1", (row_id,)).fetchone()
        return _row_to_photo(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_photo(row: sqlite3.Row | None) -> MasterPhoto | None:
    if row is None:
        return None
    return MasterPhoto(
        id=int(row["id"]),
        platform=str(row["platform"]),
        yclients_staff_id=str(row["yclients_staff_id"]),
        master_name=_optional_text(row["master_name"]),
        photo_file_id=_optional_text(row["photo_file_id"]),
        photo_url=_optional_text(row["photo_url"]),
        photo_attachment_json=_optional_text(row["photo_attachment_json"]),
        is_active=bool(row["is_active"]),
        created_by_platform_user_id=_optional_text(row["created_by_platform_user_id"]),
        updated_by_platform_user_id=_optional_text(row["updated_by_platform_user_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _required_text(value: object) -> str:
    clean = _optional_text(value)
    if not clean:
        raise ValueError("YClients staff id must not be empty")
    return clean


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None
