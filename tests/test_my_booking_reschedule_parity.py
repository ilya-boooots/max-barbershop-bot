from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from max_barbershop_bot.integrations.yclients.dto import YClientsBookingRecord, YClientsCancelBookingResult
from max_barbershop_bot.integrations.yclients.exceptions import YClientsRateLimitError, YClientsValidationError
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.services import my_bookings as svc
from max_barbershop_bot.ui.buttons import (
    MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD,
    MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX,
    MY_BOOKINGS_RESCHEDULE_START_PAYLOAD,
    my_booking_details_keyboard,
)


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def user() -> User:
    return User(
        id=1,
        platform="max",
        platform_user_id="u1",
        max_user_id="u1",
        chat_id="c1",
        display_name="Иван",
        first_name="Иван",
        last_name=None,
        username=None,
        phone="+79991234567",
        birthdate=None,
        role="user",
        yclients_client_id="client-1",
        notifications_enabled=True,
    )


def future_row(status: str = "active", minutes: int = 120) -> dict[str, object]:
    dt = datetime.now(ZoneInfo("Europe/Samara")) + timedelta(minutes=minutes)
    return {
        "id": "old-1",
        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "services": [{"id": "svc-1", "title": "Услуга", "seance_length": 3600}],
        "staff": {"id": "staff-1", "name": "Максим"},
        "client": {"id": "client-1", "name": "Иван", "phone": "+79991234567"},
        "seance_length": 3600,
    }


def test_plan_and_telegram_reference_prove_pr036_scope_and_semantics() -> None:
    plan = read("docs/max_telegram_parity_plan_v2.md")
    tg_my = read("telegram_reference/app/handlers/my_bookings.py")
    tg_ep = read("telegram_reference/app/integrations/yclients/endpoints.py")
    assert "PR-036 — My booking reschedule parity" in plan
    assert "repeat booking" in plan
    assert "CB_RESCHEDULE" in tg_my and "reschedule_via_rebook" in tg_my
    assert tg_my.index("create_booking_or_visit(") < tg_my.index("cancel_booking(client, company_id=company_id, record_id=record_id)")
    assert "async def reschedule_booking" in tg_ep and "client.put" in tg_ep


def test_router_payloads_are_registered_and_not_placeholders() -> None:
    flow = read("max_barbershop_bot/flows/my_bookings.py")
    assert f"router.on_callback({MY_BOOKINGS_RESCHEDULE_START_PAYLOAD!r}" not in flow  # constants are used directly
    assert "router.on_callback(MY_BOOKINGS_RESCHEDULE_START_PAYLOAD, handle_my_booking_reschedule_start)" in flow
    assert "router.on_callback(MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD, handle_my_booking_reschedule_confirm)" in flow
    assert "MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX" in flow
    assert "MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX" in flow
    assert MY_BOOKINGS_RESCHEDULE_START_PAYLOAD.startswith("my_bookings:reschedule")
    assert MY_BOOKINGS_RESCHEDULE_CONFIRM_PAYLOAD.startswith("my_bookings:reschedule")
    assert MY_BOOKINGS_RESCHEDULE_DATE_PAYLOAD_PREFIX.endswith(":date:")
    assert MY_BOOKINGS_RESCHEDULE_SLOT_PAYLOAD_PREFIX.endswith(":slot:")
    assert "SECTION_SOON_TEXT" not in flow[flow.index("async def handle_my_booking_reschedule_start"):flow.index("async def handle_my_booking_reschedule_date")]


@pytest.mark.parametrize("status", ["active", "confirmed", "approve", "approved", "pending", "new", "", "booked", "created", "reserved", "mystery"])
def test_reschedule_button_visible_for_telegram_allowed_statuses(status: str) -> None:
    row = future_row(status=status)
    assert svc.is_booking_reschedulable(row, timezone_name="Europe/Samara") is True
    keyboard = my_booking_details_keyboard(is_active=True, can_reschedule=True)
    assert any("Перенести" in button.text for row_buttons in keyboard.rows for button in row_buttons)


@pytest.mark.parametrize("status", ["cancelled", "canceled", "done", "completed", "visit", "no_show"])
def test_reschedule_button_hidden_for_telegram_forbidden_statuses(status: str) -> None:
    row = future_row(status=status)
    assert svc.is_booking_reschedulable(row, timezone_name="Europe/Samara") is False
    keyboard = my_booking_details_keyboard(is_active=True, can_reschedule=False)
    assert not any("Перенести" in button.text for row_buttons in keyboard.rows for button in row_buttons)


def test_past_booking_is_not_reschedulable_but_five_minute_grace_matches_telegram() -> None:
    assert svc.is_booking_reschedulable(future_row(minutes=-10), timezone_name="Europe/Samara") is False
    assert svc.is_booking_reschedulable(future_row(minutes=-3), timezone_name="Europe/Samara") is True


def test_reschedule_confirmation_contains_service_master_and_old_new_order() -> None:
    text = svc.format_reschedule_confirmation_text(
        {
            "service_name": "Услуга",
            "staff_name": "Максим",
            "old_date": "20.07.2026",
            "old_time": "10:00",
            "new_date": "21.07.2026",
            "new_time": "11:30",
        }
    )
    assert text.splitlines() == [
        "Проверьте перенос записи 🔁",
        "",
        "✨ Услуга: Услуга",
        "👤 Мастер: Максим",
        "",
        "Было:",
        "🗓 20.07.2026",
        "🕒 10:00",
        "",
        "Станет:",
        "🗓 21.07.2026",
        "🕒 11:30",
    ]


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class FakeLayer:
    calls: list[tuple[str, dict[str, object]]] = []
    fail_cancel: Exception | None = None
    fail_create: Exception | None = None

    def __init__(self, client, company_id: str):
        self.company_id = company_id

    async def create_booking(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.fail_create:
            raise self.fail_create
        return YClientsBookingRecord(record_id="new-1", datetime=kwargs["datetime_iso"], raw_payload={"id": "new-1"})

    async def cancel_booking(self, **kwargs):
        self.calls.append(("cancel", kwargs))
        if self.fail_cancel:
            raise self.fail_cancel
        return YClientsCancelBookingResult(record_id=str(kwargs["yclients_record_id"]), status="cancelled", raw_payload={"status": "cancelled"})


@pytest.fixture(autouse=True)
def patch_reschedule_integration(monkeypatch, tmp_path):
    FakeLayer.calls = []
    FakeLayer.fail_cancel = None
    FakeLayer.fail_create = None
    monkeypatch.setattr(svc, "load_active_yclients_settings", lambda repo, operation: YClientsSettings(company_id="cid", partner_token="p", user_token="u", branch_timezone="Europe/Samara"))
    monkeypatch.setattr(svc, "has_required_yclients_credentials", lambda settings: True)
    monkeypatch.setattr(svc, "build_yclients_client_from_active_settings", lambda settings: FakeClient())
    monkeypatch.setattr(svc, "YClientsServiceLayer", FakeLayer)
    monkeypatch.setattr(svc, "_platform_user_attributions", lambda *args, **kwargs: [])
    yield


def test_create_then_cancel_semantics_and_payload_no_update_call(tmp_path) -> None:
    service = svc.MyBookingsService(type("Repo", (), {"database_path": str(tmp_path / "db.sqlite")})())
    import asyncio
    result = asyncio.run(service.reschedule_booking_for_user(
        user(),
        platform_user_id="u1",
        reschedule_context={
            "yclients_record_id": "old-1",
            "service_ids": ["svc-1"],
            "staff_id": "staff-1",
            "client_data": {"id": "client-1", "phone": "+79991234567", "name": "Иван"},
            "seance_length": 60,
        },
        new_datetime_iso="2026-07-21 11:30:00",
    ))
    assert result == {"old_record_id": "old-1", "new_record_id": "new-1", "new_datetime": "2026-07-21 11:30:00"}
    assert [name for name, _ in FakeLayer.calls] == ["create", "cancel"]
    create_payload = FakeLayer.calls[0][1]
    assert create_payload["service_id"] == "svc-1"
    assert create_payload["staff_id"] == "staff-1"
    assert create_payload["datetime_iso"] == "2026-07-21 11:30:00"
    assert create_payload["phone"] == "+79991234567"
    assert "update" not in [name for name, _ in FakeLayer.calls]


def test_partial_failure_preserves_no_second_creation(tmp_path) -> None:
    FakeLayer.fail_cancel = YClientsValidationError("bad", status_code=400)
    service = svc.MyBookingsService(type("Repo", (), {"database_path": str(tmp_path / "db.sqlite")})())
    with pytest.raises(svc.MyBookingRescheduleError) as exc:
        import asyncio
        asyncio.run(service.reschedule_booking_for_user(
            user(),
            platform_user_id="u1",
            reschedule_context={
                "yclients_record_id": "old-1",
                "service_ids": ["svc-1"],
                "staff_id": "staff-1",
                "client_data": {"id": "client-1", "phone": "+79991234567", "name": "Иван"},
                "seance_length": 60,
            },
            new_datetime_iso="2026-07-21 11:30:00",
        ))
    assert exc.value.user_message == svc.MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT
    assert [name for name, _ in FakeLayer.calls] == ["create", "cancel"]


def test_rate_limit_has_friendly_retry_text_before_creation(tmp_path) -> None:
    FakeLayer.fail_create = YClientsRateLimitError("too many", status_code=429)
    service = svc.MyBookingsService(type("Repo", (), {"database_path": str(tmp_path / "db.sqlite")})())
    with pytest.raises(svc.MyBookingRescheduleError) as exc:
        import asyncio
        asyncio.run(service.reschedule_booking_for_user(
            user(),
            platform_user_id="u1",
            reschedule_context={
                "yclients_record_id": "old-1",
                "service_ids": ["svc-1"],
                "staff_id": "staff-1",
                "client_data": {"id": "client-1", "phone": "+79991234567", "name": "Иван"},
                "seance_length": 60,
            },
            new_datetime_iso="2026-07-21 11:30:00",
        ))
    assert exc.value.user_message == svc.MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT
    assert [name for name, _ in FakeLayer.calls] == ["create"]


def test_scope_safety_no_aiogram_and_forbidden_files_untouched_by_pr() -> None:
    assert "from aiogram" not in read("max_barbershop_bot/flows/my_bookings.py")
    assert "import aiogram" not in read("max_barbershop_bot/flows/my_bookings.py")
    flow = read("max_barbershop_bot/flows/my_bookings.py")
    assert "handle_my_booking_repeat_start" in flow and "start_repeat_booking_with_prefill" in flow
    assert "handle_my_booking_cancel_start" in flow and "handle_my_booking_cancel_confirm" in flow
