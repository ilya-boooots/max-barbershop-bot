from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.db.sqlite import execute
from app.handlers import notifications
from app.repositories import broadcasts as broadcasts_repo
from app.services import client_segments as client_segments_module
from app.services.client_segments import ClientSegmentService
from tests.factories import create_user
from tests.sync import run


@dataclass
class _Settings:
    company_id: str = "12345"


class _FakeYClientsClient:
    async def close(self) -> None:
        return None


async def _utc_branch_timezone(self) -> str:
    return "UTC"


async def _fake_settings() -> _Settings:
    return _Settings()


def test_all_clients_uses_yclients_clients_and_local_telegram_mapping(monkeypatch: pytest.MonkeyPatch):
    run(create_user(tg_id=1001, yclients_client_id=501, phone="+79990000501"))
    run(create_user(tg_id=1002, yclients_client_id=None, phone="+79990000502"))

    async def fake_all_clients(*, actor_tg_id=None):
        return [
            {"yclients_client_id": "501", "phone": "+79990000501"},
            {"yclients_client_id": "502", "phone": "+79990000502"},
            {"yclients_client_id": "503", "phone": "+79990000503"},
        ], {"company_id": "12345", "clients_count": 3}

    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_all_clients_from_yclients", fake_all_clients)

    rows = run(broadcasts_repo.resolve_one_time_audience("all_clients", actor_id=999))

    assert {int(row["tg_id"]) for row in rows} == {1001, 1002}


def test_active_30_uses_yclients_records_not_local_user_activity(monkeypatch: pytest.MonkeyPatch):
    run(create_user(tg_id=1101, yclients_client_id=601, phone="+79990000601"))
    run(execute("UPDATE users SET last_seen_at=?, last_activity_ts_utc=? WHERE user_id=?", ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", 1101)))
    called = {"active": False}

    async def fake_active(days=30, *, actor_tg_id=None):
        called["active"] = True
        assert days == 30
        return [{"yclients_client_id": "601", "phone": "+79990000601"}], {"company_id": "12345", "records_count": 1}

    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_active_clients_from_yclients", fake_active)

    rows = run(broadcasts_repo.resolve_one_time_audience("active_30", actor_id=999))

    assert called["active"] is True
    assert [int(row["tg_id"]) for row in rows] == [1101]


def test_no_future_booking_uses_yclients_future_records(monkeypatch: pytest.MonkeyPatch):
    service = ClientSegmentService()
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=3)).isoformat()

    async def fake_build_client():
        return _FakeYClientsClient(), "12345"

    async def fake_list_clients(client, *, company_id, page=1, count=200):
        assert company_id == "12345"
        return {"data": [
            {"id": 701, "phone": "+79990000701", "name": "Есть запись"},
            {"id": 702, "phone": "+79990000702", "name": "Без записи"},
        ]}

    async def fake_records(client, *, company_id, date_from, date_to, page=None, count=None, staff_id=None, status=None):
        assert date_from <= now.date().isoformat() <= date_to
        return {"data": [{"client": {"id": 701, "phone": "+79990000701"}, "datetime": future, "deleted": False, "attendance": 0}]}

    monkeypatch.setattr(client_segments_module, "get_yclients_settings", _fake_settings)
    monkeypatch.setattr(ClientSegmentService, "branch_timezone", _utc_branch_timezone)
    monkeypatch.setattr(client_segments_module, "build_yclients_client", fake_build_client)
    monkeypatch.setattr(client_segments_module, "list_clients", fake_list_clients)
    monkeypatch.setattr(client_segments_module, "list_bookings_by_date_range", fake_records)

    clients, diag = run(service.resolve_no_future_booking_clients_from_yclients(actor_tg_id=999))

    assert [client["yclients_client_id"] for client in clients] == ["702"]
    assert diag["excluded_future_booking_count"] == 1


@pytest.mark.parametrize("audience_key,days", [("lost_30", 30), ("lost_60", 60), ("lost_90", 90)])
def test_lost_audiences_use_yclients_last_visit_and_no_future_booking(monkeypatch: pytest.MonkeyPatch, audience_key: str, days: int):
    run(create_user(tg_id=1201 + days, yclients_client_id=800 + days, phone=f"+79990008{days:02d}"))
    called = {"days": None}

    async def fake_lost(requested_days, *, actor_tg_id=None):
        called["days"] = requested_days
        return [{"yclients_client_id": str(800 + days), "phone": f"+79990008{days:02d}"}], {"company_id": "12345", "records_count": 2}

    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_lost_clients_from_yclients", fake_lost)

    rows = run(broadcasts_repo.resolve_one_time_audience(audience_key, actor_id=999))

    assert called["days"] == days
    assert [int(row["tg_id"]) for row in rows] == [1201 + days]


def test_lost_yclients_resolver_excludes_clients_with_future_booking(monkeypatch: pytest.MonkeyPatch):
    service = ClientSegmentService()
    now = datetime.now(timezone.utc)
    old_visit = (now - timedelta(days=45)).isoformat()
    future = (now + timedelta(days=5)).isoformat()

    async def fake_build_client():
        return _FakeYClientsClient(), "12345"

    async def fake_records(client, *, company_id, date_from, date_to, page=None, count=None, staff_id=None, status=None):
        return {"data": [
            {"client": {"id": 901, "phone": "+79990000901"}, "datetime": old_visit, "deleted": False, "attendance": 1},
            {"client": {"id": 902, "phone": "+79990000902"}, "datetime": old_visit, "deleted": False, "attendance": 1},
            {"client": {"id": 902, "phone": "+79990000902"}, "datetime": future, "deleted": False, "attendance": 0},
        ]}

    monkeypatch.setattr(client_segments_module, "get_yclients_settings", _fake_settings)
    monkeypatch.setattr(ClientSegmentService, "branch_timezone", _utc_branch_timezone)
    monkeypatch.setattr(client_segments_module, "build_yclients_client", fake_build_client)
    monkeypatch.setattr(client_segments_module, "list_bookings_by_date_range", fake_records)

    clients, diag = run(service.resolve_lost_clients_from_yclients(30, actor_tg_id=999))

    assert [client["yclients_client_id"] for client in clients] == ["901"]
    assert diag["excluded_future_booking_count"] == 1


def test_send_to_self_still_bypasses_yclients(monkeypatch: pytest.MonkeyPatch):
    async def fail_yclients(*args, **kwargs):
        raise AssertionError("send_to_self must not call YClients")

    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_all_clients_from_yclients", fail_yclients)
    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_active_clients_from_yclients", fail_yclients)
    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_no_future_booking_clients_from_yclients", fail_yclients)
    monkeypatch.setattr(broadcasts_repo.segment_service, "resolve_lost_clients_from_yclients", fail_yclients)

    rows = run(broadcasts_repo.resolve_one_time_audience("send_to_self", actor_id=123))

    assert len(rows) == 1
    assert int(rows[0]["tg_id"]) == 123


class _FakeUser:
    id = 777


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


class _FakeCallback:
    data = "broadcast:aud:all_clients"

    def __init__(self) -> None:
        self.from_user = _FakeUser()
        self.message = _FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True


def test_empty_audience_returns_clean_empty_state(monkeypatch: pytest.MonkeyPatch, fsm_context):
    async def fake_resolve(audience_key, actor_id, payload=None):
        return []

    async def fake_role(user_id):
        return "admin"

    monkeypatch.setattr(notifications.broadcasts_repo, "resolve_one_time_audience", fake_resolve)
    monkeypatch.setattr(notifications, "resolve_role", fake_role)
    callback = _FakeCallback()

    run(notifications._start_one_time_from_audience(callback, fsm_context, "all_clients", role="admin"))

    assert callback.answered is True
    assert callback.message.answers == ["😌 В этой аудитории пока нет клиентов для рассылки."]


def test_segment_counts_use_yclients_sources(monkeypatch: pytest.MonkeyPatch):
    service = ClientSegmentService()

    async def fake_all_clients(*, actor_tg_id=None):
        return [{"yclients_client_id": "1"}, {"yclients_client_id": "2"}], {"company_id": "12345"}

    async def fake_active(days=30, *, actor_tg_id=None):
        assert days == 30
        return [{"yclients_client_id": "3"}], {"company_id": "12345"}

    async def fake_no_future(*, actor_tg_id=None):
        return [{"yclients_client_id": "4"}, {"yclients_client_id": "5"}, {"yclients_client_id": "6"}], {"company_id": "12345"}

    monkeypatch.setattr(service, "resolve_all_clients_from_yclients", fake_all_clients)
    monkeypatch.setattr(service, "resolve_active_clients_from_yclients", fake_active)
    monkeypatch.setattr(service, "resolve_no_future_booking_clients_from_yclients", fake_no_future)

    assert run(service.count_segment_clients("all_clients")) == 2
    assert run(service.count_segment_clients("active_30")) == 1
    assert run(service.count_segment_clients("no_future_booking")) == 3


def test_long_service_category_names_get_safe_callback_ids(monkeypatch: pytest.MonkeyPatch):
    service = ClientSegmentService()
    long_name = "Очень длинная категория услуг для премиального ухода и окрашивания бороды"

    async def fake_build_client():
        return _FakeYClientsClient(), "12345"

    async def fake_categories(client, *, company_id):
        return {"data": []}

    async def fake_services(client, *, company_id):
        return {"data": [{"id": "9001", "title": "Услуга", "category": long_name}]}

    monkeypatch.setattr(client_segments_module, "get_yclients_settings", _fake_settings)
    monkeypatch.setattr(client_segments_module, "build_yclients_client", fake_build_client)
    monkeypatch.setattr(client_segments_module, "list_service_categories", fake_categories)
    monkeypatch.setattr(client_segments_module, "get_services", fake_services)

    categories, _ = run(service.list_service_categories(actor_tg_id=999))

    assert categories == [{"id": categories[0]["id"], "name": long_name}]
    callback_data = f"broadcast:segments:by_service_category:{categories[0]['id']}"
    use_callback_data = f"broadcast:segments:use:by_service_category:{categories[0]['id']}"
    assert categories[0]["id"].startswith("namehash:")
    assert len(callback_data.encode("utf-8")) <= 64
    assert len(use_callback_data.encode("utf-8")) <= 64
