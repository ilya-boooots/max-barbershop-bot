from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows.settings import (
    AUTOMATION_DEFAULTS,
    get_automation_setting,
    handle_settings_automation_root,
    handle_settings_automation_input,
    handle_settings_automation_toggle,
    render_automation_module_text,
    upsert_automation_setting,
)
from max_barbershop_bot.max_api.sender import MaxSendResult
from max_barbershop_bot.repositories.app_settings import AppSettingsRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.ui.buttons import (
    LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX,
    REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX,
    SETTINGS_AUTOMATION_EDIT_PREFIX,
    SETTINGS_AUTOMATION_MODULE_PREFIX,
    SETTINGS_AUTOMATION_ROOT_PAYLOAD,
    SETTINGS_BACK_PAYLOAD,
    SETTINGS_HOME_PAYLOAD,
    settings_automation_module_keyboard,
    settings_automation_root_keyboard,
)


@dataclass
class Sent:
    text: str
    keyboard: object | None


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[Sent] = []
        self.answered = 0

    async def send_to_user(self, user_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent(text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="user", recipient_id=str(user_id))

    async def send_to_chat(self, chat_id, text, *, keyboard=None, attachments=None, format=None, metadata=None):
        self.sent.append(Sent(text, keyboard))
        return MaxSendResult(ok=True, status_code=200, message_id="m1", recipient_type="chat", recipient_id=str(chat_id))

    async def answer_callback(self, callback_id):
        self.answered += 1
        return None


def _event(payload: str, user: str = "42") -> NormalizedEvent:
    return NormalizedEvent("message_callback", user, user, "900", None, payload, "cb")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "bot.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    StaffRolesRepository(str(path)).assign_role("42", "manager", assigned_by_platform_user_id="1")
    return path


def _buttons(keyboard) -> list[tuple[str, str | None]]:
    return [(button.text, button.payload) for row in keyboard.rows for button in row]


def test_plan_mentions_pr_029_topic() -> None:
    text = open("docs/max_telegram_parity_plan_v2.md", encoding="utf-8").read()
    assert "PR-029 — Automation settings modules parity" in text
    assert "Automation root/module screens" in text


@pytest.mark.parametrize("key", ["post_visit_review", "cancellation_return", "lost_clients", "birthday", "repeat_visit", "anti_spam", "quiet_hours"])
def test_missing_automation_settings_are_enabled_by_default(db, key: str) -> None:
    assert get_automation_setting(key, AppSettingsRepository(str(db))).get("enabled") is True


def test_existing_disabled_setting_is_preserved_and_stored_values_override_defaults(db) -> None:
    repo = AppSettingsRepository(str(db))
    upsert_automation_setting("lost_clients", {"enabled": False, "threshold_days": [10, 20, 30]}, repo)
    setting = get_automation_setting("lost_clients", repo)
    assert setting["enabled"] is False
    assert setting["threshold_days"] == [10, 20, 30]
    assert setting["text_30"] == AUTOMATION_DEFAULTS["lost_clients"]["text_30"]


def test_module_list_order_and_names_match_telegram_ui() -> None:
    assert _buttons(settings_automation_root_keyboard())[:8] == [
        ("⭐ Оценка после визита", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}post_visit_review"),
        ("❌ Возврат после отмены", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}cancellation_return"),
        ("😔 Потерянные клиенты", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}lost_clients"),
        ("🎂 День рождения", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}birthday"),
        ("🔁 Повторный визит", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}repeat_visit"),
        ("🔕 Антиспам", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}anti_spam"),
        ("🔗 Ссылки на отзывы", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}review_links"),
        ("⏰ Рабочее время / тихие часы", f"{SETTINGS_AUTOMATION_MODULE_PREFIX}quiet_hours"),
    ]


def test_module_detail_status_edit_buttons_and_payload_limits(db) -> None:
    setting = get_automation_setting("post_visit_review", AppSettingsRepository(str(db)))
    text = render_automation_module_text("post_visit_review", setting)
    assert "Статус: ✅ Включено" in text
    assert "Задержка после визита: 2 ч" in text
    buttons = _buttons(settings_automation_module_keyboard("review_links"))
    assert ("🟡 Изменить ссылку Яндекс", f"{SETTINGS_AUTOMATION_EDIT_PREFIX}review_links:yandex_url") in buttons
    assert all(len((payload or "").encode("utf-8")) <= 64 for _, payload in buttons)


def test_toggle_persists_and_audits_without_delivery_runtime(db) -> None:
    sender = FakeSender()
    asyncio.run(handle_settings_automation_toggle(RouterContext(_event("set:auto:t:lost_clients"), sender)))
    assert get_automation_setting("lost_clients", AppSettingsRepository(str(db)))["enabled"] is False
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT action, section, metadata_json FROM settings_audit_log").fetchall()
    assert any(row[0] == "automation_toggled" and row[1] == "automation" and "lost_clients" in (row[2] or "") for row in rows)


def test_root_access_denied_for_unauthorized_user(db) -> None:
    sender = FakeSender()
    asyncio.run(handle_settings_automation_root(RouterContext(_event("settings:automation", user="99"), sender)))
    assert sender.sent[-1].text == "У вас нет доступа к этому разделу."


def test_audit_metadata_does_not_store_raw_url_or_text(db) -> None:
    state.set_current_screen("42", "900", "settings_automation_edit")
    state.set_state_data_value("42", "900", "automation_edit_key", "review_links")
    state.set_state_data_value("42", "900", "automation_edit_field", "yandex_url")
    sender = FakeSender()
    raw_url = "https://reviews.example/private-token-url"

    event = NormalizedEvent("message_created", "42", "42", "900", raw_url, None, None)
    asyncio.run(handle_settings_automation_input(RouterContext(event, sender)))

    with sqlite3.connect(db) as conn:
        metadata_rows = [row[0] or "" for row in conn.execute("SELECT metadata_json FROM settings_audit_log").fetchall()]
    joined = "\n".join(metadata_rows)
    assert "private-token-url" not in joined
    assert "https://reviews.example" not in joined
    assert "value_length" in joined


def test_module_back_buttons_match_telegram_reference() -> None:
    root_buttons = _buttons(settings_automation_module_keyboard("lost_clients"))
    assert ("⬅️ Назад", SETTINGS_AUTOMATION_ROOT_PAYLOAD) in root_buttons
    assert ("🏠 Главное меню", SETTINGS_HOME_PAYLOAD) in root_buttons

    for key in [
        "post_visit_review",
        "cancellation_return",
        "birthday",
        "repeat_visit",
        "anti_spam",
        "review_links",
        "quiet_hours",
    ]:
        buttons = _buttons(settings_automation_module_keyboard(key))
        assert ("⬅️ Назад", SETTINGS_BACK_PAYLOAD) in buttons
        assert ("🏠 Главное меню", SETTINGS_HOME_PAYLOAD) in buttons


def test_cta_payload_prefixes_unchanged() -> None:
    assert LOST_CLIENTS_BOOKING_PAYLOAD_PREFIX == "lost_clients:book:"
    assert REPEAT_VISIT_BOOKING_PAYLOAD_PREFIX == "repeat_visit:book:"


def _raw_app_setting(db, key: str) -> str | None:
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def test_ui_writes_plain_runtime_keys_not_prefixed(db) -> None:
    repo = AppSettingsRepository(str(db))
    upsert_automation_setting("lost_clients", {"enabled": False}, repo)
    upsert_automation_setting("review_links", {"yandex_url": "https://reviews.example/y", "two_gis_url": ""}, repo)
    upsert_automation_setting("birthday", {"send_days_before": 5}, repo)

    assert _raw_app_setting(db, "lost_clients") is not None
    assert _raw_app_setting(db, "review_links") is not None
    assert _raw_app_setting(db, "birthday") is not None
    assert _raw_app_setting(db, "automation:lost_clients") is None
    assert _raw_app_setting(db, "automation:review_links") is None
    assert _raw_app_setting(db, "automation:birthday") is None


def test_prefixed_key_read_fallback_is_not_source_of_truth(db) -> None:
    repo = AppSettingsRepository(str(db))
    repo.set_json("automation:lost_clients", {"enabled": False, "threshold_days": [45, 60, 90]})
    assert get_automation_setting("lost_clients", repo)["enabled"] is False
    upsert_automation_setting("lost_clients", {"enabled": True}, repo)
    assert _raw_app_setting(db, "lost_clients") is not None
    assert get_automation_setting("lost_clients", repo)["enabled"] is True


def test_invalid_json_safely_returns_default(db) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", ("birthday", "not-json"))
        conn.commit()
    setting = get_automation_setting("birthday", AppSettingsRepository(str(db)))
    assert setting["enabled"] is True
    assert setting["send_days_before"] == 7


def test_runtime_readers_use_plain_app_settings_keys(db, monkeypatch) -> None:
    from max_barbershop_bot.services.birthday_funnel import _birthday_settings
    from max_barbershop_bot.services.lost_clients import _load_anti_spam_settings, _load_lost_clients_settings
    from max_barbershop_bot.services.feedback import feedback_review_links_keyboard

    repo = AppSettingsRepository(str(db))
    upsert_automation_setting("birthday", {"enabled": False, "send_days_before": 3, "message_text": "Коротко"}, repo)
    upsert_automation_setting("lost_clients", {"enabled": False, "threshold_days": [15, 30, 45]}, repo)
    upsert_automation_setting("anti_spam", {"enabled": False, "min_interval_hours": 24}, repo)
    upsert_automation_setting("review_links", {"yandex_url": "https://reviews.example/y", "two_gis_url": "https://reviews.example/2"}, repo)

    assert _birthday_settings(str(db))["enabled"] is False
    assert _birthday_settings(str(db))["send_days_before"] == 3
    assert _load_lost_clients_settings(str(db))["threshold_days"] == [15, 30, 45]
    assert _load_lost_clients_settings(str(db))["enabled"] is False
    assert _load_anti_spam_settings(str(db))["min_interval_hours"] == 24
    links = feedback_review_links_keyboard(str(db))
    assert links.rows[0][0].url == "https://reviews.example/y"
    assert links.rows[1][0].url == "https://reviews.example/2"


@pytest.mark.parametrize("raw, expected", [("1", True), ('{"enabled": false}', False), ('{"enabled": true}', True)])
def test_bool_reader_accepts_legacy_bool_and_json_enabled(db, raw: str, expected: bool) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", ("birthday", raw))
        conn.commit()
    assert AppSettingsRepository(str(db)).get_bool("birthday", default=True) is expected


def test_repeat_and_cancellation_runtime_read_plain_settings_without_sending(db) -> None:
    from max_barbershop_bot.services.cancellation_recovery import process_due_cancellation_recovery_events
    from max_barbershop_bot.services.repeat_visit import process_due_repeat_visit_events

    repo = AppSettingsRepository(str(db))
    upsert_automation_setting("repeat_visit", {"enabled": False}, repo)
    upsert_automation_setting("cancellation_return", {"enabled": False}, repo)
    sender = FakeSender()

    assert asyncio.run(process_due_repeat_visit_events(sender, database_path=str(db))) == 0
    assert asyncio.run(process_due_cancellation_recovery_events(sender, database_path=str(db))) == 0
    assert sender.sent == []
