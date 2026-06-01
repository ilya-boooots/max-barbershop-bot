from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from app.db.sqlite import execute, fetchall, fetchone
from app.repositories.users import get_user_by_tg_id
from app.utils.datetime import resolve_branch_timezone
from app.utils.staff import display_name, role_label

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _role_label_plain(role: str | None) -> str:
    label = role_label(role)
    return label.split(" ", 1)[1] if " " in label else label


async def _safe_branch_timezone() -> str | None:
    try:
        return await resolve_branch_timezone()
    except Exception:
        logger.warning("staff_action_log_timezone_resolve_failed", exc_info=True)
        return None


def _safe_metadata(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True)


async def log_staff_action(
    *,
    actor_tg_id: int,
    action_type: str,
    human_text: str,
    target_tg_id: int | None = None,
    target_name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor_name: str | None = None,
    actor_role: str | None = None,
    branch_timezone: str | None = None,
) -> None:
    """Store a human-readable staff action journal entry.

    action_type is technical and never rendered to Telegram users. human_text must be a
    complete business-language sentence without raw callback data/JSON/SQL.
    """

    actor = await get_user_by_tg_id(actor_tg_id)
    resolved_actor_name = actor_name or (display_name(actor) if actor else None)
    resolved_actor_role = actor_role or (actor.get("role") if actor else None)
    if actor_tg_id == 378881880 and not resolved_actor_role:
        resolved_actor_role = "developer"
    if target_tg_id is not None and target_name is None:
        target = await get_user_by_tg_id(target_tg_id)
        target_name = display_name(target) if target else None
    tz_name = branch_timezone or await _safe_branch_timezone()
    await execute(
        """
        INSERT INTO staff_action_logs (
            actor_tg_id,
            actor_name,
            actor_role,
            action_type,
            human_text,
            target_tg_id,
            target_name,
            metadata_json,
            created_at_utc,
            branch_timezone,
            action_text,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_tg_id,
            resolved_actor_name,
            resolved_actor_role,
            action_type,
            human_text.strip(),
            target_tg_id,
            target_name,
            _safe_metadata(metadata),
            _now_iso(),
            tz_name,
            human_text.strip(),
            _now_iso(),
        ),
    )


async def add_staff_action_log(
    actor_tg_id: int,
    action_text: str,
    *,
    target_tg_id: int | None = None,
    action_type: str = "staff_action",
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Backward-compatible wrapper for older handlers."""

    await log_staff_action(
        actor_tg_id=actor_tg_id,
        action_type=action_type,
        human_text=action_text,
        target_tg_id=target_tg_id,
        metadata=metadata,
    )


async def log_role_assigned(
    *,
    actor_tg_id: int,
    actor_role: str | None,
    target_tg_id: int,
    target_name: str,
    new_role: str,
    old_role: str | None = None,
) -> None:
    actor = await get_user_by_tg_id(actor_tg_id)
    actor_name = display_name(actor) if actor else "Сотрудник"
    human_text = (
        f"{_role_label_plain(actor_role)} {actor_name} назначил роль "
        f"«{_role_label_plain(new_role)}» пользователю {target_name}."
    )
    action_type = "role_changed" if old_role and old_role != "user" and old_role != new_role else "role_assigned"
    await log_staff_action(
        actor_tg_id=actor_tg_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action_type=action_type,
        human_text=human_text,
        target_tg_id=target_tg_id,
        target_name=target_name,
        metadata={"old_role": old_role or "user", "new_role": new_role},
    )


async def log_role_removed(
    *,
    actor_tg_id: int,
    actor_role: str | None,
    target_tg_id: int,
    target_name: str,
    old_role: str,
) -> None:
    actor = await get_user_by_tg_id(actor_tg_id)
    actor_name = display_name(actor) if actor else "Сотрудник"
    human_text = (
        f"{_role_label_plain(actor_role)} {actor_name} снял роль "
        f"«{_role_label_plain(old_role)}» с пользователя {target_name}."
    )
    await log_staff_action(
        actor_tg_id=actor_tg_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action_type="role_removed",
        human_text=human_text,
        target_tg_id=target_tg_id,
        target_name=target_name,
        metadata={"old_role": old_role, "new_role": "user"},
    )


async def count_staff_action_logs(staff_tg_id: int) -> int:
    row = await fetchone(
        """
        SELECT COUNT(1) AS total
        FROM staff_action_logs
        WHERE actor_tg_id = ? OR target_tg_id = ?
        """,
        (staff_tg_id, staff_tg_id),
    )
    if not row:
        return 0
    return int(row["total"])


async def get_staff_action_logs(staff_tg_id: int, *, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT actor_tg_id,
               actor_name,
               actor_role,
               action_type,
               COALESCE(human_text, action_text) AS human_text,
               target_tg_id,
               target_name,
               created_at_utc,
               COALESCE(created_at_utc, created_at) AS created_at,
               branch_timezone
        FROM staff_action_logs
        WHERE actor_tg_id = ? OR target_tg_id = ?
        ORDER BY COALESCE(created_at_utc, created_at) DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (staff_tg_id, staff_tg_id, limit, offset),
    )
    return [dict(row) for row in rows]
