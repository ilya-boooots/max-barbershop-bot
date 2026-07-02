from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.notifications import (
    BOOKING_REMINDER_48H,
    BOOKING_REMINDER_2H,
    get_notification_history,
    mark_notification_history_skipped,
    reserve_notification_history,
)
from max_barbershop_bot.services.reminders import (
    BookingNotificationContext,
    _calculate_telegram_confirmation_due,
    _keyboard_for_reminder,
    _record_is_active,
    build_reminder_schedule,
    render_booking_notification_text,
)


def test_booking_at_48h_is_due_candidate_in_branch_timezone() -> None:
    tz = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 7, 2, 12, 0, tzinfo=tz)
    booking = now + timedelta(hours=48)

    schedule = build_reminder_schedule(booking, "Asia/Yekaterinburg", now=now)

    assert schedule[BOOKING_REMINDER_48H] == now
    assert _calculate_telegram_confirmation_due(booking, now) == now


def test_48h_fallback_matches_telegram_6h_rule() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 2, 12, 0, tzinfo=tz)
    booking = now + timedelta(hours=24)

    assert build_reminder_schedule(booking, "Europe/Moscow", now=now)[BOOKING_REMINDER_48H] == booking - timedelta(hours=6)


def test_same_booking_processed_twice_has_one_active_history_row(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))

    first_id = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-04T12:00:00+05:00",
    )
    second_id = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-04T12:00:00+05:00",
    )

    assert first_id is not None
    assert second_id is None
    assert get_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_48H,
    ) is not None


def test_cancelled_and_deleted_records_are_not_active() -> None:
    assert not _record_is_active({"attendance": "-1"})
    assert not _record_is_active({"attendance": "1"})
    assert not _record_is_active({"deleted": "true"})
    assert not _record_is_active({"is_deleted": True})
    assert not _record_is_active({"status": "cancelled"})


def test_booking_moved_outside_48h_due_window_is_not_due() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 2, 12, 0, tzinfo=tz)
    booking = now + timedelta(days=4)
    scheduled_for = build_reminder_schedule(booking, "Europe/Moscow", now=now)[BOOKING_REMINDER_48H]

    assert not (scheduled_for <= now < booking)


def test_notifications_disabled_or_blocked_existing_history_prevents_automatic_duplicate(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))
    mark_notification_history_skipped(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r2",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for=None,
        reason="notifications_disabled",
    )

    assert reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r2",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-04T12:00:00+03:00",
    ) is None


def test_48h_text_and_buttons_match_telegram_reference_meaning() -> None:
    tz = ZoneInfo("Europe/Moscow")
    context = BookingNotificationContext(
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_48H,
        booking_datetime=datetime.now(tz) + timedelta(days=2),
        service_name="МУЖСКАЯ СТРИЖКА",
        master_name="Рената Пономарёва",
        client_name="Илья Иванов",
    )

    text = render_booking_notification_text(context, "Europe/Moscow")
    keyboard = _keyboard_for_reminder(context)

    assert 'Илья, здравствуйте! Рената Пономарёва ждёт вас' in text
    assert 'на услугу "МУЖСКАЯ СТРИЖКА"' in text
    assert "Подтвердите, пожалуйста, запись 👇" in text
    assert keyboard is not None
    assert keyboard.rows[0][0].text == "✅ Да, запись в силе"
    assert keyboard.rows[1][0].text == "❌ Нет, отменить или перенести"


import sqlite3

import max_barbershop_bot.flows.settings as settings_flow
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.ui.buttons import SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD, SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD


class FakeSender:
    def __init__(self) -> None:
        self.sent = []

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(("user", str(user_id), text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="msg-user", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(("chat", str(chat_id), text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="msg-chat", recipient_type="chat", recipient_id=str(chat_id))


def _test_context(sender: FakeSender) -> RouterContext:
    return RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id="100",
            max_user_id="100",
            chat_id="200",
            text=None,
            callback_payload=SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD,
            callback_id="cb",
        ),
        sender=sender,
    )


async def _run_settings_test(tmp_path, monkeypatch, payload: str):
    db = tmp_path / "db.sqlite3"
    init_database(str(db))
    monkeypatch.setattr(settings_flow, "_database_path", lambda: str(db))
    sender = FakeSender()
    text = await settings_flow._send_notification_test_and_build_result_text(_test_context(sender), payload)
    return db, sender, text


def _count_rows(db, table: str, notification_type: str) -> int:
    with sqlite3.connect(db) as connection:
        if table == "notification_history":
            row = connection.execute("SELECT COUNT(*) FROM notification_history WHERE notification_type = ?", (notification_type,)).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) FROM notification_delivery WHERE message_type = ?", (notification_type,)).fetchone()
    return int(row[0])


def test_settings_test_48h_sends_real_dev_message_and_logs_history(tmp_path, monkeypatch) -> None:
    import asyncio

    db, sender, text = asyncio.run(_run_settings_test(tmp_path, monkeypatch, SETTINGS_NOTIFICATIONS_TEST_48H_PAYLOAD))

    assert "✅ Тестовое уведомление отправлено." in text
    assert "reminder_type=confirm_2d" in text
    assert sender.sent and sender.sent[0][0] == "chat"
    assert "Подтвердите, пожалуйста, запись 👇" in sender.sent[0][2]
    assert sender.sent[0][3].rows[0][0].text == "✅ Да, запись в силе"
    assert _count_rows(db, "notification_history", BOOKING_REMINDER_48H) == 1
    assert _count_rows(db, "notification_delivery", BOOKING_REMINDER_48H) == 1


def test_settings_test_2h_sends_real_dev_message_and_logs_history(tmp_path, monkeypatch) -> None:
    import asyncio

    db, sender, text = asyncio.run(_run_settings_test(tmp_path, monkeypatch, SETTINGS_NOTIFICATIONS_TEST_2H_PAYLOAD))

    assert "✅ Тестовое уведомление отправлено." in text
    assert "reminder_type=reminder_2h" in text
    assert sender.sent and sender.sent[0][0] == "chat"
    assert "вы записаны на услугу «МУЖСКАЯ СТРИЖКА»" in sender.sent[0][2]
    assert sender.sent[0][3].rows[0][0].text == "📅 Мои записи"
    assert _count_rows(db, "notification_history", BOOKING_REMINDER_2H) == 1
    assert _count_rows(db, "notification_delivery", BOOKING_REMINDER_2H) == 1
