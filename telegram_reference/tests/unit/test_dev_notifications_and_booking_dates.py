from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.handlers import booking_flow, notifications
from app.repositories.birthday_funnel_events import get_event as get_birthday_event
from app.repositories.cancellation_recovery_events import get_event as get_cancel_event
from app.repositories.post_visit_feedback_events import get_event as get_feedback_event
from tests.sync import run


class DummyBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return SimpleNamespace(message_id=len(self.messages))


class DummyMessage:
    def __init__(self) -> None:
        self.answers: list[dict] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup})
        return SimpleNamespace(message_id=len(self.answers))

    async def edit_text(self, text, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup, "edit": True})
        return SimpleNamespace(message_id=len(self.answers))


class DummyCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=notifications.DEVELOPER_TG_ID)
        self.bot = DummyBot()
        self.message = DummyMessage()
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=None):
        self.answers.append({"text": text, "show_alert": show_alert})


def _callback_values(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_dev_test_callbacks_registered_and_valid() -> None:
    callbacks = _callback_values(notifications.dev_tests_kb())
    for expected in (
        "broadcast:dev_tests:post_visit_review",
        "broadcast:dev_tests:cancellation",
        "broadcast:dev_tests:birthday",
    ):
        assert expected in callbacks
    assert all(isinstance(value, str) and len(value.encode("utf-8")) <= 64 for value in callbacks)


def test_post_visit_dev_test_sends_repeatable_rating_message(initialized_db) -> None:
    for _ in range(2):
        callback = DummyCallback("broadcast:dev_tests:post_visit_review")
        run(notifications.run_dev_test(callback, SimpleNamespace()))
        assert callback.bot.messages
        message = callback.bot.messages[-1]
        assert "Как прошёл ваш визит" in message["text"]
        rating_callbacks = _callback_values(message["reply_markup"])
        assert len(rating_callbacks) == 5
        assert all(value.startswith("feedback_rate:") for value in rating_callbacks)
        event_id = int(rating_callbacks[0].split(":")[1])
        event = run(get_feedback_event(event_id))
        assert event["source"] == "dev_test"
        assert int(event["is_test"]) == 1


def test_cancellation_dev_test_sends_repeatable_notification_without_yclients_writes(initialized_db, monkeypatch) -> None:
    async def forbidden_yclients(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("YClients must not be called by cancellation dev test")

    monkeypatch.setattr("app.services.cancellation_recovery.build_yclients_client", forbidden_yclients)
    for _ in range(2):
        callback = DummyCallback("broadcast:dev_tests:cancellation")
        run(notifications.run_dev_test(callback, SimpleNamespace()))
        assert callback.bot.messages
        message = callback.bot.messages[-1]
        callbacks = _callback_values(message["reply_markup"])
        assert any(value.startswith("cancel_recovery:rebook:") for value in callbacks)
        assert any(value.startswith("cancel_recovery:date:") for value in callbacks)
        event_id = int(callbacks[0].split(":")[-1])
        event = run(get_cancel_event(event_id))
        assert event["status"] == "sent"
        assert event["source"] == "dev_test"
        assert int(event["is_test"]) == 1


def test_birthday_dev_test_sends_repeatable_booking_cta(initialized_db) -> None:
    for _ in range(2):
        callback = DummyCallback("broadcast:dev_tests:birthday")
        run(notifications.run_dev_test(callback, SimpleNamespace()))
        assert callback.bot.messages
        message = callback.bot.messages[-1]
        assert "день рождения" in message["text"].lower()
        callbacks = _callback_values(message["reply_markup"])
        assert len(callbacks) == 1
        assert callbacks[0].startswith("birthday_funnel:book:")
        event_id = int(callbacks[0].split(":")[-1])
        event = run(get_birthday_event(event_id))
        assert event["status"] == "sent"
        assert event["source"] == "dev_test"
        assert int(event["is_test"]) == 1


def test_booking_date_filter_hides_today_after_all_branch_slots(monkeypatch) -> None:
    async def fake_now(company_id: str):
        return datetime(2026, 5, 28, 22, 12, tzinfo=ZoneInfo("Europe/Moscow"))

    async def fake_load_slots(company_id: str, *, service_id, staff_id, iso_date):
        return [booking_flow.SlotItem(time="21:00", datetime_iso="2026-05-28T21:00:00")]

    monkeypatch.setattr(booking_flow, "_company_now", fake_now)
    monkeypatch.setattr(booking_flow, "_load_slots", fake_load_slots)
    result = run(
        booking_flow._filter_dates_with_available_slots(
            "12345", ["2026-05-28"], service_id="svc", staff_id="staff", user_tg_id=1
        )
    )
    assert result == []


def test_booking_date_filter_shows_date_with_future_branch_slot(monkeypatch) -> None:
    async def fake_now(company_id: str):
        return datetime(2026, 5, 28, 20, 12, tzinfo=ZoneInfo("Europe/Moscow"))

    async def fake_load_slots(company_id: str, *, service_id, staff_id, iso_date):
        return [booking_flow.SlotItem(time="21:00", datetime_iso="2026-05-28T21:00:00")]

    monkeypatch.setattr(booking_flow, "_company_now", fake_now)
    monkeypatch.setattr(booking_flow, "_load_slots", fake_load_slots)
    result = run(
        booking_flow._filter_dates_with_available_slots(
            "12345", ["2026-05-28"], service_id="svc", staff_id="staff", user_tg_id=1
        )
    )
    assert result == ["2026-05-28"]
