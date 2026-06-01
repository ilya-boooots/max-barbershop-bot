from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.handlers.loyalty_mvp import _format_operation
from app.services import loyalty_mvp
from app.utils.datetime import format_branch_datetime
from tests.sync import run


pytestmark = pytest.mark.unit


def test_referral_code_stable_after_first_generation(initialized_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loyalty_mvp, "_generate_referral_code", lambda _user_id: "U0001-ABC123")
    from tests.factories import create_user
    run(create_user(tg_id=1))
    code1 = run(loyalty_mvp.ensure_referral_code(1))
    code2 = run(loyalty_mvp.ensure_referral_code(1))
    assert code1 == code2 == "U0001-ABC123"


def test_referral_start_param_parsing_and_self_referral_blocked(monkeypatch: pytest.MonkeyPatch):
    async def fake_lookup(code: str):
        return 10 if code == "AAAAAA" else None

    async def fake_create(**_kwargs):
        return True

    monkeypatch.setattr("app.services.loyalty_mvp.get_user_by_referral_code", fake_lookup)
    monkeypatch.setattr("app.services.loyalty_mvp.create_referral_attribution", fake_create)

    assert run(loyalty_mvp.apply_start_referral(invited_tg_id=11, start_param="ref_AAAAAA")) == "attributed"
    assert run(loyalty_mvp.apply_start_referral(invited_tg_id=10, start_param="ref_AAAAAA")) == "self_referral"


def test_branch_timezone_rendering(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve_company_timezone(_company_id: str):
        return type("Ctx", (), {"timezone_name": "Europe/Moscow"})()

    monkeypatch.setattr("app.utils.datetime.resolve_company_timezone", fake_resolve_company_timezone)
    async def fake_settings():
        return type("S", (), {"company_id": "123"})()

    monkeypatch.setattr("app.utils.datetime.get_yclients_settings", fake_settings)
    rendered = run(format_branch_datetime(datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)))
    assert rendered == "10.01.2026 в 15:00:00"


def test_loyalty_operation_format_uses_timezone(monkeypatch: pytest.MonkeyPatch):
    async def fake_fmt(_value):
        return "01.04.2026 22:15"

    monkeypatch.setattr("app.handlers.loyalty_mvp.format_branch_datetime", fake_fmt)
    text = run(_format_operation({"created_at_utc": "2026-04-01T19:15:00+00:00", "points_delta": 200, "operation_type": "referral_bonus_inviter", "source": "referral"}))
    assert "01.04.2026 22:15" in text
    assert "+200 баллов" in text
