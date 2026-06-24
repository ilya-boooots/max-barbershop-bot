"""Read-only access to the existing Telegram bot SQLite users table."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from max_barbershop_bot.repositories.users import PLATFORM_TELEGRAM
from max_barbershop_bot.services.phone_normalization import build_phone_match_keys, mask_phone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramDbDiagnostics:
    token_configured: bool = False
    db_path_configured: bool = False
    db_exists: bool = False
    db_readable: bool = False
    users_table_found: bool = False
    users_count: int = 0
    users_with_chat_id_count: int = 0
    users_with_phone_count: int = 0
    users_with_yclients_client_id_count: int = 0
    columns: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    identity_columns_by_table: dict[str, tuple[str, ...]] | None = None
    masked_phone_samples: tuple[str, ...] = ()
    normalized_phone_key_samples: tuple[str, ...] = ()
    unavailable_reason: str | None = None

@dataclass(frozen=True)
class TelegramUserRecord:
    """Normalized Telegram user shape used by omnichannel broadcasts."""

    id: int | None
    platform: str
    platform_user_id: str
    max_user_id: str | None = None
    chat_id: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    birthdate: str | None = None
    role: str = "user"
    yclients_client_id: str | None = None
    notifications_enabled: bool = True
    notification_settings: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    blocked: bool = False
    stopped: bool = False
    phone_keys: frozenset[str] = frozenset()


class TelegramUsersRepository:
    """Robust reader for Telegram reference DB without importing Telegram runtime."""

    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        self._columns_cache: set[str] | None = None

    @property
    def database_path(self) -> str:
        return self._database_path

    def is_available(self) -> bool:
        return bool(self._database_path and Path(self._database_path).exists())

    def inspect_database(self, *, token_configured: bool = False) -> TelegramDbDiagnostics:
        db_path_configured = bool(str(self._database_path or '').strip())
        path = Path(self._database_path) if db_path_configured else None
        try:
            db_exists = bool(path and path.exists())
            db_readable = bool(path and path.is_file() and path.stat().st_size >= 0) if db_exists else False
        except OSError:
            db_exists = bool(path)
            db_readable = False
        columns = self.get_columns() if db_readable else set()
        users_table_found = bool(columns)
        all_users = self.list_users_for_broadcast_audience() if users_table_found else []
        reason = None
        if not token_configured:
            reason = "token_missing"
        elif not db_path_configured:
            reason = "db_path_missing"
        elif not db_exists:
            reason = "db_not_found"
        elif not db_readable:
            reason = "db_unreadable"
        elif not users_table_found:
            reason = "users_table_missing"
        return TelegramDbDiagnostics(
            token_configured=token_configured,
            db_path_configured=db_path_configured,
            db_exists=db_exists,
            db_readable=db_readable,
            users_table_found=users_table_found,
            users_count=len(all_users),
            users_with_chat_id_count=sum(1 for u in all_users if u.chat_id or u.platform_user_id),
            users_with_phone_count=sum(1 for u in all_users if u.phone_keys),
            users_with_yclients_client_id_count=sum(1 for u in all_users if u.yclients_client_id),
            columns=tuple(sorted(columns)),
            tables=tuple(self.list_tables()),
            identity_columns_by_table=self.inspect_identity_columns(),
            masked_phone_samples=tuple(dict.fromkeys(mask_phone(u.phone) for u in all_users if u.phone))[:5],
            normalized_phone_key_samples=tuple(mask_phone(key) for key in sorted({key for u in all_users for key in u.phone_keys})[:5]),
            unavailable_reason=reason,
        )

    def get_columns(self) -> set[str]:
        if self._columns_cache is not None:
            return self._columns_cache
        if not self.is_available():
            self._columns_cache = set()
            return self._columns_cache
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("PRAGMA table_info(users)").fetchall()
        except sqlite3.Error as exc:
            logger.warning(
                "MAX Telegram broadcast diagnostic: telegram_db_configured=%s error_code=%s",
                bool(self._database_path),
                type(exc).__name__,
            )
            self._columns_cache = set()
            return self._columns_cache
        self._columns_cache = {str(row[1]) for row in rows}
        logger.info(
            "MAX Telegram broadcast diagnostic: telegram_db_configured=%s telegram_users_count=%s columns_count=%s",
            True,
            self.count_users(),
            len(self._columns_cache),
        )
        return self._columns_cache

    def count_users(self) -> int:
        if not self.is_available():
            return 0
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT COUNT(1) AS c FROM users").fetchone()
                return int(row["c"] or 0) if row else 0
        except sqlite3.Error:
            return 0

    def list_by_yclients_client_id(self, yclients_client_id: str, *, platform: str | None = PLATFORM_TELEGRAM) -> list[TelegramUserRecord]:
        if platform not in {None, PLATFORM_TELEGRAM} or not str(yclients_client_id or "").strip():
            return []
        wanted = str(yclients_client_id).strip()
        return [user for user in self.list_users_for_broadcast_audience(platform=PLATFORM_TELEGRAM) if user.yclients_client_id == wanted]

    def list_by_phone_keys(self, phone_keys: set[str], *, platform: str | None = PLATFORM_TELEGRAM) -> list[TelegramUserRecord]:
        if platform not in {None, PLATFORM_TELEGRAM} or not phone_keys:
            return []
        candidates = self.list_users_for_broadcast_audience(platform=PLATFORM_TELEGRAM)
        return [user for user in candidates if set(user.phone_keys or build_phone_match_keys(user.phone)) & phone_keys]

    def find_by_platform_user_id(self, platform_user_id: str, *, platform: str = PLATFORM_TELEGRAM) -> TelegramUserRecord | None:
        if platform != PLATFORM_TELEGRAM or not str(platform_user_id or "").strip():
            return None
        columns = self.get_columns()
        id_column = _first_existing(columns, ("user_id", "tg_id", "telegram_user_id", "chat_id"))
        if not id_column:
            return None
        wanted = str(platform_user_id).strip()
        for user in self.list_users_for_broadcast_audience(platform=PLATFORM_TELEGRAM):
            if user.platform_user_id == wanted or user.chat_id == wanted:
                return user
        rows = self._query(f"WHERE {id_column} = ?", (wanted,), limit=1)
        return rows[0] if rows else None

    def list_users_for_broadcast_audience(self, *, platform: str | None = PLATFORM_TELEGRAM) -> list[TelegramUserRecord]:
        if platform not in {None, PLATFORM_TELEGRAM}:
            return []
        return self._query("", ())

    def list_tables(self) -> list[str]:
        if not self.is_available():
            return []
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        except sqlite3.Error:
            return []
        return [str(row["name"]) for row in rows if str(row["name"]).strip()]

    def inspect_identity_columns(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        if not self.is_available():
            return result
        for table in self.list_tables():
            try:
                with closing(self._connect()) as connection:
                    rows = connection.execute(f"PRAGMA table_info({ _quote_identifier(table) })").fetchall()
            except sqlite3.Error:
                continue
            cols = [str(row[1]) for row in rows]
            identity = [c for c in cols if _is_identity_column(c)]
            if identity or any(word in table.lower() for word in ("user", "client", "phone", "yclients")):
                result[table] = tuple(identity or cols)
        return result

    def _query(self, where_sql: str, params: tuple[Any, ...], *, limit: int | None = None) -> list[TelegramUserRecord]:
        columns = self.get_columns()
        if not columns:
            return []
        limit_sql = f" LIMIT {max(1, int(limit))}" if limit else ""
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(f"SELECT * FROM users {where_sql} ORDER BY {_order_by(columns)}{limit_sql}", params).fetchall()
                extra = _load_related_identity_rows(connection)
        except sqlite3.Error as exc:
            logger.warning(
                "MAX Telegram broadcast diagnostic: telegram_db_configured=%s error_code=%s",
                bool(self._database_path),
                type(exc).__name__,
            )
            return []
        result = [_row_to_record(row, columns, extra) for row in rows]
        return [item for item in result if item is not None]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self._database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_record(row: sqlite3.Row, columns: set[str], extra: dict[str, dict[str, list[sqlite3.Row]]] | None = None) -> TelegramUserRecord | None:
    user_id = _value(row, columns, "user_id", "telegram_user_id", "tg_id", "chat_id")
    chat_id = _value(row, columns, "chat_id", "telegram_chat_id", "user_id", "telegram_user_id", "tg_id")
    if user_id is None or not str(user_id).strip():
        return None
    if chat_id is None or not str(chat_id).strip():
        return None
    related = _related_rows(row, columns, extra or {}, user_id, chat_id)
    notifications = _bool_value(_value(row, columns, "notifications_enabled", "broadcasts_enabled", "marketing_enabled"), default=True)
    blocked = _bool_value(_value(row, columns, "blocked", "is_blocked", "bot_blocked"), default=False)
    stopped = _bool_value(_value(row, columns, "stopped", "is_stopped", "bot_stopped"), default=False)
    name = _text(_value(row, columns, "display_name", "name", "full_name"))
    return TelegramUserRecord(
        id=_int_or_none(user_id),
        platform=PLATFORM_TELEGRAM,
        platform_user_id=str(user_id).strip(),
        chat_id=str(chat_id).strip(),
        display_name=name,
        username=_text(_value(row, columns, "username")),
        phone=_first_text(_identity_values(row, columns, related, _PHONE_FIELDS)),
        birthdate=_text(_value(row, columns, "birth_date", "birthdate")),
        role=_text(_value(row, columns, "role")) or "user",
        yclients_client_id=_first_text(_identity_values(row, columns, related, _CLIENT_ID_FIELDS)),
        notifications_enabled=notifications and not blocked and not stopped,
        notification_settings={},
        created_at=_text(_value(row, columns, "created_at")),
        updated_at=_text(_value(row, columns, "updated_at", "last_seen_at", "last_activity_ts_utc")),
        blocked=blocked,
        stopped=stopped,
        phone_keys=frozenset(_phone_keys_from_values(_identity_values(row, columns, related, _PHONE_FIELDS))),
    )


def _value(row: sqlite3.Row, columns: set[str], *names: str) -> Any:
    for name in names:
        if name in columns:
            return row[name]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _first_existing(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _order_by(columns: set[str]) -> str:
    for name in ("updated_at", "last_seen_at", "last_activity_ts_utc", "created_at", "user_id"):
        if name in columns:
            return f"{name} DESC"
    return "rowid DESC"

_PHONE_FIELDS = ("phone", "phone_raw", "phone_digits", "phone_e164", "phone_ru_7", "phone_ru_8", "contact_phone", "normalized_phone", "mobile", "tel", "telephone")
_CLIENT_ID_FIELDS = ("yclients_client_id", "client_id", "yclients_id", "yclients_user_id")
_LINK_FIELDS = ("user_id", "telegram_user_id", "tg_id", "chat_id", "telegram_chat_id")


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_identity_column(name: str) -> bool:
    low = name.lower()
    return low in set(_PHONE_FIELDS + _CLIENT_ID_FIELDS + _LINK_FIELDS) or "phone" in low or "yclients" in low or low.endswith("user_id") or low.endswith("chat_id")


def _load_related_identity_rows(connection: sqlite3.Connection) -> dict[str, dict[str, list[sqlite3.Row]]]:
    indexes: dict[str, dict[str, list[sqlite3.Row]]] = {"user_id": {}, "chat_id": {}}
    try:
        table_rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    except sqlite3.Error:
        return indexes
    for table_row in table_rows:
        table = str(table_row["name"])
        if table == "users":
            continue
        try:
            info = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        except sqlite3.Error:
            continue
        cols = {str(row[1]) for row in info}
        if not any(_is_identity_column(col) for col in cols):
            continue
        try:
            rows = connection.execute(f"SELECT * FROM {_quote_identifier(table)}").fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            for field in _LINK_FIELDS:
                if field not in cols:
                    continue
                value = _text(row[field])
                if not value:
                    continue
                bucket = "chat_id" if "chat" in field else "user_id"
                indexes[bucket].setdefault(value, []).append(row)
    return indexes


def _related_rows(row: sqlite3.Row, columns: set[str], extra: dict[str, dict[str, list[sqlite3.Row]]], user_id: Any, chat_id: Any) -> list[sqlite3.Row]:
    result: list[sqlite3.Row] = []
    for key in {_text(user_id), _text(chat_id)}:
        if not key:
            continue
        result.extend(extra.get("user_id", {}).get(key, []))
        result.extend(extra.get("chat_id", {}).get(key, []))
    return result


def _identity_values(row: sqlite3.Row, columns: set[str], related: list[sqlite3.Row], fields: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for field in fields:
        if field in columns:
            values.append(row[field])
    for related_row in related:
        related_cols = set(related_row.keys())
        for field in fields:
            if field in related_cols:
                values.append(related_row[field])
    return values


def _first_text(values: list[Any]) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _phone_keys_from_values(values: list[Any]) -> set[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(build_phone_match_keys(_text(value)))
    return keys
