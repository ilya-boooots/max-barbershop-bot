from __future__ import annotations

import sys
import types

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.core.config import get_settings
from app.db.sqlite import init_db
from tests.sync import run

sys.modules.setdefault("cv2", types.SimpleNamespace())


@pytest.fixture
def test_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.setenv("YCLIENTS_COMPANY_ID", "12345")
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


@pytest.fixture
def initialized_db(test_env):
    run(init_db())
    return test_env


@pytest.fixture
def fsm_context() -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=2001, user_id=2001)
    return FSMContext(storage=storage, key=key)


@pytest.fixture(autouse=True)
def _clear_booking_flow_cache() -> None:
    from app.handlers import booking_flow

    booking_flow._CACHE.clear()
    booking_flow._SERVICE_RAW_CACHE.clear()


@pytest.fixture(autouse=True)
def _global_env_defaults(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "autouse.sqlite3"
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    monkeypatch.setenv("PROTECTED_DEV_TG_ID", "378881880")
    monkeypatch.setenv("YCLIENTS_COMPANY_ID", "12345")
    monkeypatch.setenv("YCLIENTS_PARTNER_TOKEN", "dummy-partner")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _init_db_for_tests():
    run(init_db())
