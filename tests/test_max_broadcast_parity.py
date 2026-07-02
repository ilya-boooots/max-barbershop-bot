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
