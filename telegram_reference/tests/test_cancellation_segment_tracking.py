from __future__ import annotations

from app.db.sqlite import fetchone
from app.repositories.automation_settings import upsert_setting
from app.services.cancellation_recovery import create_cancellation_event_from_row
from tests.sync import run


def test_my_bookings_cancel_creates_segment_event_when_automation_disabled(initialized_db) -> None:
    run(upsert_setting("cancellation_return", {"enabled": False, "delay_hours": 2}, updated_by_tg_id=1))

    event_id = run(
        create_cancellation_event_from_row(
            row={
                "id": "record-1",
                "client": {"id": "yclients-client-7"},
                "datetime": "2026-05-21T10:00:00+00:00",
            },
            source="my_bookings_cancel",
            force_tg_id=777001,
        )
    )

    assert event_id is not None
    row = run(fetchone("SELECT client_tg_id, status, source FROM cancellation_recovery_events WHERE id=?", (event_id,)))
    assert row is not None
    assert int(row["client_tg_id"]) == 777001
    assert row["status"] == "pending"
    assert row["source"] == "my_bookings_cancel"


def test_non_booking_sources_still_skip_when_automation_disabled(initialized_db) -> None:
    run(upsert_setting("cancellation_return", {"enabled": False, "delay_hours": 2}, updated_by_tg_id=1))

    event_id = run(
        create_cancellation_event_from_row(
            row={
                "id": "record-2",
                "client": {"id": "yclients-client-9"},
                "datetime": "2026-05-21T11:00:00+00:00",
            },
            source="bot_cancel",
            force_tg_id=777002,
        )
    )

    assert event_id is None
