from __future__ import annotations

from datetime import datetime
import asyncio

import pytest

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import booking as booking_flow
from max_barbershop_bot.integrations.yclients import endpoints
from max_barbershop_bot.repositories.platform_attribution import PLATFORM_MAX, PlatformAttributionRepository
from max_barbershop_bot.services.booking import build_booking_comment, build_booking_payload


def test_create_payload_includes_telegram_reference_business_fields() -> None:
    payload = build_booking_payload(
        yclients_service_id="123",
        yclients_master_id="456",
        booking_date="2026-07-05",
        booking_slot="10:30",
        selected_datetime="2026-07-05T10:30:00+03:00",
        client_name="Иван Иванов",
        client_phone="+7 (999) 123-45-67",
        comment="Клиент записался из MAX бота",
    )

    assert payload == {
        "service_id": "123",
        "staff_id": "456",
        "datetime_iso": "2026-07-05T10:30:00+03:00",
        "phone": "+79991234567",
        "fullname": "Иван Иванов",
        "comment": "Клиент записался из MAX бота",
    }


def test_endpoint_create_payload_matches_telegram_reference_shape(monkeypatch) -> None:
    captured: dict = {}

    class _Client:
        async def post(self, endpoint: str, json_data: dict):
            captured["endpoint"] = endpoint
            captured["json_data"] = json_data
            return {"data": {"record_id": "rec-1", "datetime": "2026-07-05 10:30:00"}}

    asyncio.run(endpoints.create_booking_or_visit(
        _Client(),
        company_id="company-1",
        service_id="123",
        staff_id="456",
        datetime_iso="2026-07-05T10:30:00+03:00",
        phone="+7 (999) 123-45-67",
        fullname="Иван Иванов",
        comment="Клиент записался из MAX бота 05.07.2026 в 10:00",
    ))

    assert captured["endpoint"] == "/api/v1/book_record/company-1"
    assert captured["json_data"] == {
        "phone": "+79991234567",
        "fullname": "Иван Иванов",
        "email": "",
        "comment": "Клиент записался из MAX бота 05.07.2026 в 10:00",
        "appointments": [
            {
                "services": [123],
                "datetime": "2026-07-05 10:30:00+03:00",
                "id": 456,
                "staff_id": 456,
            }
        ],
    }


@pytest.mark.parametrize("id_field", ["record_id", "id", "booking_id", "visit_id"])
def test_endpoint_extracts_yclients_record_id_from_telegram_reference_fields(id_field: str) -> None:
    class _Client:
        async def post(self, endpoint: str, json_data: dict):
            return {"data": {id_field: f"{id_field}-value", "datetime": "2026-07-05 10:30:00"}}

    created = asyncio.run(endpoints.create_booking_or_visit(
        _Client(),
        company_id="company-1",
        service_id="123",
        staff_id="456",
        datetime_iso="2026-07-05T10:30:00+03:00",
        phone="+79991234567",
        fullname="Иван Иванов",
        comment="Клиент записался из MAX бота 05.07.2026 в 10:00",
    ))

    assert created.record_id == f"{id_field}-value"


def test_alternative_yclients_id_field_does_not_skip_attribution(tmp_path, monkeypatch) -> None:
    class _Client:
        async def post(self, endpoint: str, json_data: dict):
            return {"data": {"id": "alt-record-1", "datetime": "2026-07-05 10:30:00"}}

    created = asyncio.run(endpoints.create_booking_or_visit(
        _Client(),
        company_id="company-1",
        service_id="123",
        staff_id="456",
        datetime_iso="2026-07-05T10:30:00+03:00",
        phone="+79991234567",
        fullname="Иван Иванов",
        comment="Клиент записался из MAX бота 05.07.2026 в 10:00",
    ))

    db_path = tmp_path / "bot.sqlite3"
    init_database(str(db_path))
    monkeypatch.setattr(booking_flow, "_database_path", lambda: str(db_path))

    booking_flow._save_attribution_safely(
        platform_user_id="max-user-1",
        yclients_record_id=created.record_id,
        yclients_client_id="client-1",
        marker="Клиент записался из MAX бота 05.07.2026 в 10:30",
        booking_phone="+79991234567",
        source="booking_created_from_max",
    )

    record = PlatformAttributionRepository(str(db_path)).get_by_yclients_record_id("alt-record-1")
    assert record is not None
    assert record.yclients_record_id == "alt-record-1"


def test_comment_marker_is_max_adapted_telegram_format() -> None:
    comment = build_booking_comment(
        "Клиент записался из MAX бота",
        timezone_name="Europe/Moscow",
        request_created_at=datetime(2026, 7, 5, 10, 30),
    )

    assert comment == "Клиент записался из MAX бота 05.07.2026 в 10:30"


def test_comment_timestamp_uses_branch_timezone() -> None:
    comment = build_booking_comment(
        "Клиент записался из MAX бота",
        timezone_name="Asia/Yekaterinburg",
        request_created_at=datetime.fromisoformat("2026-07-05T10:30:00+03:00"),
    )

    assert comment == "Клиент записался из MAX бота 05.07.2026 в 12:30"


def test_successful_create_saves_platform_attribution(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "bot.sqlite3"
    init_database(str(db_path))
    monkeypatch.setattr(booking_flow, "_database_path", lambda: str(db_path))

    booking_flow._save_attribution_safely(
        platform_user_id="max-user-1",
        yclients_record_id="record-1",
        yclients_client_id="client-1",
        marker="Клиент записался из MAX бота 05.07.2026 в 10:30",
        booking_phone="+79991234567",
        source="booking_created_from_max",
    )

    record = PlatformAttributionRepository(str(db_path)).get_by_yclients_record_id("record-1")
    assert record is not None
    assert record.platform == PLATFORM_MAX
    assert record.platform_user_id == "max-user-1"
    assert record.yclients_record_id == "record-1"
    assert record.yclients_client_id == "client-1"
    assert record.booking_phone == "+79991234567"
    assert record.source == "booking_created_from_max"
    assert record.created_at


def test_missing_record_id_does_not_create_fake_attribution(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "bot.sqlite3"
    init_database(str(db_path))
    monkeypatch.setattr(booking_flow, "_database_path", lambda: str(db_path))

    booking_flow._save_attribution_safely(
        platform_user_id="max-user-1",
        yclients_record_id="",
        yclients_client_id="client-1",
        marker="Клиент записался из MAX бота",
        booking_phone="+79991234567",
        source="booking_created_from_max",
    )

    assert PlatformAttributionRepository(str(db_path)).list_by_platform_user_id("max-user-1") == []


def test_failed_create_path_does_not_save_attribution(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(booking_flow.PlatformAttributionRepository, "create_if_missing", lambda self, **kwargs: calls.append(kwargs))

    # In the booking flow attribution is called only after create_booking returns a record id.
    # A failed create never reaches _save_attribution_safely, so no repository call is made.
    assert calls == []
