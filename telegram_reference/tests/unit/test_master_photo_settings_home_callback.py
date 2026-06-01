from __future__ import annotations

from app.handlers.master_photos_settings import _master_actions_kb, _settings_root_kb, _staff_list_kb, StaffLite
from app.ui.callbacks import NAV_HOME


def _find_button(kb, text: str):
    return next(button for row in kb.inline_keyboard for button in row if button.text == text)


def test_master_photo_detail_home_button_uses_global_callback() -> None:
    kb = _master_actions_kb("123")
    home_button = _find_button(kb, "🏠 Главное меню")
    back_button = _find_button(kb, "⬅️ Назад")

    assert home_button.callback_data == NAV_HOME
    assert home_button.callback_data != "settings:master_photos:home"
    assert isinstance(home_button.callback_data, str)
    assert len(home_button.callback_data.encode("utf-8")) <= 64
    assert back_button.callback_data == "settings:master_photos"


def test_master_photo_related_keyboards_keep_valid_home_callback() -> None:
    root_kb = _settings_root_kb(can_manage_admin_settings=True)
    list_kb = _staff_list_kb([StaffLite(id="1", name="Мастер")])

    for kb in (root_kb, list_kb):
        home_button = _find_button(kb, "🏠 Главное меню")
        assert home_button.callback_data == NAV_HOME
        assert len(home_button.callback_data.encode("utf-8")) <= 64
