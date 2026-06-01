from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.sqlite import fetchone
from app.repositories.loyalty_operations import create_operation, get_user_operations
from app.repositories.referrals import create_referral_attribution, save_referral_code
from app.services import loyalty_mvp
from tests.factories import create_user
from tests.sync import run


pytestmark = pytest.mark.integration


class FakeYClientsClient:
    async def close(self) -> None:
        return None


def test_referral_reward_granted_once_after_first_paid_visit(initialized_db, monkeypatch: pytest.MonkeyPatch):
    inviter_id = run(create_user(tg_id=9001, yclients_client_id=1001))
    invited_id = run(create_user(tg_id=9002, yclients_client_id=1002))

    run(save_referral_code(inviter_id, "INV123"))
    run(create_referral_attribution(invited_tg_id=invited_id, inviter_tg_id=inviter_id, referral_code="INV123"))

    async def fake_build_client():
        return FakeYClientsClient(), "12345"

    async def fake_visits(_client, **_kwargs):
        return {"data": [{"id": "visit-1", "status": "done", "paid": True, "datetime": "2026-05-01T10:00:00+00:00"}]}

    async def fake_tz():
        return "Europe/Moscow"

    monkeypatch.setattr("app.services.loyalty_mvp.build_yclients_client", fake_build_client)
    monkeypatch.setattr("app.services.loyalty_mvp.list_client_visits", fake_visits)
    monkeypatch.setattr("app.services.loyalty_mvp.resolve_branch_timezone", fake_tz)
    monkeypatch.setattr("app.services.loyalty_mvp.get_settings", lambda: type("S", (), {"yclients_company_id": "12345"})())

    first = run(loyalty_mvp.sync_referral_reward_if_eligible(invited_tg_id=invited_id))
    second = run(loyalty_mvp.sync_referral_reward_if_eligible(invited_tg_id=invited_id))

    assert first in {"rewarded", "rewarded_many"}
    assert second in {"rewarded", "already_rewarded"}

    row = run(fetchone("SELECT COUNT(1) AS c FROM loyalty_operations WHERE source = 'referral' AND user_tg_id = ?", (inviter_id,)))
    assert int(row["c"]) == 1


def test_points_history_sorted_newest_first(initialized_db):
    user_id = run(create_user(tg_id=9100, yclients_client_id=2001))
    now = datetime.now(timezone.utc)

    run(create_operation(user_tg_id=user_id, operation_type="manual_adjustment", points_delta=10, reason="first", source="manual", source_event_id=f"evt-{now.isoformat()}"))
    run(create_operation(user_tg_id=user_id, operation_type="manual_adjustment", points_delta=20, reason="second", source="manual", source_event_id=f"evt-{(now + timedelta(minutes=1)).isoformat()}"))

    rows = run(get_user_operations(user_id, limit=10, offset=0))
    assert rows[0]["points_delta"] == 20
    assert rows[1]["points_delta"] == 10
