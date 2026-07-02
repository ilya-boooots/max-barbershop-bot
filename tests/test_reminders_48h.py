from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.notifications import (
    BOOKING_REMINDER_48H,
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
