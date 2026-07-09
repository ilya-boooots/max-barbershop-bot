from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.birthday_funnel_events import BirthdayFunnelEventsRepository
from max_barbershop_bot.repositories.users import UserCreate, UsersRepository
from max_barbershop_bot.services import birthday_funnel as bf


class DummySender:
    def __init__(self, status="sent", blocked=False, stopped=False):
        self.status = status
        self.blocked = blocked
        self.stopped = stopped
        self.sent = []

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        from max_barbershop_bot.max_api.sender import MaxSendResult
        self.sent.append(("user", user_id, text, keyboard))
        if self.blocked:
            return MaxSendResult(ok=False, status_code=403, message_id=None, recipient_type="user", recipient_id=str(user_id), error_code="blocked", is_blocked=True)
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        from max_barbershop_bot.max_api.sender import MaxSendResult
        self.sent.append(("chat", chat_id, text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="chat", recipient_id=str(chat_id))


def test_birthday_text_warning_and_keyboard_match_telegram():
    assert bf.BIRTHDAY_MESSAGE_TEXT == (
        "Скоро ваш день рождения, поздравляем 🎉 😊\n\n"
        "Хотим сделать вам приятный подарок - покажите это сообщение администратору при оплате."
    )
    assert bf.BIRTHDAY_WARNING == "У КЛИЕНТА ДЕНЬ РОЖДЕНИЕ - НУЖНО СДЕЛАТЬ СКИДКУ"
    keyboard = bf.build_birthday_booking_keyboard(42)
    assert len(keyboard.rows) == 1
    assert len(keyboard.rows[0]) == 1
    assert keyboard.rows[0][0].text == "✂️ Записаться"
    assert keyboard.rows[0][0].payload == "birthday_funnel:book:42"


def test_apply_birthday_warning_appends_once_and_only_for_birthday():
    base = "Клиент записался из MAX бота"
    first = bf.apply_birthday_warning(base, booking_source="birthday_funnel", birthday_discount_context=True)
    assert first == f"{base}\n\n{bf.BIRTHDAY_WARNING}"
    assert bf.apply_birthday_warning(first, booking_source="birthday_funnel", birthday_discount_context=True) == first
    assert bf.apply_birthday_warning(base, booking_source=None, birthday_discount_context=False) == base
    assert bf.apply_birthday_warning(base, booking_source="lost_client", birthday_discount_context=True) == base


def test_repository_telegram_equivalent_fields_and_statuses(tmp_path):
    db = tmp_path / "bot.sqlite3"
    init_database(str(db))
    repo = BirthdayFunnelEventsRepository(str(db))
    event = repo.create_event(
        yclients_client_id="yc1",
        client_tg_id="max1",
        birth_date="1990-05-20",
        birthday_year=2026,
        scheduled_send_at_utc="2026-05-13T09:00:00+00:00",
        status="pending",
        branch_timezone="Europe/Moscow",
        source="dev_test",
        is_test=True,
    )
    assert event is not None
    assert event.yclients_client_id == "yc1"
    assert event.client_tg_id == "max1"
    assert event.birth_date == "1990-05-20"
    assert event.birthday_year == 2026
    assert event.scheduled_send_at_utc == "2026-05-13T09:00:00+00:00"
    assert event.status == "pending"
    assert event.source == "dev_test"
    assert event.is_test is True
    assert repo.find_by_client_year("max1", 2026, is_test=True).id == event.id
    assert repo.find_by_client_year("max1", 2026, is_test=False) is None
    assert repo.mark_status(event.id, "sent", sent=True).sent_at_utc is not None
    assert repo.mark_status(event.id, "clicked_booking", clicked=True).clicked_at_utc is not None
    assert repo.mark_status(event.id, "failed", error_summary="bad").error_summary == "bad"
    assert repo.mark_status(event.id, "blocked").status == "blocked"


def test_scan_disabled_skip_and_due_dedup_send(tmp_path, monkeypatch):
    db = tmp_path / "bot.sqlite3"
    init_database(str(db))
    users = UsersRepository(str(db))
    due = date.today() + timedelta(days=7)
    users.create(UserCreate(platform_user_id="u1", max_user_id="101", birthdate=f"1990-{due.month:02d}-{due.day:02d}", yclients_client_id="yc1"))
    users.create(UserCreate(platform_user_id="u2", max_user_id="102", birthdate="1990-01-01"))
    users.create(UserCreate(platform_user_id="u3", max_user_id="103", birthdate=f"1990-{due.month:02d}-{due.day:02d}", notifications_enabled=False))

    AppSettingsRepository(str(db)).set_bool("birthday", False)
    summary = asyncio.run(bf.run_birthday_scan(DummySender(), database_path=str(db)))
    assert summary == bf.BirthdayScanSummary()

    AppSettingsRepository(str(db)).set_bool("birthday", True)

    class FakeCompanyTime:
        def __init__(self, repo):
            pass
        def today(self):
            return date.today()
        def get_branch_timezone_name(self):
            return "Europe/Moscow"

    monkeypatch.setattr(bf, "CompanyTimeService", FakeCompanyTime)
    sender = DummySender()
    summary = asyncio.run(bf.run_birthday_scan(sender, database_path=str(db)))
    assert summary.sent == 1
    assert summary.skipped == 2
    assert sender.sent[0][2] == bf.BIRTHDAY_MESSAGE_TEXT
    assert sender.sent[0][3].rows[0][0].text == "✂️ Записаться"
    assert sender.sent[0][3].rows[0][0].payload.startswith("birthday_funnel:book:")
    repo = BirthdayFunnelEventsRepository(str(db))
    event = repo.find_by_client_year("u1", date.today().year)
    assert event is not None
    assert event.status == "sent"
    assert event.birth_date == f"1990-{due.month:02d}-{due.day:02d}"

    summary2 = asyncio.run(bf.run_birthday_scan(DummySender(), database_path=str(db)))
    assert summary2.sent == 0


def test_birth_date_parse_and_feb_29_safe():
    assert bf._parse_birth_date("1990-05-20T00:00:00") == date(1990, 5, 20)
    assert bf._parse_birth_date("bad") is None
    assert bf._is_due_today(date(1992, 2, 29), date(2026, 2, 21), days_before=7)
