from __future__ import annotations

from app.handlers.master_photos_settings import (
    _profile_name_confirm_kb,
    _profile_phone_confirm_kb,
    _profile_phone_conflict_kb,
    _profile_phone_relink_confirm_kb,
    _profile_root_kb,
)
from app.ui.callbacks import NAV_HOME


def _home_callback(kb) -> str:
    return next(
        button.callback_data
        for row in kb.inline_keyboard
        for button in row
        if button.text == "🏠 Главное меню"
    )


def test_profile_keyboards_home_button_uses_global_home_callback() -> None:
    keyboards = [
        _profile_root_kb(),
        _profile_name_confirm_kb(),
        _profile_phone_confirm_kb(),
        _profile_phone_relink_confirm_kb(),
        _profile_phone_conflict_kb(),
    ]

    for kb in keyboards:
        callback_data = _home_callback(kb)
        assert callback_data == NAV_HOME
        assert callback_data != "settings:master_photos:home"
        assert isinstance(callback_data, str)
        assert len(callback_data.encode("utf-8")) <= 64
