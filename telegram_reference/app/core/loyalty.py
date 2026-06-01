from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

AUTO_APPROVE_BONUS_THRESHOLD = 600
CANCELLATION_WINDOW_MINUTES = 5


def minutes_until_cancel_allowed(
    created_at: datetime,
    window: int = CANCELLATION_WINDOW_MINUTES,
) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed_minutes = (now - created_at).total_seconds() / 60
    remaining = window - elapsed_minutes
    return max(0, ceil(remaining))
