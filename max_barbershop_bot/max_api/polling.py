"""Development/test Long Polling helper for MAX updates."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from max_barbershop_bot.max_api.client import MaxApiClient
from max_barbershop_bot.max_api.models import MaxUpdate


async def iter_dev_updates(
    client: MaxApiClient,
    *,
    limit: int = 100,
    timeout: int = 30,
    marker: int | None = None,
    types: Sequence[str] | None = None,
    idle_sleep: float = 0.1,
) -> AsyncIterator[MaxUpdate]:
    """Iterate MAX updates via Long Polling for development and tests only."""

    next_marker = marker
    while True:
        updates, next_marker = await client.get_updates(
            limit=limit,
            timeout=timeout,
            marker=next_marker,
            types=types,
        )
        for update in updates:
            yield update
        if not updates:
            await asyncio.sleep(idle_sleep)
