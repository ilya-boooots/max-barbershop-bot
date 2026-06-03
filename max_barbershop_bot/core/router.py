"""Small custom dispatcher for normalized MAX events."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.max_api.models import MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.services.registration import extract_contact_phone, mask_phone

logger = logging.getLogger(__name__)

HandlerResult = Awaitable[None] | None
EventHandler = Callable[["RouterContext"], HandlerResult]


@dataclass(frozen=True)
class RouterContext:
    """Data and helpers available to flow handlers."""

    event: NormalizedEvent
    sender: MaxMessageSender

    async def send_text(
        self,
        text: str,
        *,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> None:
        """Send a text reply to the event chat or user when possible."""

        chat_id = _int_from_string(self.event.chat_id)
        if chat_id is not None:
            await self.sender.send_to_chat(chat_id, text, keyboard=keyboard)
            return

        user_id = _int_from_string(self.event.max_user_id or self.event.platform_user_id)
        if user_id is not None:
            await self.sender.send_to_user(user_id, text, keyboard=keyboard)
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
        self._screen_text_handlers: dict[str, EventHandler] = {}
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

    def on_screen_text(self, screen_id: str, handler: EventHandler) -> None:
        """Register a text handler for the current in-memory screen."""

        self._screen_text_handlers[screen_id] = handler

    def on_unknown_text(self, handler: EventHandler) -> None:
        """Register the fallback handler for unknown text messages."""

        self._unknown_text_handler = handler

    def on_unknown_callback(self, handler: EventHandler) -> None:
        """Register the fallback handler for unknown callbacks."""

        self._unknown_callback_handler = handler

    async def dispatch(self, event: NormalizedEvent, sender: MaxMessageSender) -> None:
        """Route one normalized event and keep runtime safe on handler errors."""

        self._log_contact_diagnostic(event)
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
            return self._resolve_text_handler(event)
        if event.update_type == "message_callback":
            return self._resolve_callback_handler(event.callback_payload)
        return self._update_handlers.get(event.update_type)

    def _resolve_text_handler(self, event: NormalizedEvent) -> EventHandler | None:
        current_screen = state.get_current_screen(event.platform_user_id, event.chat_id)
        raw_attachment_locations = _attachment_locations(event.raw_update)
        if event.text is None and not event.attachments and not raw_attachment_locations:
            return None
        if event.text is not None and event.text in self._text_handlers:
            return self._text_handlers[event.text]

        if current_screen == state.REGISTRATION_PHONE_SCREEN:
            return self._screen_text_handlers.get(current_screen)
        if event.text is None and not event.attachments:
            return None
        return self._screen_text_handlers.get(current_screen) or self._unknown_text_handler

    def _log_contact_diagnostic(self, event: NormalizedEvent) -> None:
        if event.update_type != "message_created":
            return

        diagnostic_attachments = event.attachments or _raw_attachments(event.raw_update)
        if not diagnostic_attachments and not _looks_like_contact_update(event.raw_update):
            return

        current_screen = state.get_current_screen(event.platform_user_id, event.chat_id)
        attachment_types = _attachment_types(diagnostic_attachments)
        payload_keys = _payload_keys(diagnostic_attachments)
        vcf_info_exists = _vcf_info_exists(diagnostic_attachments)
        vcf_has_tel = _vcf_info_has_tel(diagnostic_attachments)
        extracted_phone = extract_contact_phone(diagnostic_attachments)
        logger.info(
            "MAX contact diagnostic: update_type=%s screen_id=%s platform_user_id=%s "
            "chat_id=%s text_exists=%s attachment_locations=%s attachment_count=%s "
            "attachment_types=%s payload_keys=%s vcf_info_exists=%s vcf_info_has_tel=%s "
            "phone_extraction_succeeded=%s masked_phone=%s",
            event.update_type,
            current_screen,
            event.platform_user_id,
            event.chat_id,
            event.text is not None,
            _attachment_locations(event.raw_update),
            len(diagnostic_attachments),
            attachment_types,
            payload_keys,
            vcf_info_exists,
            vcf_has_tel,
            extracted_phone is not None,
            mask_phone(extracted_phone) if extracted_phone is not None else None,
        )

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


def _attachment_locations(raw_update: dict[str, object]) -> list[str]:
    return [path for path, _attachments in _attachment_lists(raw_update)]


def _raw_attachments(raw_update: dict[str, object]) -> list[object]:
    for _path, attachments in _attachment_lists(raw_update):
        if attachments:
            return list(attachments)
    return []


def _attachment_lists(raw_update: dict[str, object]) -> list[tuple[str, list[object]]]:
    locations: list[tuple[str, list[object]]] = []
    _collect_attachment_lists(raw_update, [], locations)
    return locations


def _collect_attachment_lists(
    value: object,
    path: list[str],
    locations: list[tuple[str, list[object]]],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = [*path, str(key)]
            if key == "attachments" and isinstance(child, list):
                locations.append((".".join(child_path), list(child)))
                continue
            if isinstance(child, dict):
                _collect_attachment_lists(child, child_path, locations)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, dict):
                _collect_attachment_lists(child, [*path, str(index)], locations)


def _looks_like_contact_update(raw_update: dict[str, object]) -> bool:
    return any(
        attachment_type == "contact" or "vcf_info" in payload_keys
        for attachment_type, payload_keys in _attachment_type_and_payload_keys(_raw_attachments(raw_update))
    )


def _attachment_types(attachments: list[object]) -> list[str]:
    types: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            types.append(type(attachment).__name__)
            continue
        attachment_type = attachment.get("type")
        types.append(str(attachment_type) if attachment_type is not None else "<missing>")
    return types


def _payload_keys(attachments: list[object]) -> list[list[str]]:
    return [payload_keys for _, payload_keys in _attachment_type_and_payload_keys(attachments)]


def _attachment_type_and_payload_keys(attachments: list[object]) -> list[tuple[str, list[str]]]:
    values: list[tuple[str, list[str]]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            values.append((type(attachment).__name__, []))
            continue
        attachment_type = attachment.get("type")
        payload = attachment.get("payload") if isinstance(attachment.get("payload"), dict) else attachment
        values.append(
            (
                str(attachment_type) if attachment_type is not None else "<missing>",
                sorted(str(key) for key in payload),
            )
        )
    return values


def _vcf_info_exists(attachments: list[object]) -> bool:
    return any(isinstance(_attachment_payload(attachment).get("vcf_info"), str) for attachment in attachments)


def _vcf_info_has_tel(attachments: list[object]) -> bool:
    return any(_payload_has_vcf_tel(_attachment_payload(attachment)) for attachment in attachments)


def _attachment_payload(attachment: object) -> dict[str, object]:
    if not isinstance(attachment, dict):
        return {}
    payload = attachment.get("payload")
    return payload if isinstance(payload, dict) else attachment


def _payload_has_vcf_tel(payload: dict[str, object]) -> bool:
    vcf_info = payload.get("vcf_info")
    if not isinstance(vcf_info, str):
        return False
    normalized_vcf = vcf_info.replace("\\r\\n", "\n").replace("\\n", "\n")
    return any(line.upper().startswith("TEL") for line in normalized_vcf.splitlines())
