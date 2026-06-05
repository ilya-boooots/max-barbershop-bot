"""SQLite repository for YClients settings used by the MAX bot."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any

DEFAULT_BRANCH_TIMEZONE = "Europe/Moscow"

logger = logging.getLogger(__name__)
_UNSET = object()


@dataclass(frozen=True)
class YClientsSettings:
    """Stored YClients connection and branch settings."""

    id: int | None = None
    company_id: str | None = None
    partner_token: str | None = None
    user_token: str | None = None
    branch_timezone: str = DEFAULT_BRANCH_TIMEZONE
    branch_title: str | None = None
    contacts_override_json: str | None = None
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None

    def safe_dict(self) -> dict[str, Any]:
        """Return settings as a dict with tokens masked for safe logging."""

        return {
            "id": self.id,
            "company_id": self.company_id,
            "partner_token": _mask_secret(self.partner_token),
            "user_token": _mask_secret(self.user_token),
            "branch_timezone": self.branch_timezone,
            "branch_title": self.branch_title,
            "contacts_override_json": self.contacts_override_json,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def __repr__(self) -> str:
        """Show settings without exposing tokens in obvious string output."""

        fields = ", ".join(f"{key}={value!r}" for key, value in self.safe_dict().items())
        return f"YClientsSettings({fields})"


class YClientsSettingsRepository:
    """Simple sqlite3 repository for YClients settings."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path

    @property
    def database_path(self) -> str:
        """Return the SQLite path used by this repository for safe diagnostics."""

        return self._database_path

    def get_active(self) -> YClientsSettings | None:
        """Return the newest active settings row without creating one."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM yclients_settings
                WHERE is_active = 1
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            return _row_to_settings(row)

    def get_by_id(self, settings_id: int) -> YClientsSettings | None:
        """Return settings by database id."""

        with closing(self._connect()) as connection:
            return self._get_by_id(connection, settings_id)

    def create_settings(
        self,
        *,
        company_id: str | None,
        partner_token: str | None,
        user_token: str | None,
        branch_timezone: str = DEFAULT_BRANCH_TIMEZONE,
        branch_title: str | None = None,
        contacts_override_json: str | None = None,
        is_active: bool = True,
    ) -> YClientsSettings:
        """Insert a new YClients settings row and return it."""

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO yclients_settings (
                    company_id,
                    partner_token,
                    user_token,
                    branch_timezone,
                    branch_title,
                    contacts_override_json,
                    is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _optional_text(company_id),
                    _optional_text(partner_token),
                    _optional_text(user_token),
                    _timezone_or_default(branch_timezone),
                    _optional_text(branch_title),
                    _optional_text(contacts_override_json),
                    _bool_to_int(is_active),
                ),
            )
            connection.commit()
            created = self._get_by_id(connection, cursor.lastrowid)
            if created is None:
                raise RuntimeError("Created YClients settings row was not found")
            return created

    def update_settings(
        self,
        settings_id: int,
        *,
        company_id: str | None | object = _UNSET,
        partner_token: str | None | object = _UNSET,
        user_token: str | None | object = _UNSET,
        branch_timezone: str | object = _UNSET,
        branch_title: str | None | object = _UNSET,
        contacts_override_json: str | None | object = _UNSET,
        is_active: bool | object = _UNSET,
    ) -> YClientsSettings | None:
        """Update only provided settings fields and return the updated row."""

        assignments: list[str] = []
        values: list[Any] = []

        text_updates = {
            "company_id": company_id,
            "partner_token": partner_token,
            "user_token": user_token,
            "branch_title": branch_title,
            "contacts_override_json": contacts_override_json,
        }
        for column, value in text_updates.items():
            if value is not _UNSET:
                assignments.append(f"{column} = ?")
                values.append(_optional_text(value))

        if branch_timezone is not _UNSET:
            assignments.append("branch_timezone = ?")
            values.append(_timezone_or_default(branch_timezone))

        if is_active is not _UNSET:
            assignments.append("is_active = ?")
            values.append(_bool_to_int(is_active))

        if not assignments:
            return self.get_by_id(settings_id)

        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.append(settings_id)

        with closing(self._connect()) as connection:
            cursor = connection.execute(
                f"""
                UPDATE yclients_settings
                SET {", ".join(assignments)}
                WHERE id = ?
                """,
                tuple(values),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return None
            connection.commit()
            return self._get_by_id(connection, settings_id)

    def upsert_active_settings(
        self,
        *,
        company_id: str | None | object = _UNSET,
        partner_token: str | None | object = _UNSET,
        user_token: str | None | object = _UNSET,
        branch_timezone: str | object = _UNSET,
        branch_title: str | None | object = _UNSET,
        contacts_override_json: str | None | object = _UNSET,
        is_active: bool | object = _UNSET,
    ) -> YClientsSettings:
        """Update active settings or create a new active row when none exists."""

        active = self.get_active()
        if active is not None and active.id is not None:
            updated = self.update_settings(
                active.id,
                company_id=company_id,
                partner_token=partner_token,
                user_token=user_token,
                branch_timezone=branch_timezone,
                branch_title=branch_title,
                contacts_override_json=contacts_override_json,
                is_active=is_active,
            )
            if updated is None:
                raise RuntimeError("Active YClients settings row was not found during update")
            return updated

        return self.create_settings(
            company_id=None if company_id is _UNSET else company_id,
            partner_token=None if partner_token is _UNSET else partner_token,
            user_token=None if user_token is _UNSET else user_token,
            branch_timezone=(
                DEFAULT_BRANCH_TIMEZONE if branch_timezone is _UNSET else branch_timezone
            ),
            branch_title=None if branch_title is _UNSET else branch_title,
            contacts_override_json=(
                None if contacts_override_json is _UNSET else contacts_override_json
            ),
            is_active=True if is_active is _UNSET else is_active,
        )

    def deactivate(self, settings_id: int) -> YClientsSettings | None:
        """Mark settings as inactive and return the updated row."""

        return self.update_settings(settings_id, is_active=False)

    def get_branch_timezone(self, default: str = DEFAULT_BRANCH_TIMEZONE) -> str:
        """Return active branch timezone or a safe default."""

        active = self.get_active()
        if active is None or not active.branch_timezone:
            return default
        return active.branch_timezone

    def get_contacts_override(self) -> dict[str, Any]:
        """Return parsed active contacts override JSON, or an empty dict."""

        active = self.get_active()
        if active is None or not active.contacts_override_json:
            return {}

        try:
            parsed = json.loads(active.contacts_override_json)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid contacts override JSON in YClients settings id=%s",
                active.id,
            )
            return {}

        if isinstance(parsed, dict):
            return parsed

        logger.warning(
            "Contacts override JSON must be an object in YClients settings id=%s",
            active.id,
        )
        return {}

    def set_contacts_override(self, override: dict[str, Any]) -> YClientsSettings | None:
        """Save contacts override JSON into active settings, creating defaults if needed."""

        override_json = json.dumps(override, ensure_ascii=False)
        active = self.get_active()
        if active is None:
            return self.create_settings(
                company_id=None,
                partner_token=None,
                user_token=None,
                contacts_override_json=override_json,
            )
        if active.id is None:
            return None
        return self.update_settings(active.id, contacts_override_json=override_json)

    def _connect(self) -> sqlite3.Connection:
        """Open a sqlite connection for one repository operation."""

        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _get_by_id(
        self,
        connection: sqlite3.Connection,
        settings_id: int,
    ) -> YClientsSettings | None:
        row = connection.execute(
            """
            SELECT * FROM yclients_settings
            WHERE id = ?
            LIMIT 1
            """,
            (settings_id,),
        ).fetchone()
        return _row_to_settings(row)


def _row_to_settings(row: sqlite3.Row | None) -> YClientsSettings | None:
    if row is None:
        return None
    return YClientsSettings(
        id=row["id"],
        company_id=row["company_id"],
        partner_token=row["partner_token"],
        user_token=row["user_token"],
        branch_timezone=row["branch_timezone"] or DEFAULT_BRANCH_TIMEZONE,
        branch_title=row["branch_title"],
        contacts_override_json=row["contacts_override_json"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _mask_secret(value: str | None) -> str | None:
    if value is None:
        return None
    return "***"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _timezone_or_default(value: Any) -> str:
    if value is None:
        return DEFAULT_BRANCH_TIMEZONE
    text = str(value).strip()
    if not text:
        return DEFAULT_BRANCH_TIMEZONE
    return text


def _bool_to_int(value: Any) -> int:
    return 1 if bool(value) else 0
