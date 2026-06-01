from __future__ import annotations

from types import SimpleNamespace

from app.core import navigation
from app.core.navigation import push_screen, render_previous_screen
from app.core.permissions import DEVELOPER_TG_ID
from app.core.nav_constants import NAV_BACK_CALLBACK
from app.core import screens
from app.keyboards.staff import staff_card_kb, staff_list_kb
from tests.sync import run


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id, first_name="Test")
        self.answers: list[dict] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append({"text": text, **kwargs})
        return self


class FakeCallback:
    data = NAV_BACK_CALLBACK

    def __init__(self, user_id: int, message: FakeMessage) -> None:
        self.from_user = SimpleNamespace(id=user_id, first_name="Test")
        self.message = message


def _button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _button_callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_developer_personnel_root_from_main_menu_contains_role_management(monkeypatch):
    async def fake_get_db_user(user_id: int):
        return {"role": "user"}

    monkeypatch.setattr(screens, "get_db_user", fake_get_db_user)
    message = FakeMessage(DEVELOPER_TG_ID)

    run(screens.render_personnel_menu(message))

    texts = _button_texts(message.answers[-1]["reply_markup"])
    assert "👀 Показать весь персонал" in texts
    assert "➕ Назначить роль" in texts
    assert "➖ Снять роль" in texts


def test_developer_personnel_root_after_back_contains_same_role_management(monkeypatch, fsm_context):
    async def fake_get_db_user(user_id: int):
        return {"role": "user"} if user_id == DEVELOPER_TG_ID else None

    monkeypatch.setattr(screens, "get_db_user", fake_get_db_user)
    monkeypatch.setattr(navigation, "CallbackQuery", FakeCallback)
    run(push_screen(fsm_context, "personnel_menu"))
    run(push_screen(fsm_context, "staff_list"))
    bot_message = FakeMessage(999_000)
    callback = FakeCallback(DEVELOPER_TG_ID, bot_message)

    run(render_previous_screen(callback, fsm_context))

    texts = _button_texts(bot_message.answers[-1]["reply_markup"])
    assert "👀 Показать весь персонал" in texts
    assert "➕ Назначить роль" in texts
    assert "➖ Снять роль" in texts


def test_regular_user_personnel_root_does_not_expose_role_management(monkeypatch):
    async def fake_get_db_user(user_id: int):
        return {"role": "user"}

    monkeypatch.setattr(screens, "get_db_user", fake_get_db_user)
    message = FakeMessage(111111)

    run(screens.render_personnel_menu_for_user(message, 111111))

    assert "Недостаточно прав" in message.answers[-1]["text"]
    assert "reply_markup" not in message.answers[-1]


def test_staff_list_and_detail_back_callback_routes_to_global_back_and_is_valid():
    markups = [staff_list_kb([(123456, "Dev")]), staff_card_kb(can_manage=True, target_tg_id=123456)]
    for markup in markups:
        callbacks = _button_callbacks(markup)
        assert NAV_BACK_CALLBACK in callbacks
        for callback_data in callbacks:
            assert isinstance(callback_data, str)
            assert len(callback_data.encode("utf-8")) <= 64
