"""Lightweight transport models for MAX API payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ButtonType = Literal[
    "callback",
    "link",
    "request_contact",
    "request_geo_location",
    "open_app",
    "message",
    "clipboard",
]


@dataclass(frozen=True)
class MaxButton:
    """Inline keyboard button for MAX message attachments."""

    text: str
    type: ButtonType = "callback"
    payload: str | None = None
    url: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Convert the button into MAX API inline keyboard format."""

        data: dict[str, Any] = {"type": self.type, "text": self.text}
        if self.payload is not None:
            data["payload"] = self.payload
        if self.url is not None:
            data["url"] = self.url
        return data


@dataclass(frozen=True)
class MaxInlineKeyboard:
    """Inline keyboard attachment payload grouped by rows."""

    rows: tuple[tuple[MaxButton, ...], ...]

    @classmethod
    def from_rows(cls, rows: list[list[MaxButton]] | tuple[tuple[MaxButton, ...], ...]) -> "MaxInlineKeyboard":
        """Build an immutable keyboard from button rows."""

        return cls(rows=tuple(tuple(row) for row in rows))

    def to_attachment(self) -> dict[str, Any]:
        """Convert keyboard into MAX API attachment format."""

        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [[button.to_payload() for button in row] for row in self.rows],
            },
        }


@dataclass(frozen=True)
class MaxMessage:
    """Transport subset of a MAX message."""

    message_id: str | None
    chat_id: int | None
    user_id: int | None
    text: str | None
    timestamp: int | None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "MaxMessage | None":
        """Parse only transport-level fields from a MAX Message object."""

        if not isinstance(payload, dict):
            return None

        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        recipient = payload.get("recipient") if isinstance(payload.get("recipient"), dict) else {}
        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}

        message_id = body.get("mid") or payload.get("message_id") or payload.get("id")
        text = body.get("text") if isinstance(body, dict) else None
        chat_id = recipient.get("chat_id") or payload.get("chat_id")
        user_id = sender.get("user_id") or payload.get("user_id")

        return cls(
            message_id=str(message_id) if message_id is not None else None,
            chat_id=int(chat_id) if isinstance(chat_id, int) else None,
            user_id=int(user_id) if isinstance(user_id, int) else None,
            text=text if isinstance(text, str) else None,
            timestamp=payload.get("timestamp") if isinstance(payload.get("timestamp"), int) else None,
            _raw=payload,
        )


@dataclass(frozen=True)
class MaxCallback:
    """Transport subset of a MAX button callback update."""

    callback_id: str
    payload: str | None
    user_id: int | None
    message: MaxMessage | None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "MaxCallback | None":
        """Parse callback data from a MAX Update.callback object."""

        if not isinstance(payload, dict):
            return None

        callback_id = payload.get("callback_id")
        if not isinstance(callback_id, str) or not callback_id:
            return None

        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = user.get("user_id") or payload.get("user_id")
        payload_value = payload.get("payload")

        return cls(
            callback_id=callback_id,
            payload=payload_value if isinstance(payload_value, str) else None,
            user_id=int(user_id) if isinstance(user_id, int) else None,
            message=MaxMessage.from_payload(payload.get("message")),
            _raw=payload,
        )


@dataclass(frozen=True)
class MaxUpdate:
    """Transport subset of a MAX update."""

    update_type: str
    timestamp: int | None
    chat_id: int | None
    message: MaxMessage | None
    callback: MaxCallback | None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MaxUpdate":
        """Parse known transport fields from a MAX Update object."""

        message_payload = payload.get("message")
        callback_payload = payload.get("callback")
        chat_id = payload.get("chat_id")

        return cls(
            update_type=str(payload.get("update_type", "")),
            timestamp=payload.get("timestamp") if isinstance(payload.get("timestamp"), int) else None,
            chat_id=int(chat_id) if isinstance(chat_id, int) else None,
            message=MaxMessage.from_payload(message_payload),
            callback=MaxCallback.from_payload(callback_payload),
            _raw=payload,
        )
