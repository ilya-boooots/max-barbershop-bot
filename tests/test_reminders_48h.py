from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.users import PLATFORM_MAX
from max_barbershop_bot.services.notifications import (
    BOOKING_REMINDER_48H,
    BOOKING_REMINDER_2H,
    get_notification_history,
    mark_notification_history_failed,
    mark_notification_history_sent,
    mark_notification_history_skipped,
    reserve_notification_history,
    NotificationDeliveryResult,
    save_delivery_result,
)
from max_barbershop_bot.services.reminders import (
    BookingNotificationContext,
    _calculate_telegram_2h_due,
    _calculate_telegram_confirmation_due,
    _keyboard_for_reminder,
    _record_is_active,
    build_reminder_schedule,
    render_booking_notification_text,
    send_booking_notification,
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


def test_booking_at_2h_is_due_candidate_in_branch_timezone() -> None:
    tz = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 7, 2, 12, 0, tzinfo=tz)
    booking = now + timedelta(hours=2)

    schedule = build_reminder_schedule(booking, "Asia/Yekaterinburg", now=now)

    assert schedule[BOOKING_REMINDER_2H] == now
    assert _calculate_telegram_2h_due(booking, now) == now


def test_booking_more_than_2h_away_is_not_2h_due_yet() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 3, 14, 15, tzinfo=tz)
    booking = datetime(2026, 7, 3, 16, 30, tzinfo=tz)

    scheduled_for = build_reminder_schedule(booking, "Europe/Moscow", now=now)[BOOKING_REMINDER_2H]

    assert scheduled_for == datetime(2026, 7, 3, 14, 30, tzinfo=tz)
    assert not (scheduled_for <= now < booking)


def test_booking_already_received_48h_can_still_receive_2h_once(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))

    first_48h = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-02T12:00:00+03:00",
    )
    first_2h = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_2H,
        scheduled_for="2026-07-04T10:00:00+03:00",
    )
    second_2h = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_2H,
        scheduled_for="2026-07-04T10:00:00+03:00",
    )

    assert first_48h is not None
    assert first_2h is not None
    assert second_2h is None


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


def test_2h_text_and_buttons_match_telegram_reference_meaning() -> None:
    tz = ZoneInfo("Europe/Moscow")
    context = BookingNotificationContext(
        platform_user_id="u1",
        yclients_record_id="r1",
        notification_type=BOOKING_REMINDER_2H,
        booking_datetime=datetime(2026, 7, 4, 21, 0, tzinfo=tz),
        service_name="МУЖСКАЯ СТРИЖКА",
        master_name="Рената Пономарёва",
        client_name="Илья Иванов",
        branch_address="г. Москва, ул. Тестовая, 1",
    )

    text = render_booking_notification_text(context, "Europe/Moscow")
    keyboard = _keyboard_for_reminder(context)

    assert "Илья, вы записаны на услугу «МУЖСКАЯ СТРИЖКА», ждём вас 04.07.2026 к 21:00." in text
    assert "Ваш мастер: Рената Пономарёва" in text
    assert "📍 Адрес: г. Москва, ул. Тестовая, 1" in text
    assert keyboard is not None
    assert keyboard.rows[0][0].text == "📅 Мои записи"
    assert keyboard.rows[1][0].text == "🏠 Главное меню"


def test_2h_empty_service_fallback_matches_telegram_template() -> None:
    tz = ZoneInfo("Europe/Moscow")
    context = BookingNotificationContext(
        platform_user_id="u1",
        yclients_record_id="r-empty-service",
        notification_type=BOOKING_REMINDER_2H,
        booking_datetime=datetime(2026, 7, 4, 21, 0, tzinfo=tz),
        service_name="",
        master_name="",
        client_name="",
    )

    text = render_booking_notification_text(context, "Europe/Moscow")

    assert "Здравствуйте, вы записаны на услугу «услугу», ждём вас 04.07.2026 к 21:00." in text
    assert "Ваш мастер: ваш мастер" in text


def test_2h_inside_due_window_is_selected_and_outside_is_not_selected() -> None:
    tz = ZoneInfo("Europe/Moscow")
    booking = datetime(2026, 7, 4, 21, 0, tzinfo=tz)
    inside_now = datetime(2026, 7, 4, 19, 5, tzinfo=tz)
    outside_now = datetime(2026, 7, 4, 18, 55, tzinfo=tz)

    inside_scheduled = build_reminder_schedule(booking, "Europe/Moscow", now=inside_now)[BOOKING_REMINDER_2H]
    outside_scheduled = build_reminder_schedule(booking, "Europe/Moscow", now=outside_now)[BOOKING_REMINDER_2H]

    assert inside_scheduled <= inside_now < booking
    assert not (outside_scheduled <= outside_now < booking)


def test_2h_past_appointment_is_not_scheduled() -> None:
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 4, 21, 1, tzinfo=tz)
    booking = datetime(2026, 7, 4, 21, 0, tzinfo=tz)

    assert BOOKING_REMINDER_2H not in build_reminder_schedule(booking, "Europe/Moscow", now=now)


def test_deleted_inactive_records_are_not_active_for_2h_skip_rules() -> None:
    assert not _record_is_active({"deleted": True})
    assert not _record_is_active({"is_deleted": "1"})
    assert not _record_is_active({"status": "deleted"})


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


class FailingSender(FakeSender):
    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(("chat", str(chat_id), text, keyboard))
        return MaxSendResult(
            ok=False,
            status_code=500,
            error_code="server_error",
            error_message="send failed",
            message_id=None,
            recipient_type="chat",
            recipient_id=str(chat_id),
        )


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


def test_2h_duplicate_disabled_success_and_failed_history_behaviour(tmp_path) -> None:
    import asyncio

    db = tmp_path / "db.sqlite3"
    init_database(str(db))
    tz = ZoneInfo("Europe/Moscow")
    context = BookingNotificationContext(
        platform_user_id="u1",
        max_user_id="u1",
        chat_id="c1",
        yclients_record_id="r-2h-history",
        notification_type=BOOKING_REMINDER_2H,
        booking_datetime=datetime(2026, 7, 4, 21, 0, tzinfo=tz),
        service_name="МУЖСКАЯ СТРИЖКА",
        master_name="Рената Пономарёва",
        client_name="Илья",
        scheduled_for=datetime(2026, 7, 4, 19, 0, tzinfo=tz),
    )

    first = asyncio.run(send_booking_notification(FakeSender(), database_path=str(db), context=context, timezone_name="Europe/Moscow"))
    duplicate_sender = FakeSender()
    duplicate = asyncio.run(send_booking_notification(duplicate_sender, database_path=str(db), context=context, timezone_name="Europe/Moscow"))

    assert first is not None and first.status == "sent"
    assert duplicate is not None and duplicate.id == first.id
    assert duplicate_sender.sent == []
    assert _count_rows(db, "notification_history", BOOKING_REMINDER_2H) == 1
    assert _count_rows(db, "notification_delivery", BOOKING_REMINDER_2H) == 1

    failed_context = BookingNotificationContext(
        platform_user_id="u2",
        max_user_id="u2",
        chat_id="c2",
        yclients_record_id="r-2h-failed",
        notification_type=BOOKING_REMINDER_2H,
        booking_datetime=datetime(2026, 7, 4, 21, 0, tzinfo=tz),
        service_name="МУЖСКАЯ СТРИЖКА",
        master_name="Рената Пономарёва",
        client_name="Илья",
        scheduled_for=datetime(2026, 7, 4, 19, 0, tzinfo=tz),
    )
    failed = asyncio.run(send_booking_notification(FailingSender(), database_path=str(db), context=failed_context, timezone_name="Europe/Moscow"))

    assert failed is not None and failed.status == "failed"

    mark_notification_history_skipped(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u3",
        yclients_record_id="r-2h-disabled",
        notification_type=BOOKING_REMINDER_2H,
        scheduled_for=None,
        reason="notifications_disabled",
    )
    assert reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u3",
        yclients_record_id="r-2h-disabled",
        notification_type=BOOKING_REMINDER_2H,
        scheduled_for="2026-07-04T19:00:00+03:00",
    ) is None


def test_pr001_reserve_creates_one_pending_row_and_duplicate_uses_record_type_key(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))

    first_id = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="record-dup",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-04T12:00:00+03:00",
    )
    duplicate_for_other_user = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u2",
        yclients_record_id="record-dup",
        notification_type=BOOKING_REMINDER_48H,
        scheduled_for="2026-07-04T12:00:00+03:00",
    )
    row = get_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="record-dup",
        notification_type=BOOKING_REMINDER_48H,
    )

    assert first_id is not None
    assert duplicate_for_other_user is None
    assert row is not None
    assert row.status == "pending"


def test_pr001_mark_sent_failed_skipped_blocked_and_rate_limited_statuses(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))

    sent_id = reserve_notification_history(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="sent", notification_type=BOOKING_REMINDER_48H)
    failed_id = reserve_notification_history(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="failed", notification_type=BOOKING_REMINDER_48H)
    blocked_id = reserve_notification_history(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="blocked", notification_type=BOOKING_REMINDER_48H)
    rate_id = reserve_notification_history(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="rate", notification_type=BOOKING_REMINDER_48H)

    sent = mark_notification_history_sent(str(db), history_id=sent_id, result=MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id="u1"))
    failed = mark_notification_history_failed(str(db), history_id=failed_id, result=MaxSendResult(ok=False, status_code=500, error_code="server_error", error_message="server failed", message_id=None, recipient_type="user", recipient_id="u1"))
    blocked = mark_notification_history_failed(str(db), history_id=blocked_id, result=MaxSendResult(ok=False, status_code=403, error_code="forbidden", error_message="bot blocked", message_id=None, recipient_type="user", recipient_id="u1", is_blocked=True))
    rate = mark_notification_history_failed(str(db), history_id=rate_id, result=MaxSendResult(ok=False, status_code=429, error_code="rate_limit", error_message="too many requests", message_id=None, recipient_type="user", recipient_id="u1"))
    skipped = mark_notification_history_skipped(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="skipped", notification_type=BOOKING_REMINDER_48H, scheduled_for=None, reason="disabled")

    assert sent.status == "sent" and sent.sent_at
    assert failed.status == "failed"
    assert blocked.status == "blocked" and blocked.is_blocked
    assert rate.status == "rate_limited" and rate.delivery_error_code == "rate_limit"
    assert skipped.status == "skipped" and skipped.delivery_error_message == "disabled"


def test_pr001_metadata_masking_prevents_phone_token_and_raw_payload_leaks(tmp_path) -> None:
    db = tmp_path / "db.sqlite3"
    init_database(str(db))

    history_id = reserve_notification_history(
        str(db),
        platform=PLATFORM_MAX,
        platform_user_id="u1",
        yclients_record_id="safe-meta",
        notification_type=BOOKING_REMINDER_48H,
        metadata={"phone": "+7 999 123-45-67", "token": "secret-token", "raw_payload": {"phone": "+79991234567"}, "trace_id": "trace-1"},
    )
    row = get_notification_history(str(db), platform=PLATFORM_MAX, platform_user_id="u1", yclients_record_id="safe-meta", notification_type=BOOKING_REMINDER_48H)
    delivery_id = save_delivery_result(
        str(db),
        NotificationDeliveryResult(platform=PLATFORM_MAX, platform_user_id="u1", recipient_type="user", recipient_id="u1", status="failed", error_message="Bearer token abc", metadata={"client_phone": "+7 999 123-45-67", "trace_id": "trace-2", "raw_max_payload": {"secret": "x"}}),
    )

    assert history_id is not None and delivery_id is not None
    stored = str(row.metadata)
    with sqlite3.connect(db) as connection:
        delivery_json, error_message = connection.execute("SELECT metadata_json, error_message FROM notification_delivery WHERE id=?", (delivery_id,)).fetchone()
    combined = stored + str(delivery_json) + str(error_message)
    assert "+7 999 123-45-67" not in combined
    assert "79991234567" not in combined
    assert "secret-token" not in combined
    assert "raw_payload" not in combined
    assert "raw_max_payload" not in combined
    assert "***4567" in combined
    assert "trace-1" in combined and "trace-2" in combined
    assert "скрыто из соображений безопасности" in combined



def test_pr001_history_root_detail_buttons_and_role_access_match_telegram_meaning() -> None:
    from max_barbershop_bot.core.permissions import can_view_notification_history
    from max_barbershop_bot.flows.notification_history import _ROOT_TEXT, format_notification_status_label
    from max_barbershop_bot.ui.buttons import notification_history_detail_keyboard, notification_history_keyboard

    root_keyboard = notification_history_keyboard([])
    detail_keyboard = notification_history_detail_keyboard()
    root_texts = [button.text for row in root_keyboard.rows for button in row]
    detail_texts = [button.text for row in detail_keyboard.rows for button in row]

    assert "📜 История уведомлений" in _ROOT_TEXT
    assert "❌ Только ошибки" in root_texts
    assert "🔄 Обновить" in root_texts
    assert "⬅️ Назад" in root_texts
    assert "🏠 Главное меню" in root_texts
    assert "🔎 Диагностика" in detail_texts
    assert "⬅️ Назад" in detail_texts
    assert "🏠 Главное меню" in detail_texts
    assert format_notification_status_label("sent") == "✅ Отправлено"
    assert format_notification_status_label("failed") == "❌ Ошибка"
    assert format_notification_status_label("blocked") == "🚫 Пользователь заблокировал бота"
    assert format_notification_status_label("skipped_duplicate") == "⏭ Пропущено"
    assert can_view_notification_history("developer")
    assert can_view_notification_history("admin")
    assert can_view_notification_history("manager")
    assert not can_view_notification_history("user")
