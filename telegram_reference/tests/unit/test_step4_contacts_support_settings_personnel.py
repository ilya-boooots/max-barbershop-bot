from app.core.permissions import DEVELOPER_TG_ID
from app.handlers import master_photos_settings as settings
from app.keyboards.staff import personnel_menu_inline_kb
from app.services.contacts import ResolvedContacts, render_contacts_block
from app.services.support import support_screen_kb


def _collect_callbacks(markup):
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data is not None:
                out.append(btn.callback_data)
    return out


def _assert_valid_callback_data(callbacks):
    for cb in callbacks:
        assert isinstance(cb, str) and cb.strip()
        assert len(cb.encode("utf-8")) <= 64, cb


def test_contacts_renderer_has_no_none_strings():
    rendered = render_contacts_block(ResolvedContacts(address="—", phone="—", schedule="—"))
    assert "None" not in rendered
    assert "📍 Контакты" in rendered


def test_support_keyboard_callback_data_and_home_button():
    kb = support_screen_kb(username="support_barber", include_home=True)
    callbacks = _collect_callbacks(kb)
    _assert_valid_callback_data(callbacks)
    assert any("Главное меню" in btn.text for row in kb.inline_keyboard for btn in row)


def test_settings_root_callback_data_is_valid():
    kb = settings._settings_root_kb(can_manage_admin_settings=True)
    callbacks = _collect_callbacks(kb)
    _assert_valid_callback_data(callbacks)


def test_personnel_keyboard_callback_data_is_valid():
    kb = personnel_menu_inline_kb(can_manage=True)
    callbacks = _collect_callbacks(kb)
    _assert_valid_callback_data(callbacks)


def test_protected_developer_id_constant_is_stable():
    assert DEVELOPER_TG_ID == 378881880


def test_contacts_edit_keyboard_home_uses_global_callback_and_valid_size():
    kb = settings._contacts_edit_kb()
    home_btn = next(btn for row in kb.inline_keyboard for btn in row if btn.text == "🏠 Главное меню")
    assert home_btn.callback_data == settings.NAV_HOME
    assert len(home_btn.callback_data.encode("utf-8")) <= 64


def test_contacts_edit_keyboard_back_is_not_legacy_maps_callback():
    kb = settings._contacts_edit_kb()
    back_btn = next(btn for row in kb.inline_keyboard for btn in row if btn.text == "⬅️ Назад")
    assert back_btn.callback_data == settings.CB_CONTACTS_BACK
    assert back_btn.callback_data != "contacts:maps"


def test_contacts_edit_keyboard_callbacks_are_valid():
    kb = settings._contacts_edit_kb()
    _assert_valid_callback_data(_collect_callbacks(kb))
