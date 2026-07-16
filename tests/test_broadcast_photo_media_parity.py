from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import broadcasts
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.services.broadcasts import BroadcastAudience, BroadcastRecipient, BroadcastSendReport
from max_barbershop_bot.ui.buttons import (
    BROADCAST_BACK_PAYLOAD,
    BROADCAST_CONFIRM_SEND_PAYLOAD,
    BROADCAST_HOME_PAYLOAD,
    BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD,
    BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD,
)


ACTOR = "510"
CHAT = "9510"
PHOTO = {"type": "image", "payload": {"token": "photo-token"}}


@dataclass
class Sender:
    messages: list[dict[str, object]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append({"text": text, "keyboard": keyboard, "attachments": attachments})
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        return await self.send_to_chat(user_id, text, keyboard=keyboard, attachments=attachments)

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


@pytest.fixture()
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "broadcast-media.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    UsersRepository(str(path)).create(
        UserCreate(
            platform=PLATFORM_MAX,
            platform_user_id=ACTOR,
            max_user_id=ACTOR,
            chat_id=CHAT,
            first_name="Администратор",
            phone="+79990000510",
            birthdate="1990-01-02",
        )
    )
    monkeypatch.setattr(broadcasts, "_can_open_broadcasts", lambda _context: True)
    broadcasts.release_action_lock(broadcasts._BROADCAST_SEND_LOCK_KEY)
    state._user_states.clear()
    return path


def _context(*, payload: str | None = None, text: str | None = None, attachments=None, sender=None):
    actual_sender = sender or Sender()
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback" if payload else "message_created",
            platform_user_id=ACTOR,
            max_user_id=ACTOR,
            chat_id=CHAT,
            text=text,
            callback_payload=payload,
            callback_id="cb" if payload else None,
            attachments=attachments or [],
        ),
        sender=actual_sender,
    ), actual_sender


def _seed_preview(*, attachment: dict[str, object] | None = None) -> None:
    state.set_current_screen(ACTOR, CHAT, state.BROADCAST_ONE_TIME_PREVIEW_SCREEN)
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_TEXT_KEY, "Текст рассылки")
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_AUDIENCE_KEY, broadcasts.SELF_AUDIENCE.key)
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_AUDIENCE_LABEL_KEY, broadcasts.SELF_AUDIENCE.label)
    state.set_state_data_value(
        ACTOR,
        CHAT,
        broadcasts._BROADCAST_RECIPIENTS_KEY,
        [BroadcastRecipient(platform_user_id=ACTOR, max_user_id=ACTOR)],
    )
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_PREVIEW_TOKEN_KEY, "preview-token")
    if attachment:
        state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_ATTACHMENT_TYPE_KEY, "photo")
        state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_ATTACHMENT_KEY, attachment)


def test_broadcast_photo_add_upload_real_handlers_show_photo_only_preview(configured: Path) -> None:
    _seed_preview()
    edit, edit_sender = _context(payload=BROADCAST_PREVIEW_EDIT_ATTACHMENT_PAYLOAD)
    asyncio.run(broadcasts.handle_preview_edit_attachment(edit))
    assert edit_sender.callbacks == ["cb"]
    assert edit_sender.messages[-1]["text"] == "Отправьте фото для рассылки. Можно добавить подпись текстом 👇"
    assert "GIF" not in str(edit_sender.messages[-1]["text"]) and "видео" not in str(edit_sender.messages[-1]["text"])

    upload, upload_sender = _context(text="Новая подпись", attachments=[PHOTO])
    asyncio.run(broadcasts.handle_text_input(upload))
    preview = upload_sender.messages[-1]
    assert state.get_current_screen(ACTOR, CHAT) == state.BROADCAST_ONE_TIME_PREVIEW_SCREEN
    assert preview["attachments"] == [PHOTO]
    assert [button.text for row in preview["keyboard"].rows for button in row][:4] == [
        "✅ Отправить",
        "✏️ Изменить текст",
        "📷 Изменить фото",
        "🗑 Убрать фото",
    ]


def test_broadcast_photo_skip_real_send_handler_sends_without_attachment_once(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_preview()
    calls: list[object] = []

    async def send_once(**kwargs):
        calls.append(kwargs.get("attachment"))
        return BroadcastSendReport(total=1, sent=1, failed=0, blocked=0, broadcast_id="b-photo-skip")

    monkeypatch.setattr(broadcasts, "send_one_time_broadcast", send_once)
    context, sender = _context(payload=BROADCAST_CONFIRM_SEND_PAYLOAD)
    asyncio.run(broadcasts.handle_confirm_send(context))
    asyncio.run(broadcasts.handle_confirm_send(context))
    assert calls == [None]
    assert state.get_current_screen(ACTOR, CHAT) in {
        state.BROADCAST_ONE_TIME_REPORT_SCREEN,
        state.BROADCAST_ONE_TIME_TEXT_SCREEN,
    }
    assert not any("photo-token" in str(message) for message in sender.messages)


def test_broadcast_photo_send_real_handler_preserves_exact_image_attachment(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_preview(attachment=PHOTO)
    calls: list[object] = []

    async def send_once(**kwargs):
        calls.append(kwargs.get("attachment"))
        return BroadcastSendReport(total=1, sent=1, failed=0, blocked=0, broadcast_id="b-photo")

    monkeypatch.setattr(broadcasts, "send_one_time_broadcast", send_once)
    context, _ = _context(payload=BROADCAST_CONFIRM_SEND_PAYLOAD)
    asyncio.run(broadcasts.handle_confirm_send(context))
    assert calls == [PHOTO]


@pytest.mark.parametrize(
    "unsupported",
    [
        {"type": "animation", "payload": {"token": "gif-token"}},
        {"type": "video", "payload": {"token": "video-token"}},
    ],
)
def test_broadcast_gif_video_real_input_handler_keeps_media_disabled(
    configured: Path,
    unsupported: dict[str, object],
) -> None:
    _seed_preview()
    state.set_current_screen(ACTOR, CHAT, state.BROADCAST_ONE_TIME_TEXT_SCREEN)
    context, sender = _context(text="Подпись", attachments=[unsupported])
    asyncio.run(broadcasts.handle_text_input(context))
    assert sender.messages[-1]["text"] == (
        "Этот тип вложения пока не поддерживается в MAX 🙏 "
        "Для рассылки можно добавить только фото. Отправьте фото или продолжите без него."
    )
    assert broadcasts._broadcast_attachment(context) is None
    assert state.get_current_screen(ACTOR, CHAT) == state.BROADCAST_ONE_TIME_TEXT_SCREEN


def test_broadcast_photo_remove_back_home_real_handlers_clear_owned_media(configured: Path) -> None:
    _seed_preview(attachment=PHOTO)
    remove, remove_sender = _context(payload=BROADCAST_PREVIEW_REMOVE_ATTACHMENT_PAYLOAD)
    asyncio.run(broadcasts.handle_preview_remove_attachment(remove))
    assert remove_sender.callbacks == ["cb"]
    assert remove_sender.messages[-1]["attachments"] is None
    assert broadcasts._broadcast_attachment(remove) is None

    back, back_sender = _context(payload=BROADCAST_BACK_PAYLOAD)
    asyncio.run(broadcasts.handle_broadcast_back(back))
    assert back_sender.callbacks == ["cb"]
    assert state.get_current_screen(ACTOR, CHAT) == state.BROADCAST_ONE_TIME_TEXT_SCREEN

    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_ATTACHMENT_TYPE_KEY, "photo")
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_ATTACHMENT_KEY, PHOTO)
    home, home_sender = _context(payload=BROADCAST_HOME_PAYLOAD)
    asyncio.run(broadcasts.handle_broadcast_home(home))
    assert home_sender.callbacks == ["cb"]
    assert state.get_current_screen(ACTOR, CHAT) == state.MAIN_MENU_SCREEN
    assert broadcasts._broadcast_attachment(home) is None


def test_broadcast_photo_stale_and_send_error_real_handlers_are_safe(
    configured: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_preview(attachment=PHOTO)
    state.set_state_data_value(ACTOR, CHAT, broadcasts._BROADCAST_PREVIEW_TOKEN_KEY, None)
    stale, stale_sender = _context(payload=BROADCAST_CONFIRM_SEND_PAYLOAD)
    asyncio.run(broadcasts.handle_confirm_send(stale))
    assert stale_sender.messages[-1]["text"] == "⚠️ Эта рассылка уже отправляется или была отправлена."
    assert state.get_current_screen(ACTOR, CHAT) == state.BROADCAST_MENU_SCREEN

    _seed_preview(attachment=PHOTO)

    async def fail_send(**_kwargs):
        raise RuntimeError("Authorization: Bearer media-secret internal-id-42")

    monkeypatch.setattr(broadcasts, "send_one_time_broadcast", fail_send)
    failed, failed_sender = _context(payload=BROADCAST_CONFIRM_SEND_PAYLOAD)
    asyncio.run(broadcasts.handle_confirm_send(failed))
    assert failed_sender.messages[-1]["text"] == "⚠️ Не удалось завершить рассылку. Попробуйте позже."
    assert failed_sender.messages[-1]["attachments"] == [PHOTO]
    assert state.get_current_screen(ACTOR, CHAT) == state.BROADCAST_ONE_TIME_PREVIEW_SCREEN
    assert "media-secret" not in str(failed_sender.messages)
    assert broadcasts._broadcast_attachment(failed) == PHOTO
