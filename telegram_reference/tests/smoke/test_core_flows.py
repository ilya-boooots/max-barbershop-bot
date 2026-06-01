from __future__ import annotations

import importlib

import pytest

from app.core.permissions import DEVELOPER_TG_ID
from app.db.sqlite import fetchone
from app.handlers.booking_flow import booking_flow_start
from app.handlers.loyalty_mvp import LOYALTY_BTN, open_loyalty_menu
from app.handlers.start import handle_menu, handle_start
from app.repositories.users import ensure_protected_developer
from tests.helpers import FakeMessage
from tests.sync import run


pytestmark = pytest.mark.smoke


def test_import_and_db_startup_health(initialized_db):
    importlib.import_module("app.handlers")
    importlib.import_module("app.main")
    row = run(fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
    assert row is not None


def test_start_registered_user_opens_single_menu_path(initialized_db, fsm_context, monkeypatch: pytest.MonkeyPatch):
    msg = FakeMessage(user_id=3010, text="/start")

    async def fake_get_user(_uid: int):
        return {"role": "user", "is_registered": 1, "phone": "+7999", "birth_date": "1990-01-01"}

    calls = {"render": 0}

    async def fake_render_main(*_args, **_kwargs):
        calls["render"] += 1

    async def fake_upsert(**_kwargs):
        return None

    async def fake_set_username(*_args, **_kwargs):
        return None

    async def fake_apply_ref(**_kwargs):
        return "ignored"

    async def fake_sync_ref(**_kwargs):
        return "ok"

    monkeypatch.setattr("app.handlers.start.upsert_telegram_user", fake_upsert)
    monkeypatch.setattr("app.handlers.start.set_username", fake_set_username)
    monkeypatch.setattr("app.handlers.start.get_user", fake_get_user)
    monkeypatch.setattr("app.handlers.start.apply_start_referral", fake_apply_ref)
    monkeypatch.setattr("app.handlers.start.sync_referral_reward_if_eligible", fake_sync_ref)
    monkeypatch.setattr("app.handlers.start.render_main_by_role", fake_render_main)

    run(handle_start(msg, fsm_context))
    assert calls["render"] == 1


def test_menu_command_has_no_duplicate_responses(initialized_db, fsm_context, monkeypatch: pytest.MonkeyPatch):
    msg = FakeMessage(user_id=3020, text="/menu")
    calls = {"render": 0}

    async def fake_render_main(*_args, **_kwargs):
        calls["render"] += 1

    async def fake_set_username(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.handlers.start.set_username", fake_set_username)
    monkeypatch.setattr("app.handlers.start.render_main_by_role", fake_render_main)
    run(handle_menu(msg, fsm_context))
    assert calls["render"] == 1


def test_booking_hub_and_loyalty_open(initialized_db, fsm_context, monkeypatch: pytest.MonkeyPatch):
    booking_msg = FakeMessage(user_id=3030, text="✂️ Записаться")

    async def fake_company_context():
        return "12345", None

    async def fake_load_services(_company_id: str):
        return []

    async def fake_contacts(_company_id: str):
        resolved = type("R", (), {"address": "Тест", "phone": "+70000000000", "schedule": "10-22"})()
        return type("C", (), {"resolved": resolved})()

    monkeypatch.setattr("app.handlers.booking_flow._get_company_context", fake_company_context)
    monkeypatch.setattr("app.handlers.booking_flow._load_services", fake_load_services)
    monkeypatch.setattr("app.handlers.booking_flow.resolve_contacts_for_company", fake_contacts)
    run(booking_flow_start(booking_msg, fsm_context))
    assert any("запис" in row["text"].lower() for row in booking_msg.answers)

    loyalty_msg = FakeMessage(user_id=3030, text=LOYALTY_BTN)

    async def fake_sync_ref(**_kwargs):
        return "ok"

    monkeypatch.setattr("app.handlers.loyalty_mvp.sync_referral_reward_if_eligible", fake_sync_ref)
    run(open_loyalty_menu(loyalty_msg, fsm_context))
    assert any("Система лояльности" in row["text"] for row in loyalty_msg.answers)


def test_protected_developer_remains_intact(initialized_db):
    run(ensure_protected_developer())
    row = run(fetchone("SELECT role FROM users WHERE user_id = ?", (DEVELOPER_TG_ID,)))
    assert row is not None
    assert row["role"] == "developer"
