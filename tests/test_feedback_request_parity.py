from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import feedback as feedback_flow
from max_barbershop_bot.flows.feedback import FEEDBACK_COMMENT_SCREEN
from max_barbershop_bot.repositories.feedback import FeedbackRepository
from max_barbershop_bot.services.feedback import (
    COMMENT_TOO_LONG_TEXT,
    COMMENT_TOO_SHORT_TEXT,
    INVALID_RATING_TEXT,
    NEGATIVE_COMMENT_PROMPT,
    NEGATIVE_THANKS_TEXT,
    POSITIVE_TEXT,
    POST_VISIT_FEEDBACK_REQUEST,
    RATING_MISSING_TEXT,
    REQUEST_TEXT,
    feedback_rating_keyboard,
)


@dataclass
class _Sender:
    messages: list[dict] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None):
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def _context(*, payload: str | None = None, text: str | None = None, user_id: str = "u-feedback") -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback" if payload is not None else "message_created",
            platform_user_id=user_id,
            max_user_id=user_id,
            chat_id="100",
            text=text,
            callback_payload=payload,
            callback_id="cb-feedback" if payload is not None else None,
        ),
        sender=_Sender(),
    )


def _init_request(tmp_path, *, user_id: str = "u-feedback", record_id: str = "record-1") -> str:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))
    feedback_flow.configure_feedback_flow(str(db))
    FeedbackRepository(str(db)).create_request_if_missing(platform_user_id=user_id, yclients_record_id=record_id)
    state.clear_user_state(user_id, "100")
    return str(db)


def _rows(keyboard):
    return [[button for button in row] for row in keyboard.rows]


def test_feedback_request_sends_exact_text_and_stars_keyboard_order() -> None:
    keyboard = feedback_rating_keyboard()
    assert REQUEST_TEXT == "Оцените, пожалуйста, ваш визит ⭐️"
    assert [[button.text for button in row] for row in _rows(keyboard)] == [["⭐⭐⭐⭐⭐"], ["⭐⭐⭐⭐"], ["⭐⭐⭐"], ["⭐⭐"], ["⭐"]]
    assert [[button.payload for button in row] for row in _rows(keyboard)] == [["fb:rate:5"], ["fb:rate:4"], ["fb:rate:3"], ["fb:rate:2"], ["fb:rate:1"]]


def test_invalid_rating_shows_exact_text(tmp_path) -> None:
    _init_request(tmp_path)
    context = _context(payload="fb:rate:x")
    asyncio.run(feedback_flow._handle_rating(context))
    assert context.sender.messages[-1]["text"] == INVALID_RATING_TEXT


def test_rating_5_creates_feedback_row_shows_public_links_and_clears_state(tmp_path) -> None:
    db = _init_request(tmp_path)
    context = _context(payload="fb:rate:5")
    asyncio.run(feedback_flow._handle_rating(context))
    assert context.sender.messages[-1]["text"] == POSITIVE_TEXT
    assert [row[0].text for row in context.sender.messages[-1]["keyboard"].rows] == ["Яндекс Карты", "2GIS"]
    response = FeedbackRepository(db).get_response(platform_user_id="u-feedback", yclients_record_id="record-1")
    assert response is not None and response.rating == 5 and response.comment is None
    assert state.get_current_screen("u-feedback", "100") == state.MAIN_MENU_SCREEN


def test_rating_4_creates_feedback_row_and_shows_public_links(tmp_path) -> None:
    db = _init_request(tmp_path)
    context = _context(payload="fb:rate:4")
    asyncio.run(feedback_flow._handle_rating(context))
    assert context.sender.messages[-1]["text"] == POSITIVE_TEXT
    assert [row[0].text for row in context.sender.messages[-1]["keyboard"].rows] == ["Яндекс Карты", "2GIS"]
    response = FeedbackRepository(db).get_response(platform_user_id="u-feedback", yclients_record_id="record-1")
    assert response is not None and response.rating == 4


def test_low_ratings_ask_for_comment_and_set_waiting_status(tmp_path) -> None:
    for rating in (3, 2, 1):
        user_id = f"u-feedback-{rating}"
        db = _init_request(tmp_path, user_id=user_id, record_id=f"record-{rating}")
        context = _context(payload=f"fb:rate:{rating}", user_id=user_id)
        asyncio.run(feedback_flow._handle_rating(context))
        assert context.sender.messages[-1]["text"] == NEGATIVE_COMMENT_PROMPT
        assert state.get_current_screen(user_id, "100") == FEEDBACK_COMMENT_SCREEN
        response = FeedbackRepository(db).get_response(platform_user_id=user_id, yclients_record_id=f"record-{rating}")
        request = FeedbackRepository(db).get_request(platform_user_id=user_id, yclients_record_id=f"record-{rating}")
        assert response is not None and response.rating == rating and response.comment is None
        assert request is not None and request.status == "waiting_negative_comment"


def test_short_and_long_negative_comments_are_rejected(tmp_path) -> None:
    _init_request(tmp_path)
    asyncio.run(feedback_flow._handle_rating(_context(payload="fb:rate:3")))
    short_context = _context(text="1234")
    asyncio.run(feedback_flow._handle_comment(short_context))
    assert short_context.sender.messages[-1]["text"] == COMMENT_TOO_SHORT_TEXT
    long_context = _context(text="x" * 1001)
    asyncio.run(feedback_flow._handle_comment(long_context))
    assert long_context.sender.messages[-1]["text"] == COMMENT_TOO_LONG_TEXT


def test_missing_rating_in_state_shows_exact_recovery_text(tmp_path) -> None:
    _init_request(tmp_path)
    context = _context(text="подробный комментарий")
    asyncio.run(feedback_flow._handle_comment(context))
    assert context.sender.messages[-1]["text"] == RATING_MISSING_TEXT
    assert state.get_current_screen("u-feedback", "100") == state.MAIN_MENU_SCREEN


def test_valid_negative_comment_creates_feedback_text_thanks_clears_and_updates_status(tmp_path, monkeypatch) -> None:
    db = _init_request(tmp_path)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(feedback_flow, "notify_negative_feedback", _noop)
    asyncio.run(feedback_flow._handle_rating(_context(payload="fb:rate:2")))
    context = _context(text="мастер сильно задержался")
    asyncio.run(feedback_flow._handle_comment(context))
    assert context.sender.messages[-1]["text"] == NEGATIVE_THANKS_TEXT
    response = FeedbackRepository(db).get_response(platform_user_id="u-feedback", yclients_record_id="record-1")
    request = FeedbackRepository(db).get_request(platform_user_id="u-feedback", yclients_record_id="record-1")
    assert response is not None and response.rating == 2 and response.comment == "мастер сильно задержался"
    assert request is not None and request.status == "negative_comment_received" and request.completed_at
    assert state.get_current_screen("u-feedback", "100") == state.MAIN_MENU_SCREEN


def test_post_visit_feedback_event_dedup_and_history_row_meaning(tmp_path) -> None:
    db = _init_request(tmp_path)
    repo = FeedbackRepository(db)
    duplicate = repo.create_request_if_missing(platform_user_id="u-feedback", yclients_record_id="record-1")
    assert duplicate is None
    request = repo.get_request(platform_user_id="u-feedback", yclients_record_id="record-1")
    assert request is not None and request.status == "sent" and request.requested_at
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO notification_history (platform, platform_user_id, yclients_record_id, notification_type, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("max", "u-feedback", "record-1", POST_VISIT_FEEDBACK_REQUEST, "sent"),
        )
        count = connection.execute("SELECT COUNT(*) FROM notification_history WHERE notification_type=?", (POST_VISIT_FEEDBACK_REQUEST,)).fetchone()[0]
    assert count == 1


def test_admin_reply_close_behavior_is_not_registered_in_feedback_flow() -> None:
    from max_barbershop_bot.core.router import Router

    router = Router()
    feedback_flow.register_feedback_routes(router)
    assert "fb:reply:1" not in router._callback_handlers
    assert "fb:close:1" not in router._callback_handlers
