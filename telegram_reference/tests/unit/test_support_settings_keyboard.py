from __future__ import annotations

from app.handlers.master_photos_settings import _support_settings_kb
from app.ui.callbacks import NAV_HOME


def test_support_settings_home_button_uses_global_home_callback() -> None:
    kb = _support_settings_kb()
    home_button = next(
        button
        for row in kb.inline_keyboard
        for button in row
        if button.text == "🏠 Главное меню"
    )

    assert home_button.callback_data != "settings:master_photos:home"
    assert home_button.callback_data == NAV_HOME
    assert isinstance(home_button.callback_data, str)
    assert len(home_button.callback_data.encode("utf-8")) <= 64
