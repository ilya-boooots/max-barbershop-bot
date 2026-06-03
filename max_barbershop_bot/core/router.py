"""Small custom dispatcher for normalized MAX events."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.max_api.sender import MaxMessageSender

logger = logging.getLogger(__name__)

HandlerResult = Awaitable[None] | None
EventHandler = Callable[["RouterContext"], HandlerResult]


@dataclass(frozen=True)
class RouterContext:
    """Data and helpers available to flow handlers."""

    event: NormalizedEvent
    sender: MaxMessageSender

    async def send_text(self, text: str) -> None:
        """Send a text reply to the event chat or user when possible."""

        chat_id = _int_from_string(self.event.chat_id)
        if chat_id is not None:
            await self.sender.send_to_chat(chat_id, text)
            return

        user_id = _int_from_string(self.event.max_user_id or self.event.platform_user_id)
        if user_id is not None:
            await self.sender.send_to_user(user_id, text)
            return

        logger.warning(
            "Cannot send MAX text reply: update_type=%s has no chat_id/user_id",
            self.event.update_type,
        )

    async def answer_callback(self, notification: str) -> None:
        """Answer a callback event when MAX callback_id is available."""

        if not self.event.callback_id:
            logger.warning("Cannot answer MAX callback: callback_id is missing")
            return
        await self.sender.answer_callback(self.event.callback_id, notification=notification)


class Router:
    """Beginner-friendly router for normalized MAX updates."""

    def __init__(self) -> None:
        self._update_handlers: dict[str, EventHandler] = {}
        self._text_handlers: dict[str, EventHandler] = {}
        self._callback_handlers: dict[str, EventHandler] = {}
        self._unknown_text_handler: EventHandler | None = None
        self._unknown_callback_handler: EventHandler | None = None

    def on_update(self, update_type: str, handler: EventHandler) -> None:
        """Register a handler for an update type, for example bot_started."""

        self._update_handlers[update_type] = handler

    def on_text(self, text: str, handler: EventHandler) -> None:
        """Register a handler for an exact message text."""

        self._text_handlers[text] = handler

    def on_callback(self, payload: str, handler: EventHandler) -> None:
        """Register a handler for an exact callback payload."""

        self._callback_handlers[payload] = handler

    def on_unknown_text(self, handler: EventHandler) -> None:
        """Register the fallback handler for unknown text messages."""

        self._unknown_text_handler = handler

    def on_unknown_callback(self, handler: EventHandler) -> None:
        """Register the fallback handler for unknown callbacks."""

        self._unknown_callback_handler = handler

    async def dispatch(self, event: NormalizedEvent, sender: MaxMessageSender) -> None:
        """Route one normalized event and keep runtime safe on handler errors."""

        handler = self._resolve_handler(event)
        if handler is None:
            logger.debug("No MAX route for update_type=%s", event.update_type)
            return

        try:
            result = handler(RouterContext(event=event, sender=sender))
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "MAX handler failed safely: update_type=%s text=%r callback_payload=%r",
                event.update_type,
                event.text,
                event.callback_payload,
            )

    def _resolve_handler(self, event: NormalizedEvent) -> EventHandler | None:
        if event.update_type == "message_created":
            return self._resolve_text_handler(event.text)
        if event.update_type == "message_callback":
            return self._resolve_callback_handler(event.callback_payload)
        return self._update_handlers.get(event.update_type)

    def _resolve_text_handler(self, text: str | None) -> EventHandler | None:
        if text is None:
            return None
        return self._text_handlers.get(text) or self._unknown_text_handler

    def _resolve_callback_handler(self, payload: str | None) -> EventHandler | None:
        if payload is None:
            return self._unknown_callback_handler
        return self._callback_handlers.get(payload) or self._unknown_callback_handler


def _int_from_string(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
