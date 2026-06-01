from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class FakeUser:
    id: int
    username: str | None = "tester"
    full_name: str = "Test User"


class FakeBot:
    def __init__(self, username: str = "test_bot") -> None:
        self.username = username
        self.sent_messages: list[dict[str, Any]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> SimpleNamespace:
        payload = {"chat_id": chat_id, "text": text, "kwargs": kwargs}
        self.sent_messages.append(payload)
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=len(self.sent_messages))

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        return None


class FakeMessage:
    def __init__(self, user_id: int = 1001, text: str = "", bot: FakeBot | None = None) -> None:
        self.from_user = FakeUser(id=user_id)
        self.text = text
        self.contact = None
        self.bot = bot or FakeBot()
        self.answers: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(id=user_id)
        self.message_id = 1

    async def answer(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.answers.append({"text": text, "kwargs": kwargs})
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.edits.append({"text": text, "kwargs": kwargs})
        return self


class FakeCallback:
    def __init__(self, user_id: int = 1001, data: str = "", message: FakeMessage | None = None, bot: FakeBot | None = None) -> None:
        self.from_user = FakeUser(id=user_id)
        self.data = data
        self.bot = bot or FakeBot()
        self.message = message or FakeMessage(user_id=user_id, bot=self.bot)
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append({"text": text, "kwargs": kwargs})
