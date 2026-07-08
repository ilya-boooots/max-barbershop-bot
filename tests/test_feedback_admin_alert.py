import asyncio
import sqlite3

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import feedback as feedback_flow
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.feedback import FeedbackRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import UserCreate, UsersRepository
from max_barbershop_bot.services.feedback import (
    FEEDBACK_ADMIN_NO_ACCESS_TEXT,
    FEEDBACK_ADMIN_REPLY_CLIENT_TEXT,
    FEEDBACK_ADMIN_REPLY_PROMPT,
    FEEDBACK_ADMIN_REPLY_SUCCESS_TEXT,
    FEEDBACK_ADMIN_STALE_TEXT,
    feedback_admin_keyboard,
    notify_negative_feedback,
    render_post_visit_admin_alert,
)


class FakeSender:
    def __init__(self):
        self.sent = []
        self.answered = []

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(("user", str(user_id), text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id=f"m{len(self.sent)}", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(("chat", str(chat_id), text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id=f"m{len(self.sent)}", recipient_type="chat", recipient_id=str(chat_id))

    async def answer_callback(self, callback_id):
        self.answered.append(callback_id)


@pytest.fixture()
def seeded_db(tmp_path):
    state.clear_user_state("admin", "400")
    state.clear_user_state("guest-admin", "600")
    db = tmp_path / "bot.sqlite3"
    init_database(str(db))
    users = UsersRepository(str(db))
    users.create(UserCreate(platform_user_id="client", max_user_id="100", chat_id="200", display_name="Иван", phone="+7 999 111-22-33"))
    users.create(UserCreate(platform_user_id="admin", max_user_id="300", chat_id="400", display_name="Админ"))
    users.create(UserCreate(platform_user_id="guest-admin", max_user_id="500", chat_id="600", display_name="Не админ"))
    StaffRolesRepository(str(db)).assign_role("admin", "manager", assigned_by_platform_user_id="admin")
    repo = FeedbackRepository(str(db))
    request = repo.create_request_if_missing(platform_user_id="client", yclients_record_id="rec-1", yclients_client_id="yc-1")
    assert request is not None
    response = repo.save_rating_once(platform_user_id="client", yclients_record_id="rec-1", rating=2, is_negative=True)
    assert response is not None
    response = repo.save_comment(platform_user_id="client", yclients_record_id="rec-1", comment="Слишком долго ждал Authorization secret traceback raw_payload")
    assert response is not None
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO notification_history (
                platform, platform_user_id, yclients_record_id, notification_type,
                scheduled_for, status, attempts, metadata_json
            ) VALUES ('max', 'client', 'rec-1', 'post_visit_feedback_request', '2026-07-08T10:00:00+00:00', 'sent', 1, '{}')
            """
        )
        connection.commit()
    return str(db), response.id


def _context(sender, *, platform_user_id="admin", chat_id="400", payload=None, text=None):
    update_type = "message_callback" if payload is not None else "message_created"
    return RouterContext(
        event=NormalizedEvent(
            update_type=update_type,
            platform_user_id=platform_user_id,
            max_user_id=platform_user_id,
            chat_id=chat_id,
            text=text,
            callback_payload=payload,
            callback_id="cb" if payload is not None else None,
        ),
        sender=sender,
    )


def test_negative_feedback_admin_alert_once_and_safe(seeded_db):
    async def scenario():
        db, response_id = seeded_db
        sender = FakeSender()
        repo = FeedbackRepository(db)
        response = repo.get_response_by_id(response_id)
        assert response is not None

        await notify_negative_feedback(sender, database_path=db, response=response)
        await notify_negative_feedback(sender, database_path=db, response=response)

        admin_alerts = [item for item in sender.sent if item[2].startswith("🚨 Низкая оценка после визита")]
        assert len(admin_alerts) == 1
        text = admin_alerts[0][2]
        assert "Оценка: 2/5" in text
        assert "Клиент: Иван" in text
        assert "Телефон: +*******2233" in text
        assert "Услуга: —" in text
        assert "Мастер: —" in text
        assert "Дата визита: 2026-07-08T10:00:00+00:00" in text
        assert "Комментарий клиента:" in text
        assert "Слишком долго ждал" in text
        assert "Authorization:" not in text
        assert "Traceback (most recent call last)" not in text
        keyboard = admin_alerts[0][3]
        assert keyboard is not None
        assert [button.text for row in keyboard.rows for button in row] == ["💬 Ответить клиенту", "⬅️ Назад", "🏠 Главное меню"]
        payloads = [button.payload for row in keyboard.rows for button in row]
        assert f"feedback_admin_reply:{response_id}" in payloads
        assert "broadcast:history:root" in payloads
        assert "nav:home" in payloads
        refreshed = repo.get_response_by_id(response_id)
        assert refreshed is not None
        assert refreshed.admin_notified_at is not None
    asyncio.run(scenario())


def test_reply_callback_role_and_stale_validation(seeded_db):
    async def scenario():
        db, response_id = seeded_db
        feedback_flow.configure_feedback_flow(db)
        sender = FakeSender()

        await feedback_flow._handle_admin_reply_start(_context(sender, platform_user_id="guest-admin", chat_id="600", payload=f"feedback_admin_reply:{response_id}"))
        assert sender.sent[-1][2] == FEEDBACK_ADMIN_NO_ACCESS_TEXT

        await feedback_flow._handle_admin_reply_start(_context(sender, payload="feedback_admin_reply:not-int"))
        assert sender.sent[-1][2] == FEEDBACK_ADMIN_STALE_TEXT

        await feedback_flow._handle_admin_reply_start(_context(sender, payload="feedback_admin_reply:999999"))
        assert sender.sent[-1][2] == FEEDBACK_ADMIN_STALE_TEXT

        await feedback_flow._handle_admin_reply_start(_context(sender, payload=f"feedback_admin_reply:{response_id}"))
        assert sender.sent[-1][2] == FEEDBACK_ADMIN_REPLY_PROMPT
    asyncio.run(scenario())


def test_valid_admin_reply_persists_sends_and_clears_state(seeded_db):
    async def scenario():
        db, response_id = seeded_db
        feedback_flow.configure_feedback_flow(db)
        sender = FakeSender()

        await feedback_flow._handle_admin_reply_start(_context(sender, payload=f"feedback_admin_reply:{response_id}"))
        await feedback_flow._handle_admin_reply_text(_context(sender, text="Исправим ситуацию"))
        assert sender.sent[-1][2] == "Отправить клиенту такой ответ?\n\nИсправим ситуацию"
        assert [button.text for row in sender.sent[-1][3].rows for button in row] == ["✅ Отправить", "✏️ Изменить", "⬅️ Назад", "🏠 Главное меню"]

        await feedback_flow._handle_admin_reply_confirm(_context(sender, payload="feedback_admin_reply_confirm:send"))

        assert ("chat", "200", FEEDBACK_ADMIN_REPLY_CLIENT_TEXT.format(text="Исправим ситуацию"), None) in sender.sent
        assert sender.sent[-1][2] == FEEDBACK_ADMIN_REPLY_SUCCESS_TEXT
        with sqlite3.connect(db) as connection:
            reply = connection.execute("SELECT * FROM feedback_admin_replies WHERE feedback_response_id=?", (response_id,)).fetchone()
            response_status = connection.execute("SELECT status FROM feedback_responses WHERE id=?", (response_id,)).fetchone()[0]
        assert reply is not None
        assert reply[3] == "admin"
        assert reply[4] == "Исправим ситуацию"
        assert response_status == "admin_replied"
        assert state.get_current_screen("admin", "400") == state.MAIN_MENU_SCREEN
    asyncio.run(scenario())


def test_empty_reply_matches_post_visit_stale_behavior(seeded_db):
    async def scenario():
        db, response_id = seeded_db
        feedback_flow.configure_feedback_flow(db)
        sender = FakeSender()

        await feedback_flow._handle_admin_reply_start(_context(sender, payload=f"feedback_admin_reply:{response_id}"))
        await feedback_flow._handle_admin_reply_text(_context(sender, text="   "))

        assert sender.sent[-1][2] == FEEDBACK_ADMIN_STALE_TEXT
        assert state.get_current_screen("admin", "400") == state.MAIN_MENU_SCREEN
    asyncio.run(scenario())


def test_dev_test_keyboard_and_close_persistence(seeded_db):
    db, response_id = seeded_db
    repo = FeedbackRepository(db)
    closed = repo.close_response(response_id=response_id, admin_platform_user_id="admin")
    assert closed is not None
    assert closed.status == "closed"
    assert closed.closed_by_platform_user_id == "admin"
    assert closed.closed_at is not None

    keyboard = feedback_admin_keyboard(response_id, is_test=True)
    assert [button.payload for row in keyboard.rows for button in row][1] == "broadcast:dev_tests:root"

    response = repo.get_response_by_id(response_id)
    assert response is not None
    rendered = render_post_visit_admin_alert(database_path=db, response=response)
    assert "🚨 Низкая оценка после визита" in rendered
