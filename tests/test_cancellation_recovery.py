from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import asyncio

import importlib.util
from pathlib import Path

_BOOKING_SPEC = importlib.util.spec_from_file_location("booking_flow_under_test", Path("max_barbershop_bot/flows/booking.py"))
booking_flow = importlib.util.module_from_spec(_BOOKING_SPEC)
assert _BOOKING_SPEC.loader is not None
_BOOKING_SPEC.loader.exec_module(booking_flow)
import max_barbershop_bot.services.cancellation_recovery as recovery_service
from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.cancellation_recovery_events import CancellationRecoveryEventsRepository
from max_barbershop_bot.repositories.users import UserCreate, UsersRepository
from max_barbershop_bot.ui.texts import CANCELLATION_RECOVERY_TEXT


class FakeSender:
    def __init__(self, result: MaxSendResult | None = None, *, raise_error: Exception | None = None) -> None:
        self.sent = []
        self.callbacks = []
        self._result = result
        self._raise_error = raise_error

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        if self._raise_error:
            raise self._raise_error
        self.sent.append(("user", str(user_id), text, keyboard, metadata))
        return self._result or MaxSendResult(ok=True, status_code=200, message_id="msg-user", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        if self._raise_error:
            raise self._raise_error
        self.sent.append(("chat", str(chat_id), text, keyboard, metadata))
        return self._result or MaxSendResult(ok=True, status_code=200, message_id="msg-chat", recipient_type="chat", recipient_id=str(chat_id))

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def _repo(tmp_path):
    db = tmp_path / "db.sqlite3"
    init_database(str(db))
    return str(db), CancellationRecoveryEventsRepository(str(db))


def _create_user(db: str, *, platform_user_id: str = "u1", max_user_id: str = "100", yclients_client_id: str = "yc1"):
    return UsersRepository(db).create(
        UserCreate(
            platform_user_id=platform_user_id,
            max_user_id=max_user_id,
            chat_id=None,
            first_name="Илья",
            phone="+79990000000",
            yclients_client_id=yclients_client_id,
        )
    )


def _create_due_event(repo: CancellationRecoveryEventsRepository, *, record_id: str = "r1", platform_user_id: str = "u1", is_test: bool = False):
    now = datetime.now(UTC)
    return repo.create_event(
        platform_user_id=platform_user_id,
        max_user_id="100",
        yclients_record_id=record_id,
        yclients_client_id="yc1",
        client_tg_id="100",
        staff_id="s1",
        staff_name="Тестовый мастер",
        service_id="svc1",
        service_name="Тестовая стрижка",
        cancelled_booking_datetime_utc=(now - timedelta(hours=1)).isoformat(),
        cancellation_detected_at_utc=(now - timedelta(hours=1)).isoformat(),
        scheduled_send_at_utc=(now - timedelta(minutes=1)).isoformat(),
        branch_timezone="Europe/Moscow",
        source="dev_test" if is_test else "my_bookings_cancel",
        is_test=is_test,
    )


def test_migration_adds_telegram_equivalent_columns(tmp_path) -> None:
    db, _ = _repo(tmp_path)
    with sqlite3.connect(db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cancellation_recovery_events)")}

    assert {
        "client_tg_id",
        "staff_id",
        "staff_name",
        "service_id",
        "service_name",
        "cancelled_booking_datetime_utc",
        "cancellation_detected_at_utc",
        "scheduled_send_at_utc",
        "branch_timezone",
        "is_test",
        "source",
        "clicked_at_utc",
        "error_summary",
    }.issubset(columns)


def test_repository_creates_dedups_and_preserves_telegram_fields(tmp_path) -> None:
    _, repo = _repo(tmp_path)
    event = _create_due_event(repo, is_test=True)
    duplicate = repo.create_event(
        platform_user_id="u1",
        max_user_id="100",
        yclients_record_id="r1",
        scheduled_send_at_utc=datetime.now(UTC).isoformat(),
        source="dev_test",
        is_test=True,
    )

    assert event is not None
    assert duplicate is not None
    assert duplicate.id == event.id
    assert event.staff_name == "Тестовый мастер"
    assert event.service_name == "Тестовая стрижка"
    assert event.branch_timezone == "Europe/Moscow"
    assert event.source == "dev_test"
    assert event.is_test is True
    assert event.max_user_id == "100"


def test_find_pending_to_send_uses_scheduled_send_and_old_scheduled_at_fallback(tmp_path) -> None:
    db, repo = _repo(tmp_path)
    now = datetime.now(UTC)
    due = _create_due_event(repo, record_id="due")
    repo.create_event(platform_user_id="u2", max_user_id="200", yclients_record_id="future", scheduled_send_at_utc=(now + timedelta(hours=1)).isoformat(), source="my_bookings_cancel")
    sent = repo.create_event(platform_user_id="u3", max_user_id="300", yclients_record_id="sent", scheduled_send_at_utc=(now - timedelta(hours=1)).isoformat(), source="my_bookings_cancel")
    assert sent is not None
    repo.set_status(sent.id, "sent", sent_at_utc=now.isoformat())
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO cancellation_recovery_events (platform, platform_user_id, yclients_record_id, max_user_id, scheduled_at, status)
            VALUES ('max', 'legacy', 'legacy-record', '400', ?, 'pending')
            """,
            ((now - timedelta(minutes=2)).isoformat(),),
        )
        connection.commit()

    found = repo.find_pending_to_send(now.isoformat())
    ids = {event.yclients_record_id for event in found}

    assert due is not None
    assert ids == {"due", "legacy-record"}



def test_due_event_sends_once_with_text_keyboard_and_source(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        db, repo = _repo(tmp_path)
        _create_user(db)
        event = _create_due_event(repo, is_test=True)

        async def no_future(*args, **kwargs):
            return False

        monkeypatch.setattr(recovery_service, "_has_future_booking", no_future)
        sender = FakeSender()

        sent = await recovery_service.process_due_cancellation_recovery_events(sender, database_path=db)
        sent_again = await recovery_service.process_due_cancellation_recovery_events(sender, database_path=db)
        updated = repo.get_event(event.id)

        assert sent == 1
        assert sent_again == 0
        assert len(sender.sent) == 1
        assert sender.sent[0][2] == CANCELLATION_RECOVERY_TEXT
        keyboard = sender.sent[0][3]
        assert [row[0].text for row in keyboard.rows] == ["✂️ Подобрать новое время", "📅 Выбрать другую дату", "Позже"]
        assert keyboard.rows[0][0].payload == f"cancel_recovery:rebook:{event.id}"
        assert updated.status == "sent"
        assert updated.source == "dev_test"
        assert updated.is_test is True

    asyncio.run(run())


def test_future_booking_skips_with_telegram_status(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        db, repo = _repo(tmp_path)
        _create_user(db)
        event = _create_due_event(repo)

        async def has_future(*args, **kwargs):
            return True

        monkeypatch.setattr(recovery_service, "_has_future_booking", has_future)
        sender = FakeSender()

        sent = await recovery_service.process_due_cancellation_recovery_events(sender, database_path=db)
        updated = repo.get_event(event.id)

        assert sent == 0
        assert sender.sent == []
        assert updated.status == "skipped_has_new_booking"
        assert updated.error_summary == "has_future_booking"

    asyncio.run(run())


def test_no_recipient_mapping_and_send_errors_mark_telegram_statuses(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        db, repo = _repo(tmp_path)
        event = repo.create_event(
            yclients_record_id="no-recipient",
            scheduled_send_at_utc=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            source="my_bookings_cancel",
        )

        async def no_future(*args, **kwargs):
            return False

        monkeypatch.setattr(recovery_service, "_has_future_booking", no_future)

        await recovery_service.process_due_cancellation_recovery_events(FakeSender(), database_path=db)
        assert repo.get_event(event.id).status == "failed"
        assert repo.get_event(event.id).error_summary == "no_telegram_mapping"

        _create_user(db, platform_user_id="u2", max_user_id="200")
        failed = repo.create_event(
            platform_user_id="u2",
            max_user_id="200",
            yclients_record_id="send-fail",
            scheduled_send_at_utc=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            source="my_bookings_cancel",
        )
        await recovery_service.process_due_cancellation_recovery_events(FakeSender(raise_error=RuntimeError("boom")), database_path=db)
        assert repo.get_event(failed.id).status == "failed"
        assert repo.get_event(failed.id).error_summary == "RuntimeError"

    asyncio.run(run())


def test_blocked_recipient_marks_blocked(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        db, repo = _repo(tmp_path)
        _create_user(db)
        event = _create_due_event(repo)

        async def no_future(*args, **kwargs):
            return False

        monkeypatch.setattr(recovery_service, "_has_future_booking", no_future)
        sender = FakeSender(
            MaxSendResult(
                ok=False,
                status_code=403,
                error_code="blocked",
                error_message="blocked",
                message_id=None,
                recipient_type="user",
                recipient_id="100",
                is_blocked=True,
            )
        )

        await recovery_service.process_due_cancellation_recovery_events(sender, database_path=db)

        assert repo.get_event(event.id).status == "blocked"
        assert repo.get_event(event.id).error_summary == "blocked"

    asyncio.run(run())


def test_cta_rebook_and_later_mark_clicks_and_preserve_attribution(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        db, repo = _repo(tmp_path)
        event = _create_due_event(repo, is_test=True)
        repo.set_status(event.id, "sent", sent_at_utc=datetime.now(UTC).isoformat())
        monkeypatch.setattr(booking_flow, "_database_path", lambda: db)

        async def fake_show_hub(context, *, push_current=True):
            state.set_current_screen(context.event.platform_user_id, context.event.chat_id, state.BOOKING_HUB_SCREEN)

        monkeypatch.setattr(booking_flow, "_show_booking_hub", fake_show_hub)
        sender = FakeSender()
        context = RouterContext(
            event=NormalizedEvent(
                update_type="message_callback",
                platform_user_id="u1",
                max_user_id="100",
                chat_id="c1",
                text=None,
                callback_payload=f"cancel_recovery:rebook:{event.id}",
                callback_id="cb1",
            ),
            sender=sender,
        )

        await booking_flow.handle_cancellation_recovery_booking_cta(context)
        clicked = repo.get_event(event.id)

        assert clicked.status == "clicked_rebook"
        assert clicked.clicked_at_utc is not None
        assert state.get_state_data_value("u1", "c1", "booking_source") == "cancellation_recovery"
        assert state.get_state_data_value("u1", "c1", "notification_event_id") == event.id
        assert state.get_state_data_value("u1", "c1", "notification_is_test") is True
        assert state.get_state_data_value("u1", "c1", "notification_source") == "dev_test"

        later = repo.create_event(
            platform_user_id="u1",
            max_user_id="100",
            yclients_record_id="later",
            scheduled_send_at_utc=datetime.now(UTC).isoformat(),
            source="my_bookings_cancel",
        )
        repo.set_status(later.id, "sent", sent_at_utc=datetime.now(UTC).isoformat())
        later_context = RouterContext(
            event=NormalizedEvent(
                update_type="message_callback",
                platform_user_id="u1",
                max_user_id="100",
                chat_id="c1",
                text=None,
                callback_payload=f"cancel_recovery:later:{later.id}",
                callback_id="cb2",
            ),
            sender=sender,
        )
        await booking_flow.handle_cancellation_recovery_booking_cta(later_context)

        assert repo.get_event(later.id).status == "clicked_later"
        assert sender.sent[-1][2] == "Хорошо, будем ждать вас позже 😊"

    asyncio.run(run())
