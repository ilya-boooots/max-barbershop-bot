from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from app.core.security import CARD_NUMBER_PATTERN
from app.db.sqlite import execute, fetchone

CODE_TTL_SECONDS = 600
logger = logging.getLogger(__name__)


def _now_unix() -> int:
    return int(time.time())


def _generate_code() -> str:
    return f"{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"


def _normalize_code(code: str) -> str:
    return code.strip()


async def create_loyalty_code(client_id: int) -> dict[str, Any]:
    now = _now_unix()
    expires_at = now + CODE_TTL_SECONDS
    await execute(
        """
        UPDATE loyalty_codes
        SET expires_at_ts = ?
        WHERE client_id = ?
          AND used_at_ts IS NULL
          AND expires_at_ts >= ?
        """,
        (now - 1, client_id, now),
    )
    for _ in range(50):
        candidate = _generate_code()
        if not CARD_NUMBER_PATTERN.fullmatch(candidate):
            continue
        try:
            await execute(
                """
                INSERT INTO loyalty_codes (
                    code,
                    client_id,
                    created_at_ts,
                    expires_at_ts,
                    used_at_ts,
                    used_by_staff_id
                )
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (candidate, client_id, now, expires_at),
            )
            return {
                "code": candidate,
                "client_id": client_id,
                "created_at_ts": now,
                "expires_at_ts": expires_at,
            }
        except Exception:
            continue
    raise RuntimeError("Failed to generate unique loyalty code")


async def get_loyalty_code(code: str) -> dict[str, Any] | None:
    normalized = _normalize_code(code)
    row = await fetchone(
        """
        SELECT code,
               client_id,
               created_at_ts,
               expires_at_ts,
               used_at_ts,
               used_by_staff_id,
               used_action
        FROM loyalty_codes
        WHERE code = ?
        ORDER BY created_at_ts DESC
        LIMIT 1
        """,
        (normalized,),
    )
    return dict(row) if row else None


async def get_valid_loyalty_code(code: str, now: int | None = None) -> dict[str, Any] | None:
    normalized = _normalize_code(code)
    timestamp = _now_unix() if now is None else now
    row = await fetchone(
        """
        SELECT code,
               client_id,
               created_at_ts,
               expires_at_ts,
               used_at_ts,
               used_by_staff_id,
               used_action
        FROM loyalty_codes
        WHERE code = ?
          AND used_at_ts IS NULL
          AND expires_at_ts >= ?
        ORDER BY created_at_ts DESC
        LIMIT 1
        """,
        (normalized, timestamp),
    )
    if not row:
        latest = await fetchone(
            """
            SELECT code,
                   expires_at_ts,
                   used_at_ts
            FROM loyalty_codes
            WHERE code = ?
            ORDER BY created_at_ts DESC
            LIMIT 1
            """,
            (normalized,),
        )
        if latest and latest["expires_at_ts"] is not None and latest["expires_at_ts"] < timestamp:
            logger.debug(
                "Loyalty code expired for %s (now_ts=%s, expires_at_ts=%s)",
                normalized,
                timestamp,
                latest["expires_at_ts"],
            )
    return dict(row) if row else None


async def consume_loyalty_code(
    code: str,
    staff_id: int,
    action: str,
    now: int | None = None,
) -> bool:
    normalized = _normalize_code(code)
    timestamp = _now_unix() if now is None else now
    await execute(
        """
        UPDATE loyalty_codes
        SET used_at_ts = ?,
            used_by_staff_id = ?,
            used_action = ?
        WHERE code = ?
          AND used_at_ts IS NULL
          AND expires_at_ts >= ?
        """,
        (timestamp, staff_id, action, normalized, timestamp),
    )
    row = await fetchone(
        """
        SELECT used_at_ts
        FROM loyalty_codes
        WHERE code = ?
          AND used_at_ts = ?
          AND used_by_staff_id = ?
          AND used_action = ?
        """,
        (normalized, timestamp, staff_id, action),
    )
    return bool(row and row["used_at_ts"])
