from __future__ import annotations

import asyncio

import pytest

from app.handlers import master_photos_settings as mps


pytestmark = pytest.mark.unit


class _DummyMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))


class _DummyCallback:
    def __init__(self, user_id: int) -> None:
        self.from_user = type("U", (), {"id": user_id})()
        self.message = _DummyMessage()

    async def answer(self):
        return None


class _DummyState:
    def __init__(self, data: dict) -> None:
        self._data = data

    async def get_data(self):
        return self._data


class _DummyClient:
    async def close(self):
        return None


def test_name_update_success_message(monkeypatch):
    callback = _DummyCallback(user_id=11)
    state = _DummyState({"profile_new_name": "Вазген"})

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mps, "_is_valid_name", lambda v: True)
    async def _get_user_name(_uid):
        return {"yclients_client_id": "123", "name": "Старое"}

    monkeypatch.setattr(mps, "get_user_by_tg_id", _get_user_name)
    async def _get_creds():
        return type("C", (), {"company_id": 1})(), None

    monkeypatch.setattr(mps, "get_yclients_credentials", _get_creds)
    async def _build_client():
        return _DummyClient(), None

    monkeypatch.setattr(mps, "build_yclients_client", _build_client)
    monkeypatch.setattr(mps, "get_client_details", _noop)
    monkeypatch.setattr(mps, "_extract_client_row", lambda *_: {})
    monkeypatch.setattr(mps, "build_yclients_client_update_payload", lambda **_: {"name": "Вазген", "phone": "+7999"})
    monkeypatch.setattr(mps, "yclients_update_client", _noop)
    monkeypatch.setattr(mps, "update_profile_name_phone", _noop)
    monkeypatch.setattr(mps, "clear_state_preserving_navigation", _noop)
    monkeypatch.setattr(mps, "_show_profile_root", _noop)

    asyncio.run(mps.settings_profile_save_name(callback, state))

    sent_texts = [text for text, _ in callback.message.answers]
    assert "✅ Имя обновлено, Вазген" in sent_texts
    assert all("Данные также обновлены в YClients" not in text for text in sent_texts)


def test_phone_update_success_message_uses_user_name(monkeypatch):
    callback = _DummyCallback(user_id=22)
    state = _DummyState(
        {
            "profile_new_phone": "+79990001122",
            "profile_new_phone_raw": "89990001122",
            "profile_new_phone_digits": "79990001122",
            "profile_new_phone_ru7": "79990001122",
            "profile_new_phone_ru8": "89990001122",
        }
    )

    async def _noop(*args, **kwargs):
        return None

    async def _get_user_phone(_uid):
        return {"yclients_client_id": "123", "name": "Вазген"}

    monkeypatch.setattr(mps, "get_user_by_tg_id", _get_user_phone)
    async def _get_creds():
        return type("C", (), {"company_id": 1})(), None

    monkeypatch.setattr(mps, "get_yclients_credentials", _get_creds)
    async def _resolve_conflict(**_):
        return type("R", (), {"conflict_type": mps.PhoneConflictType.NO_CONFLICT})()

    monkeypatch.setattr(mps, "resolve_phone_change_conflict", _resolve_conflict)
    async def _build_client_phone():
        return _DummyClient(), None

    monkeypatch.setattr(mps, "build_yclients_client", _build_client_phone)
    monkeypatch.setattr(mps, "get_client_details", _noop)
    monkeypatch.setattr(mps, "_extract_client_row", lambda *_: {})
    monkeypatch.setattr(mps, "build_yclients_client_update_payload", lambda **_: {"name": "Вазген", "phone": "+7999"})
    monkeypatch.setattr(mps, "yclients_update_client", _noop)
    monkeypatch.setattr(mps, "update_profile_phone_and_mapping", _noop)
    monkeypatch.setattr(mps, "clear_state_preserving_navigation", _noop)
    monkeypatch.setattr(mps, "_show_profile_root", _noop)

    asyncio.run(mps.settings_profile_save_phone(callback, state))

    sent_texts = [text for text, _ in callback.message.answers]
    assert "✅ Вазген, телефон обновлён" in sent_texts
    assert all("Данные также обновлены в YClients" not in text for text in sent_texts)
