from __future__ import annotations

from types import SimpleNamespace

from app.handlers.master_photos_settings import SupportSettingsStates, settings_support_receive_username
from tests.sync import run


class DummyMessage:
    def __init__(self, text: str, user_id: int = 378881880) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return None


def test_support_username_save_resets_fsm_navigation_before_render(fsm_context, monkeypatch):
    rendered = {"called": False}

    async def _allow(_: int) -> bool:
        return True

    async def _resolve_support_settings():
        return "12345", SimpleNamespace(description="desc", username="help"), None

    async def _show_editor(message, state):
        rendered["called"] = True
        await state.set_state(SupportSettingsStates.SUPPORT_SETTINGS_MENU)

    monkeypatch.setattr("app.handlers.master_photos_settings._is_allowed", _allow)
    monkeypatch.setattr("app.handlers.master_photos_settings.resolve_support_settings", _resolve_support_settings)
    monkeypatch.setattr("app.handlers.master_photos_settings._show_support_settings_editor", _show_editor)

    run(fsm_context.set_state(SupportSettingsStates.SUPPORT_EDIT_USERNAME))
    run(fsm_context.update_data(__nav_stack__=[{"name": "support", "payload": {"stale": True}}], stale_key="value"))

    message = DummyMessage("@nevzorovilya")
    run(settings_support_receive_username(message, fsm_context))

    assert rendered["called"] is True
    assert any("✅ Аккаунт поддержки обновлён" in text for text in message.answers)
    assert run(fsm_context.get_state()) == SupportSettingsStates.SUPPORT_SETTINGS_MENU.state
    data = run(fsm_context.get_data())
    assert data.get("__nav_stack__") == []
    assert "stale_key" not in data
