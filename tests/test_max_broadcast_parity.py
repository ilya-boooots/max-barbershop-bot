import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from max_barbershop_bot.services import client_segments as segments
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.ui.buttons import LOST_CLIENTS_OPEN_PAYLOAD, broadcast_audience_keyboard, broadcast_menu_keyboard, client_segments_menu_keyboard


def _button_texts(keyboard):
    return [button.text for row in keyboard.rows for button in row]


def test_broadcast_main_menu_matches_reference_buttons():
    assert _button_texts(broadcast_menu_keyboard()) == [
        "✉️ Разовая рассылка",
        "🎯 Сегменты клиентов",
        "😔 Потерянные клиенты",
        "📊 Эффективность",
        "📜 История уведомлений",
        "🧪 Тест уведомлений",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_one_time_broadcast_audience_matches_reference_buttons():
    assert _button_texts(broadcast_audience_keyboard()) == [
        "👥 Все клиенты",
        "🔥 Активные за 30 дней",
        "😴 Потерянные 30 дней",
        "😴 Потерянные 60 дней",
        "😴 Потерянные 90 дней",
        "📅 Без будущей записи",
        "🧪 Отправить себе",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_segment_menu_matches_reference_buttons():
    buttons = _button_texts(client_segments_menu_keyboard())
    assert buttons == [
        "👥 Все клиенты",
        "🔥 Активные за 30 дней",
        "😴 Не были 30 дней",
        "😴 Не были 60 дней",
        "😴 Не были 90 дней",
        "📅 Без будущей записи",
        "❌ Отменили запись",
        "💈 По мастеру",
        "✂️ По услуге",
        "🎂 День рождения скоро",
        "🔄 Обновить сегменты",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]
    assert "🔥 Активные за 7 дней" not in buttons
    assert "🔥 Активные 7 дней" not in buttons
    assert "🗓 Активные 90 дней" not in buttons
    assert "🔥 Активные за 90 дней" not in buttons


def test_only_active_30_is_registered_as_telegram_parity_segment():
    from max_barbershop_bot.flows import client_segments

    assert "SEGMENTS_ACTIVE_30_PAYLOAD" in client_segments.__dict__
    assert "SEGMENTS_ACTIVE_7_PAYLOAD" not in client_segments.__dict__
    assert "SEGMENTS_ACTIVE_90_PAYLOAD" not in client_segments.__dict__

    source = inspect.getsource(client_segments._load_segment)
    assert "get_active_clients(30)" in source
    assert "get_active_clients(7)" not in source
    assert "get_active_clients(90)" not in source


def test_confirmed_booking_counts_active_and_cancelled_does_not():
    now = datetime.now(timezone.utc)
    active_record = {
        "id": 1,
        "datetime": (now - timedelta(days=1)).isoformat(),
        "client": {"id": "101", "phone": "+79990000001", "name": "Анна"},
        "attendance": "1",
    }
    cancelled_record = {
        "id": 2,
        "datetime": (now - timedelta(days=1)).isoformat(),
        "client": {"id": "102", "phone": "+79990000002", "name": "Иван"},
        "attendance": "-1",
    }
    windows, diagnostics = segments._active_keys_by_window([active_record, cancelled_record], now, allowed_keys={"yc:101", "yc:102"})
    assert "yc:101" in windows[30]
    assert "yc:102" not in windows[30]
    assert diagnostics["skipped_cancelled_count"] == 1


def test_active_30_service_matches_telegram_definition(monkeypatch):
    now = datetime.now(timezone.utc)
    inside = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=1)).isoformat()
    records = [
        {"id": 1, "datetime": inside, "client": {"id": "101", "phone": "+79990000001", "name": "Анна"}},
        {"id": 2, "datetime": inside, "client": {"id": "101", "phone": "+79990000001", "name": "Анна"}},
        {"id": 3, "datetime": inside, "client": {"id": "102", "phone": "+79990000002", "name": "Иван"}, "deleted": True},
        {"id": 4, "datetime": future, "client": {"id": "103", "phone": "+79990000003", "name": "Олег"}},
        {"id": 5, "datetime": inside},
        {"id": 6, "datetime": inside, "client": {"id": "104", "phone": "+79990000004", "name": "Мария"}, "attendance": "-1"},
        {"id": 7, "client": {"id": "105", "phone": "+79990000005", "name": "Без даты"}},
    ]
    service = segments.ClientSegmentService(settings_repository=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        service,
        "_require_settings",
        lambda: YClientsSettings(company_id="1", partner_token="partner", user_token="user", branch_timezone="UTC"),
    )

    async def fake_fetch_records(settings, *, date_from: str, date_to: str):
        assert date_from <= now.date().isoformat()
        assert date_to == now.date().isoformat()
        return records

    monkeypatch.setattr(service, "_fetch_records", fake_fetch_records)

    result = asyncio.run(service.get_active_clients(30))

    assert result.segment_type == "active_30"
    assert result.count == 3
    assert {member.yclients_client_id for member in result.members} == {"101", "104", "105"}
    assert result.description == "Клиенты, которые были активны за последние 30 дней."


def test_active_30_formatting_matches_telegram_summary_and_empty_state():
    result = segments.ClientSegmentResult(
        segment_type="active_30",
        title="🔥 Активные за 30 дней",
        description="Клиенты, которые были активны за последние 30 дней.",
        members=[],
        branch_timezone="UTC",
        calculated_at="2026-07-07T12:00:00+00:00",
        diagnostics={},
    )

    assert segments.format_segment_summary(result) == (
        "🔥 Активные за 30 дней\n\n"
        "Клиенты, которые были активны за последние 30 дней.\n\n"
        "Количество клиентов: 0\n"
        "Обновлено: 07.07.2026 12:00\n\n"
        "😌 В этом сегменте пока нет клиентов."
    )


def test_stale_active_callback_gets_friendly_text():
    from max_barbershop_bot.flows import client_segments

    source = inspect.getsource(client_segments.handle_segment_selected)
    assert "segments:active:" in source
    assert client_segments.SEGMENT_STALE_TEXT == "⚠️ Данные устарели. Откройте раздел заново."
    assert "SEGMENT_STALE_TEXT" in source


def test_lost_clients_main_button_opens_section_not_text_prompt():
    keyboard = broadcast_menu_keyboard()
    lost_buttons = [button for row in keyboard.rows for button in row if button.text == "😔 Потерянные клиенты"]
    assert lost_buttons
    assert lost_buttons[0].payload == LOST_CLIENTS_OPEN_PAYLOAD


def test_confirm_send_uses_omnichannel_for_all_non_self_audiences():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    source = inspect.getsource(broadcasts.handle_confirm_send)
    assert "if is_omnichannel:" in source
    assert "if audience.key == AUDIENCE_SOURCE_YCLIENTS_ALL" not in source


def test_broadcast_empty_and_whitespace_text_rejected_like_telegram():
    from max_barbershop_bot.services.broadcasts import validate_broadcast_text

    for value in ("", "   ", "\n\t"):
        validation = validate_broadcast_text(value)
        assert not validation.ok
        assert validation.error == "⚠️ Текст рассылки не может быть пустым. Введите сообщение."


def test_broadcast_too_long_text_rejected_like_telegram():
    from max_barbershop_bot.services.broadcasts import MAX_BROADCAST_TEXT_LENGTH, validate_broadcast_text

    validation = validate_broadcast_text("а" * (MAX_BROADCAST_TEXT_LENGTH + 1))
    assert not validation.ok
    assert validation.error == f"⚠️ Слишком длинный текст. Максимум {MAX_BROADCAST_TEXT_LENGTH} символов."


def test_preview_and_confirm_buttons_match_telegram_labels():
    from max_barbershop_bot.ui.buttons import broadcast_confirm_keyboard, broadcast_preview_keyboard

    assert _button_texts(broadcast_preview_keyboard()) == [
        "✅ Отправить",
        "✏️ Изменить текст",
        "📷 Добавить фото",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]
    assert _button_texts(broadcast_confirm_keyboard()) == [
        "✅ Отправить",
        "✏️ Изменить текст",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_preview_send_label_sends_without_intermediate_confirm_screen():
    from max_barbershop_bot.ui.buttons import BROADCAST_CONFIRM_SEND_PAYLOAD, broadcast_preview_keyboard

    first_button = broadcast_preview_keyboard().rows[0][0]
    assert first_button.text == "✅ Отправить"
    assert first_button.payload == BROADCAST_CONFIRM_SEND_PAYLOAD


def test_no_non_self_confirm_without_preview_guard():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    confirm_source = inspect.getsource(broadcasts.handle_confirm_send)
    assert "BROADCAST_ONE_TIME_PREVIEW_SCREEN" in confirm_source
    assert "BROADCAST_ONE_TIME_CONFIRM_SCREEN" not in confirm_source
    assert "_BROADCAST_PREVIEW_TOKEN_KEY" in confirm_source
    assert "await _show_stale_broadcast(context)" in confirm_source

    audience_source = inspect.getsource(broadcasts._select_audience)
    assert "await _show_preview(context)" in audience_source
    assert "_format_omnichannel_confirm(estimate)" not in audience_source


def test_preview_next_requires_valid_preview_state():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    source = inspect.getsource(broadcasts.handle_preview_next)
    assert "await handle_confirm_send(context)" in source
    assert "BROADCAST_ONE_TIME_CONFIRM_SCREEN" not in source


def test_cancel_back_home_safety_clears_or_steps_back():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    back_source = inspect.getsource(broadcasts.handle_broadcast_back)
    home_source = inspect.getsource(broadcasts.handle_broadcast_home)
    assert "BROADCAST_ONE_TIME_PREVIEW_SCREEN" in back_source
    assert "BROADCAST_ONE_TIME_TEXT_SCREEN" in back_source
    assert "_BROADCAST_PREVIEW_TOKEN_KEY" in back_source
    assert "_clear_broadcast_state(context)" in home_source


def test_duplicate_confirm_guard_reuses_pr004_lock_and_token():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    source = inspect.getsource(broadcasts.handle_confirm_send)
    assert "is_action_locked(_BROADCAST_SEND_LOCK_KEY)" in source
    assert "acquire_action_lock(_BROADCAST_SEND_LOCK_KEY" in source
    assert "_BROADCAST_SEND_TOKEN_KEY" in source
    assert "Эта рассылка уже отправляется или была отправлена" in source


def test_media_photo_preview_closest_max_equivalent_documented_in_code():
    import inspect
    from max_barbershop_bot.flows import broadcasts
    from max_barbershop_bot.ui.buttons import broadcast_preview_keyboard

    assert "📷 Добавить фото" in _button_texts(broadcast_preview_keyboard())
    source = inspect.getsource(broadcasts.handle_text_input)
    assert "Этот тип вложения пока не поддерживается в MAX 🙏" in source
    assert "extract_broadcast_attachment" in source


def test_self_test_flow_still_uses_preview_and_confirm_guard():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    text_source = inspect.getsource(broadcasts.handle_text_input)
    select_source = inspect.getsource(broadcasts._select_audience)
    assert "SELF_AUDIENCE" in text_source
    assert "await _select_audience(context, SELF_AUDIENCE)" in text_source
    assert "await _show_preview(context)" in select_source


def test_self_test_preview_does_not_load_yclients_or_client_audiences():
    import inspect
    from max_barbershop_bot.flows import broadcasts

    show_preview_source = inspect.getsource(broadcasts._show_preview)
    self_fast_path = show_preview_source.split("return", 1)[0]
    assert "_broadcast_audience(context).key == SELF_AUDIENCE.key" in self_fast_path
    assert "_fetch_yclients_clients_for_audience" not in self_fast_path
    assert "_omnichannel_service" not in self_fast_path


def test_self_test_resolves_only_current_actor_and_uses_self_audience_source():
    import inspect
    from max_barbershop_bot.flows import broadcasts
    from max_barbershop_bot.services.broadcasts import BROADCAST_SELF_AUDIENCE, SELF_AUDIENCE

    resolve_source = inspect.getsource(broadcasts._resolve_audience_recipients)
    self_branch = resolve_source.split("else:", 1)[0]
    assert SELF_AUDIENCE.key == "send_to_self"
    assert BROADCAST_SELF_AUDIENCE == "send_to_self"
    assert "find_by_platform_user_id(str(_user_id(context) or \"\"), platform=PLATFORM_MAX)" in self_branch
    assert "list_users_for_broadcast_audience" not in self_branch


def test_broadcast_report_contains_metrics_without_raw_secrets_payload_or_phone():
    from max_barbershop_bot.services.broadcasts import BroadcastSendReport, format_broadcast_report

    text = format_broadcast_report(
        BroadcastSendReport(
            total=1,
            sent=1,
            failed=0,
            blocked=0,
            stopped=0,
            skipped_notifications_disabled=0,
            skipped_missing_recipient_id=0,
            rate_limited=0,
            broadcast_id="secret-token-79990000001",
        )
    )
    assert "Всего клиентов: 1" in text
    assert "Отправлено: 1" in text
    assert "secret-token" not in text
    assert "payload" not in text.lower()
    assert "79990000001" not in text

class _FakeLostSegmentService(segments.ClientSegmentService):
    def __init__(self, records):
        self.records = records
        self.last_date_range = None

    def _require_settings(self):
        from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
        return YClientsSettings(company_id="1", partner_token="p", user_token="u", branch_timezone="UTC")

    async def _fetch_records(self, settings, *, date_from: str, date_to: str):
        self.last_date_range = (date_from, date_to)
        return self.records


def _lost_record(client_id: str, days_ago: int, *, attendance=1, deleted=False):
    now = datetime.now(timezone.utc)
    return {
        "datetime": (now - timedelta(days=days_ago)).isoformat(),
        "client": {"id": client_id, "phone": f"+79990000{int(client_id):03d}", "name": f"Клиент {client_id}"},
        "attendance": attendance,
        "deleted": deleted,
    }


def _future_record(client_id: str, days_ahead: int, *, attendance=0):
    now = datetime.now(timezone.utc)
    return {
        "datetime": (now + timedelta(days=days_ahead)).isoformat(),
        "client": {"id": client_id, "phone": f"+79990000{int(client_id):03d}", "name": f"Клиент {client_id}"},
        "attendance": attendance,
    }


def test_lost_30_60_90_counts_match_telegram_attendance_and_thresholds():
    import asyncio

    async def run():
        service = _FakeLostSegmentService([
            _lost_record("101", 30),
            _lost_record("102", 60),
            _lost_record("103", 90),
            _lost_record("104", 29),
            _lost_record("105", 120, attendance=-1),
            _lost_record("106", 120, deleted=True),
        ])

        result_30 = await service.get_lost_clients(30)
        result_60 = await service.get_lost_clients(60)
        result_90 = await service.get_lost_clients(90)

        assert {member.yclients_client_id for member in result_30.members} == {"101", "102", "103"}
        assert {member.yclients_client_id for member in result_60.members} == {"102", "103"}
        assert {member.yclients_client_id for member in result_90.members} == {"103"}

    asyncio.run(run())


def test_lost_segments_use_telegram_past_records_window_not_future_lookahead():
    import asyncio

    async def run():
        service = _FakeLostSegmentService([_lost_record("101", 45)])

        await service.get_lost_clients(30)

        assert service.last_date_range is not None
        assert service.last_date_range[1] == datetime.now(timezone.utc).date().isoformat()

    asyncio.run(run())


def test_lost_duplicate_client_counts_once_and_future_row_excludes_if_present():
    import asyncio

    async def run():
        service = _FakeLostSegmentService([
            _lost_record("101", 45),
            _lost_record("101", 75),
            _lost_record("102", 75),
            _future_record("102", 5, attendance=0),
        ])

        result = await service.get_lost_clients(30)

        assert [member.yclients_client_id for member in result.members] == ["101"]

    asyncio.run(run())


def test_lost_segment_detail_format_matches_telegram_count_detail():
    result = segments.ClientSegmentResult(
        segment_type="lost_30",
        title="😴 Не были 30 дней",
        description="Клиенты, которые не были 30 дней и не имеют будущей записи.",
        members=[],
        branch_timezone="UTC",
        calculated_at=datetime.now(timezone.utc).isoformat(),
    )

    text = segments.format_segment_summary(result)

    assert text.startswith("😴 Не были 30 дней\n\nКлиенты, которые не были 30 дней")
    assert "Количество клиентов: 0" in text
    assert "😌 В этом сегменте пока нет клиентов." in text
    assert "телефон" not in text


def _create_cancellation_events_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "cancelled_segment.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE cancellation_recovery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'max',
            platform_user_id TEXT NOT NULL,
            yclients_record_id TEXT NOT NULL,
            yclients_client_id TEXT,
            max_user_id TEXT,
            chat_id TEXT,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT,
            skipped_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()
    return db_path


def _insert_cancellation_event(db_path, *, platform_user_id, created_at, scheduled_at, record_id):
    import sqlite3

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO cancellation_recovery_events (
                platform, platform_user_id, yclients_record_id, yclients_client_id,
                scheduled_at, status, created_at, updated_at
            ) VALUES ('max', ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (platform_user_id, record_id, f"yc-{platform_user_id}", scheduled_at, created_at, created_at),
        )


def test_cancelled_segment_uses_local_recovery_events_not_yclients(monkeypatch, tmp_path):
    db_path = _create_cancellation_events_db(tmp_path)
    now = datetime.now(timezone.utc)
    inside = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=31)).isoformat()
    future_schedule = (now + timedelta(days=10)).isoformat()
    _insert_cancellation_event(db_path, platform_user_id="u1", created_at=inside, scheduled_at=future_schedule, record_id="r1")
    _insert_cancellation_event(db_path, platform_user_id="u1", created_at=inside, scheduled_at=future_schedule, record_id="r2")
    _insert_cancellation_event(db_path, platform_user_id="u2", created_at=old, scheduled_at=future_schedule, record_id="r3")
    _insert_cancellation_event(db_path, platform_user_id="u3", created_at=inside, scheduled_at=old, record_id="r4")

    service = segments.ClientSegmentService(settings_repository=None, database_path=str(db_path))  # type: ignore[arg-type]

    async def fail_fetch_records(*args, **kwargs):
        raise AssertionError("cancelled segment must not fetch live YClients records")

    monkeypatch.setattr(service, "_fetch_records", fail_fetch_records)

    result = asyncio.run(service.get_cancelled_clients(30))

    assert result.segment_type == "cancelled"
    assert result.count == 2
    assert result.diagnostics["source_table"] == "cancellation_recovery_events"
    assert result.diagnostics["timestamp_column"] == "created_at"
    assert result.diagnostics["scheduled_at_used"] is False
    assert result.diagnostics["is_test_column_present"] is False


def test_cancelled_segment_excludes_test_events_when_column_exists(tmp_path):
    import sqlite3

    db_path = tmp_path / "cancelled_segment_is_test.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE cancellation_recovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform_user_id TEXT NOT NULL,
                yclients_record_id TEXT NOT NULL,
                yclients_client_id TEXT,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_test INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        now = datetime.now(timezone.utc).isoformat()
        connection.execute("INSERT INTO cancellation_recovery_events (platform_user_id, yclients_record_id, scheduled_at, status, created_at, updated_at, is_test) VALUES ('real', 'r1', ?, 'pending', ?, ?, 0)", (now, now, now))
        connection.execute("INSERT INTO cancellation_recovery_events (platform_user_id, yclients_record_id, scheduled_at, status, created_at, updated_at, is_test) VALUES ('test', 'r2', ?, 'pending', ?, ?, 1)", (now, now, now))

    service = segments.ClientSegmentService(settings_repository=None, database_path=str(db_path))  # type: ignore[arg-type]
    result = asyncio.run(service.get_cancelled_clients(30))

    assert result.count == 1
    assert result.diagnostics["is_test_column_present"] is True


def test_cancelled_segment_formatting_matches_telegram_summary_and_empty_state():
    result = segments.ClientSegmentResult(
        segment_type="cancelled",
        title="❌ Отменили запись",
        description="Клиенты, которые отменяли запись и могут вернуться через мягкое напоминание.",
        members=[],
        branch_timezone="UTC",
        calculated_at="2026-07-07T12:00:00+00:00",
        diagnostics={},
    )

    assert segments.format_segment_summary(result) == (
        "❌ Отменили запись\n\n"
        "Клиенты, которые отменяли запись и могут вернуться через мягкое напоминание.\n\n"
        "Количество клиентов: 0\n"
        "Обновлено: 07.07.2026 12:00\n\n"
        "😌 В этом сегменте пока нет клиентов."
    )


def test_cancelled_segment_handoff_uses_cancelled_recent_and_preview_text_flow():
    from max_barbershop_bot.flows import client_segments

    assert client_segments._audience_key_from_segment_payload("segments:cancelled", "cancelled") == "cancelled_recent"
    source = inspect.getsource(client_segments.handle_segment_broadcast)
    assert "open_segment_broadcast_text" in source
    assert "send_confirmed" not in source


def test_broadcast_flow_accepts_cancelled_recent_alias(monkeypatch):
    from max_barbershop_bot.flows import broadcasts

    calls = []

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def get_cancelled_clients(self, days):
            calls.append(days)
            return segments.ClientSegmentResult(segment_type="cancelled", title="❌ Отменили запись", members=[])

    monkeypatch.setattr(broadcasts, "ClientSegmentService", FakeService)

    asyncio.run(broadcasts._fetch_yclients_clients_for_audience(context=None, audience_key="cancelled_recent"))  # type: ignore[arg-type]

    assert calls == [30]
