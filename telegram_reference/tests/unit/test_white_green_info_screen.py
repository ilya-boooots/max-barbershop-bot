from app.handlers import notifications


def test_white_green_info_text_has_single_white_and_green_sections():
    text = notifications.WHITE_GREEN_INFO_TEXT
    assert text.count("⚪ Белые уведомления") == 1
    assert text.count("🟢 Зелёные уведомления") == 1


def test_white_green_info_keyboard_has_single_back_and_home_buttons():
    keyboard = notifications.white_green_info_kb()
    labels = [btn.text for row in keyboard.inline_keyboard for btn in row]

    assert labels.count("⬅️ Назад") == 1
    assert labels.count("🏠 Главное меню") == 1


def test_white_green_info_keyboard_callback_data_valid():
    keyboard = notifications.white_green_info_kb()
    callbacks = [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]
    for cb in callbacks:
        assert isinstance(cb, str) and cb.strip()
        assert len(cb.encode("utf-8")) <= 64, cb
