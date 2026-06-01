from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchall, fetchone
from app.repositories.diagnostics import log_bot_event, log_user_event


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_open_thread(user_id: int, staff_id: int | None = None) -> dict[str, Any]:
    row = await fetchone(
        """
        SELECT * FROM message_threads
        WHERE user_id = ? AND status = 'open'
        ORDER BY updated_ts DESC
        LIMIT 1
        """,
        (user_id,),
    )
    if row:
        return dict(row)
    ts = now_iso()
    await execute(
        """
        INSERT INTO message_threads (user_id, status, created_ts, updated_ts, last_staff_id)
        VALUES (?, 'open', ?, ?, ?)
        """,
        (user_id, ts, ts, staff_id),
    )
    created = await fetchone("SELECT * FROM message_threads ORDER BY id DESC LIMIT 1")
    return dict(created)


async def get_thread(thread_id: int) -> dict[str, Any] | None:
    row = await fetchone("SELECT * FROM message_threads WHERE id = ?", (thread_id,))
    return dict(row) if row else None


async def set_thread_status(thread_id: int, status: str) -> None:
    await execute(
        "UPDATE message_threads SET status = ?, updated_ts = ? WHERE id = ?",
        (status, now_iso(), thread_id),
    )


async def touch_thread(thread_id: int, staff_id: int | None = None) -> None:
    await execute(
        "UPDATE message_threads SET updated_ts = ?, last_staff_id = COALESCE(?, last_staff_id) WHERE id = ?",
        (now_iso(), staff_id, thread_id),
    )


async def add_thread_message(
    thread_id: int,
    sender_role: str,
    text: str,
    staff_id: int | None = None,
    tg_message_id: int | None = None,
) -> None:
    await execute(
        """
        INSERT INTO thread_messages (thread_id, ts, sender_role, staff_id, text, tg_message_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (thread_id, now_iso(), sender_role, staff_id, text[:4000], tg_message_id),
    )
    await touch_thread(thread_id, staff_id)


async def get_open_threads(limit: int = 20) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT t.*, u.username, u.phone, u.name
        FROM message_threads t
        LEFT JOIN users u ON u.user_id = t.user_id
        WHERE t.status = 'open'
        ORDER BY t.updated_ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]


async def get_thread_messages(thread_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetchall(
        """
        SELECT *
        FROM thread_messages
        WHERE thread_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (thread_id, limit),
    )
    result = [dict(row) for row in rows]
    result.reverse()
    return result


async def save_broadcast_log(
    *,
    staff_id: int,
    text: str,
    recipients_total: int,
    delivered: int,
    failed: int,
    segment: str = "all",
    payload_type: str = "text",
    blocked: int = 0,
) -> None:
    await execute(
        """
        INSERT INTO broadcast_logs (ts, staff_id, segment, payload_type, text, recipients_total, delivered, failed, blocked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (now_iso(), staff_id, segment, payload_type, text[:4000], recipients_total, delivered, failed, blocked),
    )
    await log_bot_event(
        level="INFO",
        source="staff_broadcast",
        message="Рассылка завершена",
        details={
            "staff_id": staff_id,
            "recipients_total": recipients_total,
            "delivered": delivered,
            "failed": failed,
            "blocked": blocked,
            "segment": segment,
            "payload_type": payload_type,
        },
    )


async def log_direct_message(staff_id: int, user_id: int, thread_id: int, text: str) -> None:
    await log_bot_event(
        level="INFO",
        source="staff_direct_message",
        message="Персональное сообщение отправлено",
        details={"staff_id": staff_id, "user_id": user_id, "thread_id": thread_id, "text": text[:400]},
    )


async def log_user_thread_reply(
    *,
    user_id: int,
    username: str | None,
    phone: str | None,
    thread_id: int,
    text: str,
) -> None:
    await log_user_event(
        user_id=user_id,
        username=username,
        phone=phone,
        event_type="message",
        event_name="Ответ в диалоге",
        screen="staff_messages",
        payload={"thread_id": thread_id, "text": text[:400]},
    )
