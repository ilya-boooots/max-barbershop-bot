from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass

import pytest

from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows.settings import (
    _DEV_TESTS_HUB_TEXT,
    handle_settings_notifications_cleanup_confirm,
    handle_settings_notifications_run_test,
    handle_settings_notifications_tests,
)
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.notification_test_events import NotificationTestEventsRepository
from max_barbershop_bot.ui.buttons import DEV_TEST_BUTTONS, settings_notification_tests_keyboard


@dataclass
class Sent:
    recipient_type: str
    recipient_id: str
    text: str
    keyboard: object | None
    metadata: object | None


class FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[Sent] = []
        self.fail = fail

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent("user", str(user_id), text, keyboard, metadata))
        return MaxSendResult(ok=not self.fail, status_code=200 if not self.fail else 500, message_id="m1" if not self.fail else None, recipient_type="user", recipient_id=str(user_id), error_message="token=secret raw traceback" if self.fail else None)

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent("chat", str(chat_id), text, keyboard, metadata))
        return MaxSendResult(ok=not self.fail, status_code=200 if not self.fail else 500, message_id="m1" if not self.fail else None, recipient_type="chat", recipient_id=str(chat_id), error_message="failed" if self.fail else None)

    async def answer_callback(self, callback_id):
        return None


def _event(payload: str, user: str = "42") -> NormalizedEvent:
    return NormalizedEvent("message_callback", user, user, "900", None, payload, "cb")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "bot.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DEV_MAX_USER_ID", "42")
    return path


def test_hub_is_protected_and_matches_telegram_text_and_buttons(db):
    denied_sender = FakeSender()
    asyncio.run(handle_settings_notifications_tests(RouterContext(_event("broadcast:dev_tests:root", user="99"), denied_sender)))
    assert denied_sender.sent[-1].text == "⛔ Раздел доступен только разработчику."

    sender = FakeSender()
    asyncio.run(handle_settings_notifications_tests(RouterContext(_event("broadcast:dev_tests:root"), sender)))
    assert sender.sent[-1].text == _DEV_TESTS_HUB_TEXT
    buttons = [row[0] for row in settings_notification_tests_keyboard().rows]
    assert [(button.text, button.payload) for button in buttons[: len(DEV_TEST_BUTTONS)]] == [
        (text, f"broadcast:dev_tests:{key}") for text, key in DEV_TEST_BUTTONS
    ]
    assert all("сразу" not in button.text.lower() for button in buttons)


def test_self_test_creates_sent_row_and_targets_only_current_developer(db):
    sender = FakeSender()
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event("broadcast:dev_tests:self"), sender)))
    assert sender.sent[0].recipient_type == "user"
    assert sender.sent[0].recipient_id == "42"
    assert sender.sent[0].text == "📣 Тестовое уведомление\n\nЭто тестовое сообщение от системы уведомлений FlowBots.\n\nЕсли вы видите это сообщение — базовая отправка работает ✅"
    assert sender.sent[-1].text == "✅ Тестовое уведомление отправлено себе."
    row = sqlite3.connect(db).execute("SELECT event_type, source, is_test, status, sent_at_utc FROM notification_test_events").fetchone()
    assert row == ("self", "dev_test", 1, "sent", row[4])
    assert row[4]


def test_failed_test_row_is_sanitized_and_limited(db):
    sender = FakeSender(fail=True)
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event("broadcast:dev_tests:self"), sender)))
    row = sqlite3.connect(db).execute("SELECT status, error_summary FROM notification_test_events").fetchone()
    assert row[0] == "failed"
    assert len(row[1]) <= 200
    assert "secret" not in row[1].lower()


def test_feedback_test_creates_request_and_rating_buttons(db):
    sender = FakeSender()
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event("broadcast:dev_tests:post_visit_review"), sender)))
    feedback_message = sender.sent[0]
    assert feedback_message.text == "⭐️ Как прошёл ваш визит?\n\nОцените, пожалуйста, от 1 до 5 ⭐️"
    assert [button.text for button in feedback_message.keyboard.rows[0]] == ["⭐️ 1", "⭐️ 2", "⭐️ 3", "⭐️ 4", "⭐️ 5"]
    assert [button.payload for button in feedback_message.keyboard.rows[0]] == [f"feedback:rate:{i}" for i in range(1, 6)]
    row = sqlite3.connect(db).execute("SELECT source, is_test FROM feedback_requests").fetchone()
    assert row == ("dev_test", 1)


@pytest.mark.parametrize(
    "key, table, text, payload_prefix",
    [
        ("cancellation", "cancellation_recovery_events", "Видим, что вы отменили запись 😔\n\nМожем подобрать другое удобное время.", "cancel_recovery:"),
        ("lost_client_30", "lost_client_events", "Давно вас не видели 😊\n\nХотите записаться снова? Подберём удобное время.", "lost_clients:book:"),
        ("lost_client_60", "lost_client_events", "Похоже, вы давно не заглядывали к нам.\n\nПодберём удобное время?", "lost_clients:book:"),
        ("lost_client_90", "lost_client_events", "Мы скучаем 😄\n\nДля вас есть специальное предложение на возвращение.", "lost_clients:book:"),
        ("birthday", "birthday_funnel_events", "Скоро ваш день рождения, поздравляем 🎉 😊\n\nХотим сделать вам приятный подарок - покажите это сообщение администратору при оплате.", "birthday_funnel:book:"),
        ("repeat_visit", "repeat_visit_events", None, "repeat_visit:book:"),
    ],
)
def test_funnel_test_creates_underlying_dev_event_and_cta(db, key, table, text, payload_prefix):
    sender = FakeSender()
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event(f"broadcast:dev_tests:{key}"), sender)))
    if text is not None:
        assert sender.sent[0].text == text
    assert sender.sent[0].recipient_id == "42"
    assert sender.sent[0].keyboard.rows[0][0].payload.startswith(payload_prefix)
    row = sqlite3.connect(db).execute(f"SELECT source, is_test, status FROM {table}").fetchone()
    assert row[0] == "dev_test"
    assert row[1] == 1
    assert row[2] == "sent"


@pytest.mark.parametrize("key", ["booking_confirm_2d", "booking_reminder_2h"])
def test_reminder_tests_target_developer_and_mark_history_dev_test(db, key):
    sender = FakeSender()
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event(f"broadcast:dev_tests:{key}"), sender)))
    assert sender.sent[0].recipient_id == "42"
    row = sqlite3.connect(db).execute("SELECT status, metadata_json FROM notification_history").fetchone()
    assert row[0] == "sent"
    assert "dev_test" in row[1]


def test_cleanup_requires_confirmation_and_deletes_only_test_rows(db):
    repo = NotificationTestEventsRepository(str(db))
    repo.create_test_event(event_type="self", target_platform_user_id="42")
    sqlite3.connect(db).execute("INSERT INTO notification_test_events(event_type, target_platform_user_id, source, is_test, payload_json, status, created_at_utc) VALUES('real','42','prod',0,'{}','created','now')").connection.commit()

    sender = FakeSender()
    asyncio.run(handle_settings_notifications_run_test(RouterContext(_event("broadcast:dev_tests:cleanup"), sender)))
    assert sender.sent[-1].text.startswith("🧹 Очистить тестовые события?")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM notification_test_events").fetchone()[0] == 2

    asyncio.run(handle_settings_notifications_cleanup_confirm(RouterContext(_event("broadcast:dev_tests:cleanup_confirm"), sender)))
    rows = sqlite3.connect(db).execute("SELECT event_type, source, is_test FROM notification_test_events").fetchall()
    assert rows == [("real", "prod", 0)]


def test_no_aiogram_imports_in_max():
    for root, _, files in os.walk("max_barbershop_bot"):
        for name in files:
            if name.endswith(".py"):
                text = open(os.path.join(root, name), encoding="utf-8").read()
                assert "import aiogram" not in text
                assert "from aiogram" not in text
