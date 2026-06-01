from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.sqlite import fetchone
from app.repositories.notification_attributions import find_last_click, log_click, mark_attributed
from tests.sync import run

pytestmark = pytest.mark.unit


def test_find_last_click_with_only_telegram_id_does_not_mismatch_sql_bindings(initialized_db):
    run(log_click(funnel_type="birthday", client_tg_id=12345, yclients_client_id=None))

    click = run(
        find_last_click(
            client_tg_id=12345,
            yclients_client_id=None,
            booking_created_at_utc=datetime.now(timezone.utc).isoformat(),
            window_days=7,
        )
    )

    assert click is not None
    assert click["client_tg_id"] == 12345


def test_find_last_click_with_yclients_client_id_does_not_mismatch_sql_bindings(initialized_db):
    run(log_click(funnel_type="birthday", client_tg_id=None, yclients_client_id="yc-42"))

    click = run(
        find_last_click(
            client_tg_id=None,
            yclients_client_id="yc-42",
            booking_created_at_utc=datetime.now(timezone.utc).isoformat(),
            window_days=7,
        )
    )

    assert click is not None
    assert click["yclients_client_id"] == "yc-42"


def test_mark_attributed_updates_click_without_crashing_booking_context(initialized_db):
    run(log_click(funnel_type="birthday", client_tg_id=12345, yclients_client_id=None))
    click = run(
        find_last_click(
            client_tg_id=12345,
            yclients_client_id=None,
            booking_created_at_utc=datetime.now(timezone.utc).isoformat(),
            window_days=7,
        )
    )

    run(
        mark_attributed(
            attribution_id=int(click["id"]),
            booking_id="record-1",
            booking_created_at_utc=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            revenue=None,
        )
    )

    row = run(fetchone("SELECT status, yclients_booking_id FROM notification_attributions WHERE id=?", (click["id"],)))
    assert row["status"] == "attributed_booking"
    assert row["yclients_booking_id"] == "record-1"
