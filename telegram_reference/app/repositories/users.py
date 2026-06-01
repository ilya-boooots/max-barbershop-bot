from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
import time
from typing import Any

from app.core.permissions import DEVELOPER_TG_ID
from app.repositories.staff_roles import get_role as get_staff_role, list_staff as list_staff_roles, remove_role as remove_staff_role, set_role as set_staff_role

PROTECTED_DEVELOPER_NAME = "🧑‍💻 Разработчик"
from app.db.sqlite import execute, fetchall, fetchone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_telegram_user(
    *,
    tg_id: int,
    username: str | None,
    phone: str | None = None,
    name: str | None = None,
) -> None:
    timestamp = _now()
    clean_username = (username or "").strip().lower() or None
    clean_phone = (phone or "").strip()
    clean_name = (name or "").strip() or "Пользователь"
    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            phone,
            name,
            birth_date,
            gender,
            is_registered,
            loyalty_balance,
            bonus_balance,
            first_purchase_done,
            created_at,
            updated_at,
            last_activity_ts_utc,
            last_seen_at,
            notifications_enabled
        )
        VALUES (?, ?, ?, ?, '', '', 0, 0, 0, 0, ?, ?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            phone = CASE
                WHEN excluded.phone != '' THEN excluded.phone
                ELSE users.phone
            END,
            name = CASE
                WHEN users.name = '' OR users.name = 'Пользователь' THEN excluded.name
                ELSE users.name
            END,
            updated_at = excluded.updated_at,
            last_activity_ts_utc = excluded.last_activity_ts_utc,
            last_seen_at = excluded.last_seen_at
        """,
        (tg_id, clean_username, clean_phone, clean_name, timestamp, timestamp, timestamp, timestamp),
    )


async def touch_user_activity(user_id: int) -> None:
    await execute(
        """
        UPDATE users
        SET last_activity_ts_utc = ?,
            last_seen_at = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (_now(), _now(), _now(), user_id),
    )


async def count_registered_clients() -> int:
    row = await fetchone(
        """
        SELECT COUNT(*) AS total
        FROM users u
        WHERE u.is_registered = 1
          AND COALESCE(u.role, 'user') NOT IN ('developer', 'manager', 'admin')
          AND NOT EXISTS (
              SELECT 1
              FROM staff_roles sr
              WHERE sr.tg_id = u.user_id
                AND sr.role IN ('developer', 'manager', 'admin')
          )
        """
    )
    return int(row["total"] or 0) if row else 0

async def get_user(user_id: int) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT user_id,
               username,
               phone,
               name,
               display_name,
               birth_date,
               gender,
               is_registered,
               loyalty_balance,
               bonus_balance,
               first_purchase_done,
               role,
               role_assigned_at,
               role_assigned_by_tg_id,
               card_number,
               card_created_at,
               created_at,
               updated_at,
               yclients_client_id,
               phone_raw,
               phone_digits,
               phone_e164,
               phone_ru_7,
               phone_ru_8,
               phone_matched_at,
               phone_match_source,
               last_activity_ts_utc,
               notifications_enabled,
               last_seen_at,
               registration_success_message_shown_at_utc
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )
    if row is None:
        return None
    return dict(row)


async def upsert_registered_user(
    user_id: int,
    phone: str,
    name: str,
    birth_date: str,
    gender: str,
    username: str | None = None,
) -> int:
    timestamp = _now()
    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            phone,
            name,
            birth_date,
            gender,
            is_registered,
            loyalty_balance,
            bonus_balance,
            first_purchase_done,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            phone = excluded.phone,
            name = excluded.name,
            birth_date = excluded.birth_date,
            gender = excluded.gender,
            is_registered = 1,
            updated_at = excluded.updated_at
        """,
        (user_id, username, phone, name, birth_date, gender, timestamp, timestamp),
    )
    return int(user_id)


async def upsert_registration_profile(
    *,
    tg_user_id: int,
    name: str,
    birthdate_iso: str,
    phone: str,
    username: str | None = None,
    yclients_client_id: int | None = None,
    phone_raw: str | None = None,
    phone_digits: str | None = None,
    phone_e164: str | None = None,
    phone_ru_7: str | None = None,
    phone_ru_8: str | None = None,
    match_source: str | None = None,
) -> None:
    timestamp = _now()
    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            phone,
            name,
            birth_date,
            gender,
            is_registered,
            loyalty_balance,
            bonus_balance,
            first_purchase_done,
            yclients_client_id,
            phone_raw,
            phone_digits,
            phone_e164,
            phone_ru_7,
            phone_ru_8,
            phone_matched_at,
            phone_match_source,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, '', 1, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            phone = excluded.phone,
            name = excluded.name,
            birth_date = excluded.birth_date,
            is_registered = 1,
            yclients_client_id = COALESCE(excluded.yclients_client_id, users.yclients_client_id),
            phone_raw = COALESCE(excluded.phone_raw, users.phone_raw),
            phone_digits = COALESCE(excluded.phone_digits, users.phone_digits),
            phone_e164 = COALESCE(excluded.phone_e164, users.phone_e164),
            phone_ru_7 = COALESCE(excluded.phone_ru_7, users.phone_ru_7),
            phone_ru_8 = COALESCE(excluded.phone_ru_8, users.phone_ru_8),
            phone_matched_at = COALESCE(excluded.phone_matched_at, users.phone_matched_at),
            phone_match_source = COALESCE(excluded.phone_match_source, users.phone_match_source),
            updated_at = excluded.updated_at
        """,
        (
            tg_user_id,
            (username or "").strip().lower() or None,
            phone,
            name,
            birthdate_iso,
            yclients_client_id,
            (phone_raw or "").strip() or None,
            (phone_digits or "").strip() or None,
            (phone_e164 or "").strip() or None,
            (phone_ru_7 or "").strip() or None,
            (phone_ru_8 or "").strip() or None,
            timestamp if phone_e164 else None,
            (match_source or "").strip() or None,
            timestamp,
            timestamp,
        ),
    )


async def set_loyalty_balance(user_id: int, new_balance: int) -> None:
    await execute(
        """
        UPDATE users
        SET loyalty_balance = ?,
            bonus_balance = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (new_balance, new_balance, _now(), user_id),
    )


async def add_loyalty_balance(user_id: int, delta: int) -> None:
    await execute(
        """
        UPDATE users
        SET loyalty_balance = loyalty_balance + ?,
            bonus_balance = bonus_balance + ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (delta, delta, _now(), user_id),
    )


async def mark_registration_success_message_shown(user_id: int) -> None:
    await execute(
        """
        UPDATE users
        SET registration_success_message_shown_at_utc = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (_now(), _now(), user_id),
    )


async def set_first_purchase_done(user_id: int, value: bool) -> None:
    await execute(
        """
        UPDATE users
        SET first_purchase_done = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (1 if value else 0, _now(), user_id),
    )


async def get_role(user_id: int) -> str | None:
    return await get_staff_role(user_id)


async def get_developer_ids() -> list[int]:
    rows = await fetchall("SELECT tg_id FROM staff_roles WHERE role = 'developer'")
    return [row["tg_id"] for row in rows]


async def ensure_protected_developer() -> None:
    timestamp = _now()
    await execute(
        """
        INSERT INTO users (
            user_id,
            username,
            phone,
            name,
            display_name,
            birth_date,
            gender,
            is_registered,
            loyalty_balance,
            bonus_balance,
            first_purchase_done,
            role,
            role_assigned_at,
            role_assigned_by_tg_id,
            created_at,
            updated_at
        )
        VALUES (?, NULL, '', ?, ?, '', '', 0, 0, 0, 0, 'developer', ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            display_name = excluded.display_name,
            role = 'developer',
            role_assigned_at = excluded.role_assigned_at,
            role_assigned_by_tg_id = excluded.role_assigned_by_tg_id,
            updated_at = excluded.updated_at
        """,
        (
            DEVELOPER_TG_ID,
            PROTECTED_DEVELOPER_NAME,
            PROTECTED_DEVELOPER_NAME,
            timestamp,
            DEVELOPER_TG_ID,
            timestamp,
            timestamp,
        ),
    )
    await set_staff_role(DEVELOPER_TG_ID, "developer", DEVELOPER_TG_ID)


async def set_role(user_id: int, role: str) -> None:
    if role in {"developer", "admin", "manager"}:
        await set_staff_role(user_id, role, DEVELOPER_TG_ID)
    else:
        await remove_staff_role(user_id)


async def get_user_by_tg_id(tg_id: int) -> dict[str, Any] | None:
    return await get_user(tg_id)


async def set_user_role(
    target_tg_id: int,
    new_role: str,
    assigned_by_tg_id: int,
    assigned_at_iso: str,
) -> None:
    if target_tg_id == DEVELOPER_TG_ID:
        return
    if new_role in {"developer", "admin", "manager"}:
        await set_staff_role(target_tg_id, new_role, assigned_by_tg_id)
    else:
        await remove_staff_role(target_tg_id)
    await execute(
        """
        UPDATE users
        SET role = ?,
            role_assigned_at = ?,
            role_assigned_by_tg_id = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (new_role if new_role in {"developer", "admin", "manager"} else "user", assigned_at_iso, assigned_by_tg_id, _now(), target_tg_id),
    )


async def set_staff_display_name(target_tg_id: int, display_name: str) -> None:
    if target_tg_id == DEVELOPER_TG_ID:
        return
    await execute(
        """
        UPDATE users
        SET display_name = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (display_name, _now(), target_tg_id),
    )


async def reset_registration(user_id: int) -> None:
    await execute(
        """
        UPDATE users
        SET is_registered = 0,
            updated_at = ?
        WHERE user_id = ?
        """,
        (_now(), user_id),
    )


async def get_user_by_card_number(card_number: str) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT user_id,
               username,
               phone,
               name,
               display_name,
               birth_date,
               gender,
               is_registered,
               loyalty_balance,
               bonus_balance,
               first_purchase_done,
               role,
               role_assigned_at,
               role_assigned_by_tg_id,
               card_number,
               card_created_at,
               card_used_at,
               created_at,
               updated_at
        FROM users
        WHERE card_number = ?
        """,
        (card_number,),
    )
    if row is None:
        return None
    return dict(row)


async def get_user_by_phone(phone: str) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT user_id,
               username,
               phone,
               name,
               display_name,
               birth_date,
               gender,
               is_registered,
               loyalty_balance,
               bonus_balance,
               first_purchase_done,
               role,
               role_assigned_at,
               role_assigned_by_tg_id,
               card_number,
               card_created_at,
               card_used_at,
               created_at,
               updated_at
        FROM users
        WHERE phone = ?
        LIMIT 1
        """,
        (phone,),
    )
    if row is None:
        return None
    return dict(row)


async def find_user_by_phone(phone: str) -> dict[str, Any] | None:
    """Backward-compatible alias used by notification services."""
    return await get_user_by_phone(phone)


async def find_other_user_by_phone_keys(
    *,
    current_user_id: int,
    phone_e164: str | None,
    phone_ru_7: str | None,
    phone_ru_8: str | None,
) -> dict[str, Any] | None:
    values = [value for value in (phone_e164, phone_ru_7, phone_ru_8) if value]
    if not values:
        return None
    placeholders = ", ".join(["?"] * len(values))
    row = await fetchone(
        f"""
        SELECT user_id,
               username,
               phone,
               name,
               yclients_client_id
        FROM users
        WHERE user_id != ?
          AND (
              phone IN ({placeholders})
              OR phone_e164 IN ({placeholders})
              OR phone_ru_7 IN ({placeholders})
              OR phone_ru_8 IN ({placeholders})
          )
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (current_user_id, *values, *values, *values, *values),
    )
    return dict(row) if row else None


async def find_other_user_by_yclients_client_id(
    *,
    current_user_id: int,
    yclients_client_id: int,
) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT user_id,
               username,
               phone,
               name,
               yclients_client_id
        FROM users
        WHERE user_id != ?
          AND yclients_client_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (current_user_id, yclients_client_id),
    )
    return dict(row) if row else None


async def update_profile_name_phone(
    *,
    user_id: int,
    name: str | None = None,
    phone: str | None = None,
    phone_raw: str | None = None,
    phone_digits: str | None = None,
    phone_e164: str | None = None,
    phone_ru_7: str | None = None,
    phone_ru_8: str | None = None,
    match_source: str | None = None,
) -> None:
    await execute(
        """
        UPDATE users
        SET name = COALESCE(?, name),
            phone = COALESCE(?, phone),
            phone_raw = COALESCE(?, phone_raw),
            phone_digits = COALESCE(?, phone_digits),
            phone_e164 = COALESCE(?, phone_e164),
            phone_ru_7 = COALESCE(?, phone_ru_7),
            phone_ru_8 = COALESCE(?, phone_ru_8),
            phone_matched_at = CASE WHEN ? IS NOT NULL THEN ? ELSE phone_matched_at END,
            phone_match_source = COALESCE(?, phone_match_source),
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            (name or "").strip() or None,
            (phone or "").strip() or None,
            (phone_raw or "").strip() or None,
            (phone_digits or "").strip() or None,
            (phone_e164 or "").strip() or None,
            (phone_ru_7 or "").strip() or None,
            (phone_ru_8 or "").strip() or None,
            (phone_e164 or "").strip() or None,
            _now(),
            (match_source or "").strip() or None,
            _now(),
            user_id,
        ),
    )


async def update_profile_phone_and_mapping(
    *,
    user_id: int,
    phone: str,
    phone_raw: str | None,
    phone_digits: str | None,
    phone_e164: str | None,
    phone_ru_7: str | None,
    phone_ru_8: str | None,
    yclients_client_id: int | None,
    match_source: str | None = None,
) -> None:
    await execute(
        """
        UPDATE users
        SET phone = ?,
            phone_raw = COALESCE(?, phone_raw),
            phone_digits = COALESCE(?, phone_digits),
            phone_e164 = COALESCE(?, phone_e164),
            phone_ru_7 = COALESCE(?, phone_ru_7),
            phone_ru_8 = COALESCE(?, phone_ru_8),
            yclients_client_id = COALESCE(?, yclients_client_id),
            phone_matched_at = CASE WHEN ? IS NOT NULL THEN ? ELSE phone_matched_at END,
            phone_match_source = COALESCE(?, phone_match_source),
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            (phone or "").strip(),
            (phone_raw or "").strip() or None,
            (phone_digits or "").strip() or None,
            (phone_e164 or "").strip() or None,
            (phone_ru_7 or "").strip() or None,
            (phone_ru_8 or "").strip() or None,
            yclients_client_id,
            (phone_e164 or "").strip() or None,
            _now(),
            (match_source or "").strip() or None,
            _now(),
            user_id,
        ),
    )


async def search_users_by_phone_suffix(suffix: str) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT u.user_id,
               u.username,
               u.phone,
               u.name,
               u.display_name,
               u.birth_date,
               u.gender,
               u.is_registered,
               u.loyalty_balance,
               u.bonus_balance,
               u.first_purchase_done,
               u.role,
               u.role_assigned_at,
               u.role_assigned_by_tg_id,
               u.card_number,
               u.card_created_at,
               u.card_used_at,
               u.created_at,
               u.updated_at
        FROM users u
        JOIN (
            SELECT phone, MAX(updated_at) AS max_updated_at
            FROM users
            WHERE phone LIKE ?
            GROUP BY phone
        ) latest
            ON latest.phone = u.phone AND latest.max_updated_at = u.updated_at
        ORDER BY u.updated_at DESC
        """,
        (f"%{suffix}",),
    )
    return [dict(row) for row in rows]


async def card_number_exists(card_number: str) -> bool:
    row = await fetchone("SELECT 1 FROM users WHERE card_number = ? LIMIT 1", (card_number,))
    return row is not None


async def set_card_number(user_id: int, card_number: str) -> None:
    now_ts = int(time.time())
    await execute(
        """
        UPDATE users
        SET card_number = ?,
            card_created_at = COALESCE(card_created_at, ?),
            card_used_at = NULL,
            updated_at = ?
        WHERE user_id = ?
        """,
        (card_number, now_ts, _now(), user_id),
    )


async def set_card_issue_timestamp(user_id: int, issued_at: int | None = None) -> None:
    issued_ts = int(time.time()) if issued_at is None else issued_at
    await execute(
        """
        UPDATE users
        SET card_created_at = ?,
            card_used_at = NULL,
            updated_at = ?
        WHERE user_id = ?
        """,
        (issued_ts, _now(), user_id),
    )


async def set_username(user_id: int, username: str | None) -> None:
    await upsert_telegram_user(tg_id=user_id, username=username)


async def find_user_by_identifier(identifier: str) -> dict[str, Any] | None:
    value = identifier.strip()
    if not value:
        return None
    if value.startswith("@"):
        row = await fetchone(
            "SELECT * FROM users WHERE lower(COALESCE(username, '')) = ? ORDER BY updated_at DESC LIMIT 1",
            (value[1:].strip().lower(),),
        )
        return dict(row) if row else None
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits and value.lstrip("+").isdigit():
        if len(digits) >= 9:
            phone = digits
            if phone.startswith("8") and len(phone) == 11:
                phone = "7" + phone[1:]
            if len(phone) == 11 and not phone.startswith("+"):
                phone = f"+{phone}"
            row = await fetchone(
                "SELECT * FROM users WHERE phone = ? ORDER BY updated_at DESC LIMIT 1",
                (phone,),
            )
            if row:
                return dict(row)
        if len(digits) >= 6:
            row = await fetchone(
                "SELECT * FROM users WHERE CAST(user_id AS TEXT) = ? ORDER BY updated_at DESC LIMIT 1",
                (digits,),
            )
            return dict(row) if row else None
    row = await fetchone(
        "SELECT * FROM users WHERE lower(COALESCE(username, '')) = ? ORDER BY updated_at DESC LIMIT 1",
        (value.lower(),),
    )
    return dict(row) if row else None


async def get_hostess_ids() -> list[int]:
    rows = await fetchall(
        "SELECT tg_id FROM staff_roles WHERE role IN ('manager', 'admin', 'developer')"
    )
    return [int(row["tg_id"]) for row in rows]


async def get_all_user_ids() -> list[int]:
    rows = await fetchall("SELECT user_id FROM users")
    return [int(row["user_id"]) for row in rows]


async def get_segment_user_ids(segment: str) -> list[int]:
    if segment == "all_opt_in":
        rows = await fetchall(
            "SELECT user_id FROM users WHERE user_id IS NOT NULL AND notifications_enabled = 1"
        )
        return [int(row["user_id"]) for row in rows]

    cutoff_map = {
        "active_7": (7, ">="),
        "active_30": (30, ">="),
        "inactive_90": (90, "<"),
    }
    config = cutoff_map.get(segment)
    if not config:
        return []

    days, comparator = config
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await fetchall(
        f"""
        SELECT user_id
        FROM users
        WHERE user_id IS NOT NULL
          AND notifications_enabled = 1
          AND COALESCE(last_seen_at, last_activity_ts_utc, created_at) {comparator} ?
        """,
        (cutoff,),
    )
    return [int(row["user_id"]) for row in rows]


async def set_notifications_enabled(user_id: int, enabled: bool) -> None:
    await execute(
        """
        UPDATE users
        SET notifications_enabled = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (1 if enabled else 0, _now(), user_id),
    )


async def get_notifications_enabled(user_id: int) -> bool:
    row = await fetchone("SELECT notifications_enabled FROM users WHERE user_id = ?", (user_id,))
    return bool(row and int(row["notifications_enabled"]) == 1)


async def set_card_used_at(user_id: int, used_at: int | None = None) -> None:
    used_ts = int(time.time()) if used_at is None else used_at
    await execute(
        """
        UPDATE users
        SET card_used_at = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (used_ts, _now(), user_id),
    )


async def reset_user_data(user_id: int) -> None:
    await execute(
        """
        UPDATE users
        SET phone = '',
            name = '',
            birth_date = '',
            gender = '',
            is_registered = 0,
            loyalty_balance = 0,
            bonus_balance = 0,
            first_purchase_done = 0,
            updated_at = ?
        WHERE user_id = ?
        """,
        (_now(), user_id),
    )


async def get_by_tg_id(tg_id: int) -> dict[str, Any] | None:
    return await get_user(tg_id)


async def list_staff() -> list[dict[str, Any]]:
    rows = await list_staff_roles()
    for row in rows:
        row["user_id"] = row.get("tg_id")
        row["role_assigned_at"] = row.get("assigned_at")
        row["role_assigned_by_tg_id"] = row.get("assigned_by")
    return rows


async def search_staff_by_name(query: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT user_id,
               name,
               display_name,
               role
        FROM users
        WHERE role IN ('developer', 'manager', 'admin')
          AND lower(COALESCE(display_name, name)) LIKE lower(?)
        ORDER BY COALESCE(display_name, name) ASC
        LIMIT ?
        """,
        (f"%{query}%", limit),
    )
    return [dict(row) for row in rows]


async def get_users_by_ids(user_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not user_ids:
        return {}
    placeholders = ", ".join(["?"] * len(user_ids))
    rows = await fetchall(
        f"""
        SELECT user_id,
               name,
               display_name,
               role
        FROM users
        WHERE user_id IN ({placeholders})
        """,
        tuple(user_ids),
    )
    return {row["user_id"]: dict(row) for row in rows}


async def update_balance_delta(tg_id: int, delta: int) -> None:
    await add_loyalty_balance(tg_id, delta)


async def is_registration_success_message_shown(user_id: int) -> bool:
    row = await fetchone(
        "SELECT registration_success_message_shown_at_utc FROM users WHERE user_id = ?",
        (user_id,),
    )
    if row is None:
        return False
    return bool(row["registration_success_message_shown_at_utc"])
