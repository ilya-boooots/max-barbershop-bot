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

    async def answer_callback(
        self,
        callback_id: str,
        *,
        notification: str | None = None,
        text: str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> dict[str, object]:
        """Answer a MAX callback event through the existing API client."""

        return await self._client.answer_callback(
            callback_id=callback_id,
            notification=notification,
            text=text,
            keyboard=keyboard,
        )
