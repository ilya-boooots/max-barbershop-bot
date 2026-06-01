from __future__ import annotations

import pytest

from app.handlers import booking_flow
from app.handlers.booking_flow import ServiceItem
from tests.sync import run


pytestmark = pytest.mark.unit


def test_selected_master_limits_services_and_hides_empty_categories():
    company_id = "123"
    services = [
        ServiceItem(id="s1", name="Стрижка", category_id="c1", category_name="Волосы", price=None, duration=None),
        ServiceItem(id="s2", name="Бритьё", category_id="c2", category_name="Борода", price=None, duration=None),
    ]
    booking_flow._SERVICE_RAW_CACHE[(company_id, "s1")] = {"id": "s1", "staff": [{"id": "m1"}]}
    booking_flow._SERVICE_RAW_CACHE[(company_id, "s2")] = {"id": "s2", "staff": [{"id": "m2"}]}

    async def fake_staff_map(_company_id: str):
        return {"m1": {"s1"}, "m2": {"s2"}}

    booking_flow._CACHE[(company_id, "staff_service_map")] = (10**12, {"m1": {"s1"}, "m2": {"s2"}})
    valid = run(booking_flow._get_valid_services_for_context(company_id, {"selected_staff_id": "m1"}, services))
    assert [s.id for s in valid] == ["s1"]


def test_selected_service_limits_specialists():
    raw_staff = [
        {"id": "m1", "name": "Алексей", "service_ids": ["s1"]},
        {"id": "m2", "name": "Борис", "service_ids": ["s2"]},
    ]
    filtered = booking_flow._normalize_staff(raw_staff, selected_service_id="s1")
    assert [s.id for s in filtered] == ["m1"]


def test_invalid_staff_service_combination_revalidated_with_map(monkeypatch: pytest.MonkeyPatch):
    company_id = "123"
    booking_flow._SERVICE_RAW_CACHE[(company_id, "s1")] = {"id": "s1"}

    async def fake_staff_map(_company_id: str):
        return {"m1": {"s1"}, "m2": {"s2"}}

    monkeypatch.setattr("app.handlers.booking_flow._load_staff_service_map", fake_staff_map)

    assert run(booking_flow._is_service_compatible_with_staff(company_id, "s1", "m1")) is True
    assert run(booking_flow._is_service_compatible_with_staff(company_id, "s1", "m2")) is False


def test_datetime_first_flow_state_drops_incompatible_staff():
    service_payload = {"id": "s100", "staff": [{"id": "m77"}]}
    assigned = booking_flow._extract_assigned_staff_ids_from_service(service_payload)
    assert assigned == {"m77"}


def test_datetime_first_context_filters_services_by_real_slot(monkeypatch: pytest.MonkeyPatch):
    company_id = "123"
    services = [
        ServiceItem(id="s1", name="Стрижка", category_id="c1", category_name="Волосы", price=None, duration=None),
        ServiceItem(id="s2", name="Бритьё", category_id="c2", category_name="Борода", price=None, duration=None),
    ]

    async def fake_load_slots(_company_id: str, *, service_id: str, staff_id: str | None, iso_date: str):
        assert _company_id == company_id
        assert iso_date == "2026-05-01"
        assert staff_id is None
        if service_id == "s1":
            return [booking_flow.SlotItem(time="10:00", datetime_iso="2026-05-01T10:00:00+04:00")]
        return [booking_flow.SlotItem(time="11:00", datetime_iso="2026-05-01T11:00:00+04:00")]

    monkeypatch.setattr("app.handlers.booking_flow._load_slots", fake_load_slots)
    valid = run(
        booking_flow._get_valid_services_for_context(
            company_id,
            {"entry_mode": "datetime_first", "selected_date": "2026-05-01", "selected_time": "10:00"},
            services,
        )
    )
    assert [service.id for service in valid] == ["s1"]


def test_service_payload_staffs_and_ids_are_normalized():
    service_payload = {
        "id": 26921736,
        "staffs": [
            {"id": 502407, "name": "Алексей"},
            {"staff_id": "502308", "name": "Борис"},
            {"staff": {"id": "502999.0", "name": "Виктор"}},
        ],
    }

    assigned = booking_flow._extract_assigned_staff_ids_from_service(service_payload)

    assert assigned == {"502407", "502308", "502999"}


def test_service_payload_staffs_resolve_even_when_endpoint_lacks_service_ids(monkeypatch: pytest.MonkeyPatch):
    service_payload = {
        "id": "26921736",
        "title": "Стрижка + борода",
        "staffs": [
            {"id": 502407, "name": "Алексей"},
            {"id": "502308", "name": "Борис"},
        ],
    }

    async def fake_get_staff_for_service(_company_id: str, _service_id: str):
        return [
            {"id": "502407", "name": "Алексей Endpoint"},
            {"id": 502308, "name": "Борис Endpoint"},
        ]

    monkeypatch.setattr("app.handlers.booking_flow.get_staff_for_service", fake_get_staff_for_service)

    resolution = run(booking_flow._resolve_assigned_staff("123", service_payload))

    assert [item.id for item in resolution.staff_list] == ["502407", "502308"]
    assert {item.name for item in resolution.staff_list} == {"Алексей Endpoint", "Борис Endpoint"}
    assert resolution.service_payload_count == 2
