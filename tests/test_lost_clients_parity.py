from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
import importlib.util
import sys
import types
from pathlib import Path as _Path

broadcasts_stub = types.ModuleType("max_barbershop_bot.flows.broadcasts")
async def _open_segment_broadcast_text(*args, **kwargs):
    return None
broadcasts_stub.open_segment_broadcast_text = _open_segment_broadcast_text
sys.modules.setdefault("max_barbershop_bot.flows.broadcasts", broadcasts_stub)
_lost_spec = importlib.util.spec_from_file_location("lost_clients_flow_under_test", _Path("max_barbershop_bot/flows/lost_clients.py"))
lost_clients_flow_under_test = importlib.util.module_from_spec(_lost_spec)
assert _lost_spec.loader is not None
_lost_spec.loader.exec_module(lost_clients_flow_under_test)
handle_lost_client_booking_cta = lost_clients_flow_under_test.handle_lost_client_booking_cta
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.lost_client_events import LostClientEventsRepository
from max_barbershop_bot.repositories.users import UserCreate, UsersRepository
from max_barbershop_bot.services import lost_clients
from max_barbershop_bot.services.lost_clients import run_lost_clients_scan


def _db(tmp_path):
    path = str(tmp_path / "bot.sqlite3")
    init_database(path)
    return path


def _settings(path: str, lost: dict | None = None, anti: dict | None = None) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO yclients_settings(company_id, partner_token, user_token, branch_timezone, is_active) VALUES('1','p','u','UTC',1)")
        conn.execute("INSERT INTO app_settings(key, value) VALUES('lost_clients', ?)", (json.dumps(lost or {"enabled": True, "threshold_days": [30, 60, 90], "exclude_has_future_booking": True, "text_30": "30", "text_60": "60", "text_90": "90"}),))
        conn.execute("INSERT INTO app_settings(key, value) VALUES('anti_spam', ?)", (json.dumps(anti or {"min_interval_hours": 48}),))
        conn.commit()


def _user(path: str, *, enabled: bool = True):
    return UsersRepository(path).create(UserCreate(platform_user_id="100", max_user_id="100", yclients_client_id="yc1", notifications_enabled=enabled))


class FakeYClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeSender:
    def __init__(self, result: MaxSendResult | None = None):
        self.result = result or MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id="100")
        self.sent = []

    async def send_to_user(self, user_id, text, **kwargs):
        self.sent.append((user_id, text, kwargs.get("keyboard"), kwargs.get("metadata")))
        return self.result

    async def send_to_chat(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("keyboard"), kwargs.get("metadata")))
        return self.result

    async def answer_callback(self, callback_id):
        self.callback_id = callback_id


async def _scan(path, monkeypatch, records, sender=None, **kwargs):
    monkeypatch.setattr(lost_clients, "_build_client", lambda settings: FakeYClient())

    async def fake_list_user_bookings(*args, **kwargs):
        return {"data": records}

    monkeypatch.setattr(lost_clients, "list_user_bookings", fake_list_user_bookings)
    return await run_lost_clients_scan(sender or FakeSender(), database_path=path, now=datetime(2026, 7, 9, tzinfo=UTC), **kwargs)


def test_repository_telegram_fields_default_status_source_and_timestamps(tmp_path):
    path = _db(tmp_path)
    repo = LostClientEventsRepository(path)
    event = repo.create_event(yclients_client_id="yc1", client_tg_id="100", threshold_days=60, segment_key="lost_60", last_visit_datetime_utc="2026-05-01T10:00:00+00:00", last_visit_id="v1", source="dev_test", is_test=True)
    assert event.status == "candidate"
    assert event.source == "dev_test"
    assert event.is_test is True
    assert event.yclients_client_id == "yc1"
    assert event.client_tg_id == "100"
    assert repo.mark_status(event.id, "sent", sent=True).sent_at_utc is not None
    assert repo.mark_status(event.id, "clicked_booking", clicked=True).clicked_at_utc is not None


def test_recent_sent_threshold_specific_and_recent_stats(tmp_path):
    path = _db(tmp_path)
    repo = LostClientEventsRepository(path)
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    repo.create_event(client_tg_id="100", threshold_days=30, sent_at_utc=old, status="sent")
    assert repo.has_recent_sent("100", 30, 2) is False
    repo.create_event(client_tg_id="100", threshold_days=60, sent_at_utc=datetime.now(UTC).isoformat(), status="sent")
    assert repo.has_recent_sent("100", 60, 2) is True
    assert repo.has_recent_sent("100", 30, 2) is False
    assert repo.get_recent_stats(7) == 1


def test_completed_predicate_and_z_parser_match_telegram():
    assert lost_clients._record_datetime_utc({"datetime": "2026-07-09T10:00:00Z"}).tzinfo is not None
    assert lost_clients._is_completed_visit({"attendance": 1}) is True
    assert lost_clients._is_completed_visit({"visit_attendance": "1"}) is True
    assert lost_clients._is_completed_visit({"status": "completed"}) is True
    assert lost_clients._is_completed_visit({"attendance": 0, "status": "completed"}) is False


def test_scan_disabled_without_force_returns_empty(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path, lost={"enabled": False, "threshold_days": [30, 60, 90]})
    _user(path)
    summary = asyncio.run(_scan(path, monkeypatch, []))
    assert summary.candidates == summary.sent == summary.skipped == summary.errors == 0


def test_scan_selects_highest_threshold_sends_text_and_cta(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path)
    _user(path)
    sender = FakeSender()
    records = [{"id": "v1", "datetime": "2026-04-01T10:00:00Z", "attendance": 1}]
    summary = asyncio.run(_scan(path, monkeypatch, records, sender=sender))
    assert summary.candidates == 1 and summary.sent == 1
    assert sender.sent[0][1] == "90"
    keyboard = sender.sent[0][2]
    assert keyboard.rows[0][0].text == "✨ Записаться"
    assert keyboard.rows[0][0].payload.startswith("lost_clients:book:")
    event = LostClientEventsRepository(path).find_latest_by_tg_threshold("100", 90)
    assert event.segment_key == "lost_90"
    assert event.status == "sent"


def test_scan_future_booking_creates_skip_event(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path)
    _user(path)
    records = [
        {"id": "v1", "datetime": "2026-05-01T10:00:00Z", "attendance": 1},
        {"id": "f1", "datetime": "2026-07-20T10:00:00Z", "attendance": 0},
    ]
    summary = asyncio.run(_scan(path, monkeypatch, records))
    assert summary.skipped == 1
    event = LostClientEventsRepository(path).find_latest_by_tg_threshold("100", 0)
    assert event.status == "skipped_has_future_booking"
    assert event.segment_key == "lost_skip_future"
    assert event.has_future_booking is True


def test_scan_recent_sent_antispam_blocked_and_failed_statuses(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path, anti={"min_interval_hours": 48, "enabled": False})
    _user(path)
    records = [{"id": "v1", "datetime": "2026-05-01T10:00:00Z", "attendance": 1}]
    summary = asyncio.run(_scan(path, monkeypatch, records))
    assert summary.skipped == 1
    event = LostClientEventsRepository(path).find_latest_by_tg_threshold("100", 60)
    assert event.status == "skipped"
    assert event.error_summary == "anti_spam_disabled"

    path2 = _db(tmp_path / "b")
    _settings(path2)
    _user(path2)
    blocked = FakeSender(MaxSendResult(ok=False, status_code=403, message_id=None, recipient_type="user", recipient_id="100", is_blocked=True, error_message="forbidden"))
    summary = asyncio.run(_scan(path2, monkeypatch, records, sender=blocked))
    assert summary.errors == 1
    assert LostClientEventsRepository(path2).find_latest_by_tg_threshold("100", 60).status == "blocked"

    path3 = _db(tmp_path / "c")
    _settings(path3)
    _user(path3)
    failed = FakeSender(MaxSendResult(ok=False, status_code=400, message_id=None, recipient_type="user", recipient_id="100", error_message="bad request"))
    summary = asyncio.run(_scan(path3, monkeypatch, records, sender=failed))
    assert summary.errors == 1
    assert LostClientEventsRepository(path3).find_latest_by_tg_threshold("100", 60).status == "failed"


def test_scan_skips_disabled_notifications_no_completed_and_duplicate(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path)
    _user(path, enabled=False)
    summary = asyncio.run(_scan(path, monkeypatch, [{"datetime": "2026-05-01T10:00:00Z", "attendance": 1}]))
    assert summary.skipped == 1

    path2 = _db(tmp_path / "n")
    _settings(path2)
    _user(path2)
    summary = asyncio.run(_scan(path2, monkeypatch, [{"datetime": "2026-05-01T10:00:00Z", "attendance": 0}]))
    assert summary.candidates == 0 and summary.sent == 0

    path3 = _db(tmp_path / "d")
    _settings(path3)
    _user(path3)
    repo = LostClientEventsRepository(path3)
    repo.create_event(client_tg_id="100", threshold_days=60, sent_at_utc=datetime.now(UTC).isoformat(), status="sent")
    summary = asyncio.run(_scan(path3, monkeypatch, [{"datetime": "2026-05-01T10:00:00Z", "attendance": 1}]))
    assert summary.skipped == 1 and summary.sent == 0


def test_working_hours_skip(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _settings(path, lost={"enabled": True, "threshold_days": [30, 60, 90], "working_hours": {"enabled": True, "start": "10:00", "end": "11:00"}})
    _user(path)
    summary = asyncio.run(_scan(path, monkeypatch, [{"datetime": "2026-05-01T10:00:00Z", "attendance": 1}]))
    assert summary.candidates == summary.sent == summary.skipped == summary.errors == 0


def test_cta_click_marks_clicked_and_sets_attribution(tmp_path, monkeypatch):
    path = _db(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", path)
    event = LostClientEventsRepository(path).create_event(platform_user_id="100", client_tg_id="100", yclients_client_id="yc1", threshold_days=60, source="dev_test", is_test=True, status="sent")

    async def fake_booking_start(context):
        state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, "booking_opened", True)

    booking_stub = types.ModuleType("max_barbershop_bot.flows.booking")
    booking_stub.handle_booking_start = fake_booking_start
    monkeypatch.setitem(sys.modules, "max_barbershop_bot.flows.booking", booking_stub)
    ctx = RouterContext(
        event=NormalizedEvent(update_type="callback", text=None, platform_user_id="100", max_user_id="100", chat_id="c1", callback_payload=f"lost_clients:book:{event.id}", callback_id="cb1"),
        sender=FakeSender(),
    )
    asyncio.run(handle_lost_client_booking_cta(ctx))
    loaded = LostClientEventsRepository(path).get_event(event.id)
    assert loaded.status == "clicked_booking"
    assert loaded.clicked_at_utc is not None
    assert state.get_state_data_value("100", "c1", "booking_source") == "lost_client"
    assert state.get_state_data_value("100", "c1", "booking_origin_type") == "lost_client"
    assert state.get_state_data_value("100", "c1", "lost_client_event_id") == event.id
    assert state.get_state_data_value("100", "c1", "notification_event_id") == event.id
    assert state.get_state_data_value("100", "c1", "lost_days") == 60
    assert state.get_state_data_value("100", "c1", "notification_is_test") is True
    assert state.get_state_data_value("100", "c1", "notification_source") == "dev_test"
    assert state.get_state_data_value("100", "c1", "booking_opened") is True


def test_booking_comment_marker_only_for_lost_client_context():
    import importlib.util
    spec = importlib.util.spec_from_file_location("booking_flow_comment_under_test", _Path("max_barbershop_bot/flows/booking.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    base = "Клиент записался из MAX бота"
    marked = module.apply_lost_client_discount_comment(base, booking_origin_type="lost_client", lost_days=60)
    assert marked.endswith("Клиент не посещал 60 дней. НУЖНО СДЕЛАТЬ СКИДКУ")
    assert module.apply_lost_client_discount_comment(marked, booking_origin_type="lost_client", lost_days=60) == marked
    assert module.apply_lost_client_discount_comment(base, booking_origin_type="repeat_visit", lost_days=60) == base
    assert module.apply_lost_client_discount_comment(base, booking_origin_type="lost_client", lost_days=45) == base
