from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_test_event(*, event_type: str, target_tg_id: int, payload: dict[str, Any] | None = None, status: str = "created") -> int:
    created_at = now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    await execute(
        """
        INSERT INTO notification_test_events (event_type, target_tg_id, source, is_test, payload_json, status, created_at_utc)
        VALUES (?, ?, 'dev_test', 1, ?, ?, ?)
        """,
        (event_type, target_tg_id, payload_json, status, created_at),
    )
    row = await fetchone(
        """
        SELECT id FROM notification_test_events
        WHERE event_type=? AND target_tg_id=? AND source='dev_test' AND is_test=1 AND payload_json=? AND status=? AND created_at_utc=?
        ORDER BY id DESC LIMIT 1
        """,
        (event_type, target_tg_id, payload_json, status, created_at),
    )
    return int(row["id"]) if row else 0


async def mark_sent(event_id: int) -> None:
    await execute("UPDATE notification_test_events SET status='sent', sent_at_utc=? WHERE id=?", (now_iso(), event_id))


async def mark_failed(event_id: int, error_summary: str) -> None:
    await execute(
        "UPDATE notification_test_events SET status='failed', error_summary=? WHERE id=?",
        (error_summary[:200], event_id),
    )


async def cleanup_test_events() -> None:
    await execute("DELETE FROM notification_test_events WHERE is_test=1 OR source='dev_test'")
