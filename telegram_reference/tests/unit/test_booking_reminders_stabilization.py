from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.sqlite import fetchall
from app.repositories.booking_reminder_events import create_event, get_event
from app.services import booking_reminders as br
from app.handlers import booking_reminders as br_handlers
from tests.sync import run


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *, chat_id: int, **kwargs):
        self.sent.append((chat_id, kwargs))
        return SimpleNamespace(message_id=len(self.sent))


class FakeClient:
    async def close(self):
        return None


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_48h_template_buttons_and_no_old_reply_instruction():
    event_id = 123456
    text = br._build_48h_text(
        client_name="Илья",
        master_name="Рената",
        service_name="Стрижка",
        visit_date="01.06.2026",
        visit_time="21:00",
        date_label="послезавтра",
    )
    callbacks = _callbacks(br._confirm_kb(event_id))

    assert "Илья" in text
    assert "Рената" in text
    assert "Стрижка" in text
    assert "01.06.2026" in text
    assert "21:00" in text
    assert "пришлите" not in text.lower()
    assert "Да/Нет" not in text
    assert callbacks == [f"brc:y:{event_id}", f"brc:n:{event_id}"]
    for callback_data in callbacks:
        assert len(callback_data.encode("utf-8")) <= 64


def test_2h_template_buttons_required_fields_and_no_promo_words():
    text = br._build_2h_text(
        client_name="Илья",
        service_name="Стрижка",
        visit_date="01.06.2026",
        visit_time="21:00",
        master_name="Рената",
        branch_address="Саратов, улица Колотушкина 1",
    )
    callbacks = _callbacks(br._reminder_2h_kb())

    assert all(x in text for x in ["Илья", "Стрижка", "01.06.2026", "21:00", "Ваш мастер:", "Рената", "Саратов"])
    for forbidden in ["Ваш барбер", "Специально для вас", "скидка", "подарочный сертификат"]:
        assert forbidden not in text
    assert callbacks == ["my_bookings:open", "nav:home"]
    for callback_data in callbacks:
        assert len(callback_data.encode("utf-8")) <= 64


def test_branch_timezone_formats_naive_yclients_datetime_as_branch_time():
    dt_local, visit_date, visit_time = br._format_visit_datetime_for_branch("2026-06-01 21:00:00", "Europe/Saratov")

    assert dt_local.tzinfo is not None
    assert visit_date == "01.06.2026"
    assert visit_time == "21:00"
    assert dt_local.astimezone(timezone.utc).hour == 17


def test_dev_test_reminder_ids_are_unique(initialized_db):
    now = datetime.now(timezone.utc).isoformat()
    first = run(create_event(
        yclients_record_id="dev-test-unique-1",
        yclients_client_id="378881880",
        client_tg_id=378881880,
        client_phone="+79990000000",
        company_id="dev_test",
        visit_datetime_utc=now,
        branch_timezone="Europe/Moscow",
        reminder_type="confirm_2d",
        status="pending",
        scheduled_at_utc=now,
    ))
    second = run(create_event(
        yclients_record_id="dev-test-unique-2",
        yclients_client_id="378881880",
        client_tg_id=378881880,
        client_phone="+79990000000",
        company_id="dev_test",
        visit_datetime_utc=now,
        branch_timezone="Europe/Moscow",
        reminder_type="confirm_2d",
        status="pending",
        scheduled_at_utc=now,
    ))

    assert first != second


def test_real_duplicate_protection_is_preserved(initialized_db):
    now = datetime.now(timezone.utc).isoformat()
    first = run(create_event(
        yclients_record_id="real-record-1",
        yclients_client_id="client-1",
        client_tg_id=1001,
        client_phone="+79990000001",
        company_id="12345",
        visit_datetime_utc=now,
        branch_timezone="Europe/Samara",
        reminder_type="reminder_2h",
        status="pending",
        scheduled_at_utc=now,
    ))
    second = run(create_event(
        yclients_record_id="real-record-1",
        yclients_client_id="client-1",
        client_tg_id=1001,
        client_phone="+79990000001",
        company_id="12345",
        visit_datetime_utc=now,
        branch_timezone="Europe/Samara",
        reminder_type="reminder_2h",
        status="pending",
        scheduled_at_utc=now,
    ))
    rows = run(fetchall("SELECT * FROM booking_reminder_events WHERE yclients_record_id=? AND reminder_type=?", ("real-record-1", "reminder_2h")))

    assert first == second
    assert len(rows) == 1


def test_dev_test_process_due_events_never_calls_yclients(monkeypatch: pytest.MonkeyPatch, initialized_db):
    now = datetime.now(timezone.utc)
    event_id = run(create_event(
        yclients_record_id="dev-test-no-yclients-1",
        yclients_client_id="378881880",
        client_tg_id=378881880,
        client_phone="+79990000000",
        company_id="dev_test",
        visit_datetime_utc=(now + timedelta(days=2)).isoformat(),
        branch_timezone="Europe/Moscow",
        reminder_type="confirm_2d",
        status="pending",
        scheduled_at_utc=now.isoformat(),
    ))

    async def fail_build_yclients_client():
        raise AssertionError("YClients must not be called for dev-test reminders")

    monkeypatch.setattr(br, "build_yclients_client", fail_build_yclients_client)
    bot = FakeBot()

    run(br.process_due_events(bot))
    event = run(get_event(event_id))

    assert event["status"] == "sent"
    assert event["sent_at_utc"]
    assert len(bot.sent) == 1
    assert "Илья, здравствуйте!" in bot.sent[0][1]["text"]


def test_yes_does_not_mark_confirmed_when_yclients_update_fails(monkeypatch: pytest.MonkeyPatch):
    marks = []
    answers = []
    diagnostics = []

    async def fake_get_event(event_id: int):
        return {"id": event_id, "yclients_record_id": "777", "company_id": "12345", "client_tg_id": 1001}

    async def fake_mark_status(*args, **kwargs):
        marks.append((args, kwargs))

    async def fake_build_yclients_client():
        return FakeClient(), "12345"

    async def fake_get_booking_details(*args, **kwargs):
        return {"data": {"id": 777, "staff_id": 5, "services": [{"id": 10}], "client": {"name": "Илья Иванов"}, "datetime": "2026-06-01 21:00:00", "seance_length": 3600}}

    async def fake_update_booking(*args, **kwargs):
        raise RuntimeError("YClients unavailable")

    async def fake_answer(text=None, **kwargs):
        answers.append(text)

    async def fake_send_message(chat_id, text, **kwargs):
        diagnostics.append((chat_id, text))

    monkeypatch.setattr(br_handlers, "get_event", fake_get_event)
    monkeypatch.setattr(br_handlers, "mark_status", fake_mark_status)
    monkeypatch.setattr(br_handlers, "build_yclients_client", fake_build_yclients_client)
    monkeypatch.setattr(br_handlers, "get_booking_details", fake_get_booking_details)
    monkeypatch.setattr(br_handlers, "update_booking", fake_update_booking)

    cb = SimpleNamespace(
        data="brc:y:42",
        from_user=SimpleNamespace(first_name="Тест"),
        message=SimpleNamespace(answer=fake_answer),
        bot=SimpleNamespace(send_message=fake_send_message),
        answer=lambda *args, **kwargs: fake_answer(None),
    )

    run(br_handlers.confirm_yes(cb))

    assert marks == []
    assert "⚠️ Не удалось подтвердить запись. Попробуйте позже." in answers
    assert diagnostics and diagnostics[0][0] == 378881880
    assert "payload_keys=" in diagnostics[0][1]
