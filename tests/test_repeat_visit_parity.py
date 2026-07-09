from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
import importlib.util
from pathlib import Path as _Path

_booking_spec = importlib.util.spec_from_file_location("booking_flow_under_test", _Path("max_barbershop_bot/flows/booking.py"))
booking_flow_under_test = importlib.util.module_from_spec(_booking_spec)
assert _booking_spec.loader is not None
_booking_spec.loader.exec_module(booking_flow_under_test)
handle_repeat_visit_booking_start = booking_flow_under_test.handle_repeat_visit_booking_start
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.repeat_visit_events import RepeatVisitEventsRepository
from max_barbershop_bot.repositories.users import UserCreate, UsersRepository
from max_barbershop_bot.services import repeat_visit
from max_barbershop_bot.services.repeat_visit import BUTTON_CB_PREFIX, FALLBACK_TEXT, process_due_repeat_visit_events, schedule_repeat_visit_events, select_repeat_visit_text


def _db(tmp_path):
    path = str(tmp_path / "bot.sqlite3")
    init_database(path)
    return path


def _columns(path: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(repeat_visit_events)")}


def _add_settings(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO yclients_settings(company_id, partner_token, user_token, branch_timezone, is_active) VALUES('1','p','u','UTC',1)")
        conn.commit()


def _add_user(path: str, user_id: str = "100", yc_id: str = "yc1", *, enabled: bool = True):
    return UsersRepository(path).create(UserCreate(platform_user_id=user_id, max_user_id=user_id, chat_id=None, yclients_client_id=yc_id, notifications_enabled=enabled))


class FakeSender:
    def __init__(self, result: MaxSendResult | None = None):
        self.calls = []
        self.result = result or MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id="100")

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.calls.append(("user", user_id, text, keyboard))
        return self.result

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.calls.append(("chat", chat_id, text, keyboard))
        return self.result

    async def answer_callback(self, callback_id):
        self.calls.append(("answer", callback_id))


class FakeYClientsService:
    records = []

    def __init__(self, client, company_id):
        self.client = client
        self.company_id = company_id

    async def get_client_records(self, **kwargs):
        return {"data": list(self.records)}


@asynccontextmanager
async def fake_client(settings):
    yield SimpleNamespace(close=lambda: None)


def _patch_yclients(monkeypatch, records):
    FakeYClientsService.records = records
    monkeypatch.setattr(repeat_visit, "YClientsServiceLayer", FakeYClientsService)
    monkeypatch.setattr(repeat_visit, "build_yclients_client_from_active_settings", fake_client)


def test_repeat_visit_migration_adds_telegram_equivalent_columns(tmp_path):
    path = _db(tmp_path)
    columns = _columns(path)
    for column in {
        "yclients_visit_id", "yclients_service_id", "service_name", "last_visit_datetime_utc", "delay_days",
        "scheduled_send_at_utc", "selected_template_index", "selected_template_text", "clicked_at_utc",
        "branch_timezone", "source", "is_test", "error_summary", "sent_at_utc", "created_at_utc", "updated_at_utc",
    }:
        assert column in columns


def test_repeat_visit_repository_creates_event_with_new_and_legacy_fields(tmp_path):
    path = _db(tmp_path)
    repo = RepeatVisitEventsRepository(path)
    event = repo.create_event(platform_user_id="100", yclients_record_id="v1", yclients_client_id="yc1", yclients_visit_id="v1", yclients_service_id="s1", service_name="Стрижка", last_visit_datetime_utc="2026-07-01T10:00:00+00:00", delay_days=30, scheduled_at="2026-07-31T10:00:00+00:00", scheduled_send_at_utc="2026-07-31T10:00:00+00:00", selected_template_index=2, selected_template_text="Текст", status="pending", branch_timezone="UTC", source="dev_test", is_test=True)
    assert event is not None
    loaded = repo.get_event(event.id)
    assert loaded.yclients_record_id == "v1"
    assert loaded.yclients_visit_id == "v1"
    assert loaded.yclients_service_id == "s1"
    assert loaded.selected_template_index == 2
    assert loaded.selected_template_text == "Текст"
    assert loaded.source == "dev_test"
    assert loaded.is_test is True


def test_repeat_visit_repository_marks_statuses_and_dedups_and_cooldown(tmp_path):
    path = _db(tmp_path)
    repo = RepeatVisitEventsRepository(path)
    event = repo.create_event(platform_user_id="100", yclients_record_id="v1", yclients_visit_id="v1", yclients_service_id="s1", scheduled_at="2026-07-01T10:00:00+00:00", status="pending")
    assert repo.has_event_for_visit("100", "v1", "s1") is True
    repo.mark_status(event.id, "sent", sent=True)
    assert repo.get_event(event.id).sent_at_utc is not None
    assert repo.has_recent_sent("100", 48) is True
    repo.mark_status(event.id, "clicked_booking", clicked=True)
    assert repo.get_event(event.id).clicked_at_utc is not None
    repo.mark_status(event.id, "failed", error_summary="bad")
    assert repo.get_event(event.id).error_summary == "bad"
    repo.mark_status(event.id, "blocked", error_summary="forbidden")
    assert repo.get_event(event.id).status == "blocked"


def test_repeat_visit_text_selection_returns_active_template(monkeypatch):
    settings = {"templates": ["Текст 1", "Текст 2", "Текст 3"]}
    monkeypatch.setattr("max_barbershop_bot.services.repeat_visit.random.choice", lambda items: items[1])
    assert select_repeat_visit_text(settings) == (2, "Текст 2")


def test_repeat_visit_text_selection_ignores_blank_and_uses_fallback(monkeypatch):
    settings = {"templates": ["", "  ", "Текст 3", None]}
    monkeypatch.setattr("max_barbershop_bot.services.repeat_visit.random.choice", lambda items: items[0])
    assert select_repeat_visit_text(settings) == (3, "Текст 3")
    assert select_repeat_visit_text({"templates": ["", " ", None]}) == (0, FALLBACK_TEXT)


def test_repeat_visit_text_selection_single_and_not_hardcoded(monkeypatch):
    assert select_repeat_visit_text({"templates": ["", "Один"]}) == (2, "Один")
    monkeypatch.setattr("max_barbershop_bot.services.repeat_visit.random.choice", lambda items: items[-1])
    assert select_repeat_visit_text({"templates": ["Первый", "Второй"]}) == (2, "Второй")


def test_repeat_visit_scan_finds_latest_completed_and_sends_selected_text(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _add_settings(path)
    _add_user(path)
    now = datetime(2026, 7, 9, tzinfo=UTC)
    _patch_yclients(monkeypatch, [
        {"id": "old", "datetime": (now - timedelta(days=60)).isoformat(), "attendance": 1, "services": [{"id": "s1", "title": "Old"}]},
        {"id": "new", "datetime": (now - timedelta(days=31)).isoformat(), "status": "visit", "services": [{"id": "s2", "title": "Стрижка"}]},
    ])
    count = asyncio.run(schedule_repeat_visit_events(database_path=path, now=now, settings={"templates": ["", "Повтор"]}))
    assert count == 1
    event = RepeatVisitEventsRepository(path).get_event(platform="max", platform_user_id="100", yclients_record_id="new")
    assert event.status == "pending"
    assert event.yclients_service_id == "s2"
    assert event.selected_template_text == "Повтор"
    sender = FakeSender()
    sent = asyncio.run(process_due_repeat_visit_events(sender, database_path=path, settings={"templates": ["Повтор"]}))
    assert sent == 1
    assert sender.calls[0][2] == "Повтор"
    assert sender.calls[0][3].rows[0][0].text == "✂️ Записаться"
    assert sender.calls[0][3].rows[0][0].payload == f"{BUTTON_CB_PREFIX}{event.id}"
    assert RepeatVisitEventsRepository(path).get_event(event.id).status == "sent"


def test_repeat_visit_scan_skips_no_completed_newer_future_duplicate_antispam_disabled(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _add_settings(path)
    now = datetime(2026, 7, 9, tzinfo=UTC)
    # no completed
    _add_user(path, "u1", "yc1")
    _patch_yclients(monkeypatch, [{"id": "f1", "datetime": (now + timedelta(days=1)).isoformat(), "status": "booked"}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) == 0
    # newer than delay
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE users SET yclients_client_id='yc2' WHERE platform_user_id='u1'")
        conn.commit()
    _patch_yclients(monkeypatch, [{"id": "n1", "datetime": (now - timedelta(days=5)).isoformat(), "attendance": 1, "services": [{"id": "s1"}]}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) == 0
    # future booking skip
    _patch_yclients(monkeypatch, [{"id": "v1", "datetime": (now - timedelta(days=31)).isoformat(), "attendance": 1, "services": [{"id": "s1"}]}, {"id": "future", "datetime": (now + timedelta(days=1)).isoformat(), "status": "booked"}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) == 1
    rows = sqlite3.connect(path).execute("SELECT status FROM repeat_visit_events WHERE status='skipped_has_future_booking'").fetchall()
    assert rows
    # duplicate skip
    _add_user(path, "u2", "yc3")
    repo = RepeatVisitEventsRepository(path)
    repo.create_event(platform_user_id="u2", yclients_record_id="v2", yclients_visit_id="v2", yclients_service_id="s1", scheduled_at=now.isoformat(), status="sent")
    _patch_yclients(monkeypatch, [{"id": "v2", "datetime": (now - timedelta(days=31)).isoformat(), "attendance": 1, "services": [{"id": "s1"}]}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) >= 1
    assert sqlite3.connect(path).execute("SELECT 1 FROM repeat_visit_events WHERE status='skipped_duplicate'").fetchone()
    # antispam skip
    _add_user(path, "u3", "yc4")
    repo.create_event(platform_user_id="u3", yclients_record_id="prev", yclients_visit_id="prev", scheduled_at=now.isoformat(), status="sent", sent_at_utc=now.isoformat())
    _patch_yclients(monkeypatch, [{"id": "v3", "datetime": (now - timedelta(days=31)).isoformat(), "attendance": 1, "services": [{"id": "s1"}]}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) >= 1
    assert sqlite3.connect(path).execute("SELECT 1 FROM repeat_visit_events WHERE status='skipped_antispam'").fetchone()
    # disabled user skip
    _add_user(path, "u4", "yc5", enabled=False)
    _patch_yclients(monkeypatch, [{"id": "v4", "datetime": (now - timedelta(days=31)).isoformat(), "attendance": 1, "services": [{"id": "s1"}]}])
    assert asyncio.run(schedule_repeat_visit_events(database_path=path, now=now)) >= 1
    assert sqlite3.connect(path).execute("SELECT 1 FROM repeat_visit_events WHERE status='skipped_unsubscribed'").fetchone()


def test_repeat_visit_delivery_blocked_and_failed(tmp_path):
    path = _db(tmp_path)
    _add_user(path)
    repo = RepeatVisitEventsRepository(path)
    blocked = repo.create_event(platform_user_id="100", yclients_record_id="b1", yclients_visit_id="b1", scheduled_at="2026-07-01T10:00:00+00:00", selected_template_text="Текст", status="pending")
    sender = FakeSender(MaxSendResult(ok=False, status_code=403, message_id=None, recipient_type="user", recipient_id="100", is_blocked=True, error_code="blocked"))
    sent = asyncio.run(process_due_repeat_visit_events(sender, database_path=path, settings={"enabled": False}))
    assert sent == 0
    assert repo.get_event(blocked.id).status == "blocked"
    _add_user(path, "101", "yc2")
    failed = repo.create_event(platform_user_id="101", yclients_record_id="f1", yclients_visit_id="f1", scheduled_at="2026-07-01T10:00:00+00:00", selected_template_text="Текст", status="pending")
    sender = FakeSender(MaxSendResult(ok=False, status_code=400, message_id=None, recipient_type="user", recipient_id="101", error_code="bad"))
    asyncio.run(process_due_repeat_visit_events(sender, database_path=path, settings={"enabled": False}))
    assert repo.get_event(failed.id).status == "failed"
    assert repo.get_event(failed.id).error_summary


def test_repeat_visit_cta_click_marks_clicked_and_preserves_attribution(tmp_path, monkeypatch):
    path = _db(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", path)
    repo = RepeatVisitEventsRepository(path)
    event = repo.create_event(platform_user_id="100", yclients_record_id="v1", yclients_client_id="yc1", scheduled_at="2026-07-01T10:00:00+00:00", status="sent", source="dev_test", is_test=True)
    normalized = NormalizedEvent(update_type="message_callback", platform_user_id="100", max_user_id="100", chat_id=None, text=None, callback_payload=f"{BUTTON_CB_PREFIX}{event.id}", callback_id="cb1")
    sender = FakeSender()
    context = RouterContext(normalized, sender)  # type: ignore[arg-type]
    asyncio.run(handle_repeat_visit_booking_start(context))
    loaded = repo.get_event(event.id)
    assert loaded.status == "clicked_booking"
    assert loaded.clicked_at_utc is not None
    assert state.get_state_data_value("100", None, "booking_source") == "repeat_visit"
    assert state.get_state_data_value("100", None, "booking_origin_type") == "repeat_visit"
    assert state.get_state_data_value("100", None, "repeat_visit_event_id") == event.id
    assert state.get_state_data_value("100", None, "notification_event_id") == event.id
    assert state.get_state_data_value("100", None, "yclients_client_id") == "yc1"
    assert state.get_state_data_value("100", None, "notification_is_test") is True
    assert state.get_state_data_value("100", None, "notification_source") == "dev_test"
