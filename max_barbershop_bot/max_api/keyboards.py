"""MAX inline keyboard builders.

Формат основан на официальной документации MAX API:
`attachments[].type = "inline_keyboard"` и `attachments[].payload.buttons` как массив рядов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ButtonType = Literal["callback", "link", "message", "request_contact"]


@dataclass(frozen=True)
class MaxButton:
    """One MAX inline keyboard button."""

    text: str
    type: ButtonType
    payload: str | None = None
    url: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Convert the button to MAX API button payload."""

        data: dict[str, Any] = {"type": self.type, "text": self.text}
        if self.payload is not None:
            data["payload"] = self.payload
        if self.url is not None:
            data["url"] = self.url
        return data


@dataclass(frozen=True)
class MaxInlineKeyboard:
    """MAX inline keyboard attachment grouped by rows."""

    rows: tuple[tuple[MaxButton, ...], ...]

    @classmethod
    def from_rows(cls, rows: list[list[MaxButton]] | tuple[tuple[MaxButton, ...], ...]) -> "MaxInlineKeyboard":
        """Build an immutable keyboard from button rows."""

        return cls(rows=tuple(tuple(row) for row in rows))

    def to_attachment(self) -> dict[str, Any]:
        """Convert the keyboard to MAX API attachment format."""

        return {
            "type": "inline_keyboard",
            "payload": {
                "buttons": [[button.to_payload() for button in row] for row in self.rows],
            },
        }


def callback_button(text: str, payload: str) -> MaxButton:
    """Build a MAX callback button."""

    return MaxButton(text=text, type="callback", payload=payload)


def link_button(text: str, url: str) -> MaxButton:
    """Build a MAX link button."""

    return MaxButton(text=text, type="link", url=url)


def message_button(text: str) -> MaxButton:
    """Build a MAX message button using only fields documented by MAX API."""

    return MaxButton(text=text, type="message")


def request_contact_button(text: str) -> MaxButton:
    """Build a MAX button that asks the user to share their contact."""

    return MaxButton(text=text, type="request_contact")


def inline_keyboard(rows: list[list[MaxButton]] | tuple[tuple[MaxButton, ...], ...]) -> MaxInlineKeyboard:
    """Build a MAX inline keyboard from rows of buttons."""

    return MaxInlineKeyboard.from_rows(rows)
