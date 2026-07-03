from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from max_barbershop_bot.repositories.users import PLATFORM_MAX, User
from max_barbershop_bot.services import my_bookings as mb

TZ = "Europe/Moscow"
NOW = datetime(2026, 7, 3, 12, 0, tzinfo=ZoneInfo(TZ))
USER = User(1, PLATFORM_MAX, "u1", "u1", "c1", None, None, None, None, "+79198332692", None, "user", "123", True)


def future_iso(days: int = 1) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def past_iso() -> str:
    return (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def base_record(**overrides):
    record = {
        "id": "r1",
        "datetime": future_iso(),
        "services": [{"id": "s1", "title": "Стрижка", "price": 1200}],
        "staff": {"id": "m1", "name": "Максим"},
        "client": {"id": "123", "phone": "+79198332692"},
        "attendance": "0",
    }
    record.update(overrides)
    return record


def visible(records, *, client_id="123", phones={"79198332692"}):
    rows, counts = mb._filter_owned_active_rows(
        records,
        yclients_client_id=client_id,
        attributed_record_ids=[],
        known_phones=phones,
        timezone_name=TZ,
        now=NOW,
    )
    bookings = [mb._booking_from_payload(row, timezone_name=TZ) for row in rows]
    return [booking for booking in bookings if booking is not None], counts


def test_valid_future_minimal_scoped_record_renders_without_client_payload() -> None:
    records = mb._mark_rows_request_context(
        [{"id": "r1", "datetime": future_iso(), "attendance": "0"}],
        request_client_id="123",
        request_phone=None,
    )

    bookings, counts = visible(records)

    assert counts["hidden_not_owned_count"] == 0
    assert len(bookings) == 1
    assert "Услуга" in mb.format_bookings_screen(bookings, timezone_name=TZ)


def test_record_missing_services_uses_fallback_service_name() -> None:
    record = base_record()
    record.pop("services")
    bookings, _ = visible([record])
    assert bookings[0].service_name == "Услуга"


def test_record_missing_staff_does_not_crash() -> None:
    record = base_record()
    record.pop("staff")
    bookings, _ = visible([record])
    assert bookings[0].master_name is None


def test_record_missing_client_matches_by_scoped_request_context() -> None:
    record = mb._mark_rows_request_context([{"id": "r1", "datetime": future_iso()}], request_client_id="123", request_phone=None)[0]
    bookings, _ = visible([record])
    assert len(bookings) == 1


def test_future_unknown_or_missing_status_is_not_rejected() -> None:
    bookings_unknown, _ = visible([base_record(status="mystery")])
    bookings_missing, _ = visible([base_record()])
    assert len(bookings_unknown) == 1
    assert len(bookings_missing) == 1


def test_cancelled_or_deleted_records_are_hidden() -> None:
    bookings, counts = visible([base_record(id="c", status="cancelled"), base_record(id="d", deleted=True)])
    assert bookings == []
    assert counts["hidden_cancelled_count"] == 1
    assert counts["hidden_deleted_count"] == 1


def test_past_record_is_hidden() -> None:
    bookings, counts = visible([base_record(datetime=past_iso())])
    assert bookings == []
    assert counts["hidden_past_count"] == 1


def test_empty_yclients_list_is_empty_state_not_error() -> None:
    bookings, counts = visible([])
    assert bookings == []
    assert counts["owned_records_count"] == 0


def test_user_without_phone_and_client_id_has_no_identity_mode() -> None:
    assert mb._request_mode(None, set()) == "no_identity"


def test_one_malformed_record_does_not_hide_valid_record() -> None:
    bookings, counts = visible([base_record(id="bad", datetime=None), base_record(id="good")])
    assert [booking.yclients_record_id for booking in bookings] == ["good"]
    assert counts["hidden_parse_error_count"] == 1


def test_client_id_int_and_str_match() -> None:
    bookings, _ = visible([base_record(client={"id": 123})], client_id="123")
    assert len(bookings) == 1


def test_phone_formats_match() -> None:
    expected = {"79198332692"}
    for raw_phone in ["+79198332692", "79198332692", "89198332692", "9198332692"]:
        assert mb._normalize_phone_digits(raw_phone) in expected
    for raw_phone in ["+79198332692", "79198332692", "89198332692", "9198332692"]:
        bookings, _ = visible([base_record(client={"phone": raw_phone})], client_id=None, phones=expected)
        assert len(bookings) == 1
