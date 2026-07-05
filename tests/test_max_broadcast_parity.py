from datetime import datetime, timedelta, timezone

from max_barbershop_bot.services import client_segments as segments
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
    assert _button_texts(client_segments_menu_keyboard()) == [
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
