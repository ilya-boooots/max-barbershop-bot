"""Generic MAX message sender helpers."""

from __future__ import annotations

from max_barbershop_bot.max_api.client import MaxApiClient
from max_barbershop_bot.max_api.models import MaxInlineKeyboard, MaxMessage


class MaxMessageSender:
    """Small wrapper around MaxApiClient for generic message sending."""

    def __init__(self, client: MaxApiClient) -> None:
        self._client = client

    async def send_to_user(
        self,
        user_id: int,
        text: str,
        *,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> MaxMessage | None:
        """Send a generic text message to a MAX user."""

        return await self._client.send_message(
            user_id=user_id,
            text=text,
            keyboard=keyboard,
        )

    async def send_to_chat(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> MaxMessage | None:
        """Send a generic text message to a MAX chat."""

        return await self._client.send_message(
            chat_id=chat_id,
            text=text,
            keyboard=keyboard,
        )
