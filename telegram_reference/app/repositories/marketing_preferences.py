from __future__ import annotations

from datetime import datetime, timezone

from app.db.sqlite import execute, fetchone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def is_unsubscribed(client_tg_id: int) -> bool:
    row = await fetchone("SELECT marketing_unsubscribed FROM client_marketing_preferences WHERE client_tg_id=?", (client_tg_id,))
    return bool(row and int(row[0] or 0) == 1)


async def set_unsubscribed(client_tg_id: int, *, yclients_client_id: str | None = None, source: str = "telegram_button") -> None:
    now = _now()
    await execute(
        """
        INSERT INTO client_marketing_preferences (client_tg_id, yclients_client_id, marketing_unsubscribed, unsubscribed_at_utc, created_at_utc, updated_at_utc, unsubscribe_source)
        VALUES (?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(client_tg_id) DO UPDATE SET
            marketing_unsubscribed=1,
            yclients_client_id=COALESCE(excluded.yclients_client_id, client_marketing_preferences.yclients_client_id),
            unsubscribed_at_utc=excluded.unsubscribed_at_utc,
            updated_at_utc=excluded.updated_at_utc,
            unsubscribe_source=excluded.unsubscribe_source
        """,
        (client_tg_id, yclients_client_id, now, now, now, source),
    )


async def set_subscribed(client_tg_id: int) -> None:
    now = _now()
    await execute(
        """
        INSERT INTO client_marketing_preferences (client_tg_id, marketing_unsubscribed, resubscribed_at_utc, created_at_utc, updated_at_utc)
        VALUES (?, 0, ?, ?, ?)
        ON CONFLICT(client_tg_id) DO UPDATE SET
            marketing_unsubscribed=0,
            resubscribed_at_utc=excluded.resubscribed_at_utc,
            updated_at_utc=excluded.updated_at_utc
        """,
        (client_tg_id, now, now, now),
    )
