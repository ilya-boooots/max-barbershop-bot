import asyncio
import sqlite3
from pathlib import Path

from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import broadcasts
from max_barbershop_bot.repositories.omnichannel_broadcasts import OmnichannelBroadcastRepository
from max_barbershop_bot.ui.buttons import (
    BROADCAST_HOME_PAYLOAD,
    BROADCAST_HISTORY_DETAIL_PREFIX,
    BROADCAST_HISTORY_LIST_PREFIX,
    BROADCAST_HISTORY_ROOT_PAYLOAD,
)


class FakeSender:
    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_to_chat(self, chat_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard, attachments))

    async def send_to_user(self, user_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard, attachments))

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def _ctx(payload: str, user: str = "42"):
    sender = FakeSender()
    event = NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user,
        max_user_id=user,
        chat_id="100500",
        text=None,
        callback_payload=payload,
        callback_id="cb-1",
    )
    return RouterContext(event=event, sender=sender), sender


def _payloads(keyboard):
    return [button.payload for row in keyboard.rows for button in row]


def _texts(keyboard):
    return [button.text for row in keyboard.rows for button in row]


def _seed(db_path: Path):
    repo = OmnichannelBroadcastRepository(str(db_path))
    repo.upsert_broadcast(
        broadcast_id="old",
        origin_platform="max",
        text="Старый текст",
        attachment_type=None,
        attachment=None,
        created_by_user_id="42",
        status="sent",
    )
    repo.upsert_broadcast(
        broadcast_id="new",
        origin_platform="max",
        text="Новый текст",
        attachment_type="photo",
        attachment={"id": "masked"},
        created_by_user_id="42",
        status="sent",
    )
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE omnichannel_broadcasts SET audience_source='self_test', created_at='2026-01-01 10:00:00', finished_at='2026-01-01 10:01:00' WHERE broadcast_id='old'")
        con.execute("UPDATE omnichannel_broadcasts SET audience_source='yclients_all_clients', created_at='2026-01-02 10:00:00', finished_at='2026-01-02 10:01:00' WHERE broadcast_id='new'")
    repo.add_delivery(broadcast_id="new", yclients_client_id="1", selected_platform="max", platform_user_id="u1", delivery_status="sent", reason=None, origin_platform="max", priority_decision=None, sent=True)
    repo.add_delivery(broadcast_id="new", yclients_client_id="2", selected_platform="max", platform_user_id="u2", delivery_status="skipped_no_tg_id", reason="нет Telegram ID", origin_platform="max", priority_decision=None)
    repo.add_delivery(broadcast_id="new", yclients_client_id="3", selected_platform="max", platform_user_id="u3", delivery_status="failed", reason="masked", origin_platform="max", priority_decision=None)
    repo.add_delivery(broadcast_id="new", yclients_client_id="4", selected_platform="max", platform_user_id="u4", delivery_status="blocked", reason="blocked", origin_platform="max", priority_decision=None)
    repo.add_delivery(broadcast_id="old", yclients_client_id="5", selected_platform="max", platform_user_id="u5", delivery_status="sent", reason=None, origin_platform="max", priority_decision=None, sent=True)
    return repo


def test_plan_contains_pr_030_topic():
    assert "PR-030 — Broadcast history/effectiveness parity baseline" in Path("docs/max_telegram_parity_plan_v2.md").read_text()


def test_role_access_for_history_root(monkeypatch, tmp_path):
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(tmp_path / "db.sqlite3"))
    for role in ("admin", "manager", "developer"):
        monkeypatch.setattr(broadcasts, "_actor_role", lambda context, role=role: role)
        ctx, sender = _ctx("broadcast:history")
        asyncio.run(broadcasts.handle_history_section(ctx))
        assert sender.messages[-1][0].startswith("📜 История уведомлений")
    monkeypatch.setattr(broadcasts, "_actor_role", lambda context: "user")
    ctx, sender = _ctx("broadcast:history", user="7")
    asyncio.run(broadcasts.handle_history_section(ctx))
    assert sender.messages[-1][0] == "⛔️ Недостаточно прав."


def test_history_root_telegram_filters_and_navigation(monkeypatch, tmp_path):
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(tmp_path / "db.sqlite3"))
    monkeypatch.setattr(broadcasts, "_actor_role", lambda context: "admin")
    ctx, sender = _ctx("broadcast:history")
    asyncio.run(broadcasts.handle_history_section(ctx))
    text, keyboard, _ = sender.messages[-1]
    assert "результат доставки" in text
    assert "📋 Все уведомления" in _texts(keyboard)
    assert "✉️ Ручные рассылки" in _texts(keyboard)
    assert BROADCAST_HOME_PAYLOAD in _payloads(keyboard)


def test_history_list_recent_first_empty_state_and_counts(monkeypatch, tmp_path):
    db_path = tmp_path / "db.sqlite3"
    _seed(db_path)
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(db_path))
    monkeypatch.setattr(broadcasts, "_actor_role", lambda context: "manager")
    ctx, sender = _ctx(f"{BROADCAST_HISTORY_LIST_PREFIX}manual_broadcast:1")
    asyncio.run(broadcasts.handle_history_list(ctx))
    text, keyboard, _ = sender.messages[-1]
    assert text.index("Рассылка #new") < text.index("Рассылка #old")
    assert "Отправлено: 1" in text
    assert "Ошибок: 1" in text
    assert "Заблокировали бота: 1" in text
    assert "Пропущено: 1" in text
    assert f"{BROADCAST_HISTORY_DETAIL_PREFIX}new" in _payloads(keyboard)

    empty_db = tmp_path / "empty.sqlite3"
    OmnichannelBroadcastRepository(str(empty_db))
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(empty_db))
    ctx, sender = _ctx(f"{BROADCAST_HISTORY_LIST_PREFIX}manual_broadcast:1")
    asyncio.run(broadcasts.handle_history_list(ctx))
    assert "Пока записей нет." in sender.messages[-1][0]


def test_history_detail_counts_stale_and_back_home(monkeypatch, tmp_path):
    db_path = tmp_path / "db.sqlite3"
    _seed(db_path)
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(db_path))
    monkeypatch.setattr(broadcasts, "_actor_role", lambda context: "developer")
    ctx, sender = _ctx(f"{BROADCAST_HISTORY_DETAIL_PREFIX}new")
    asyncio.run(broadcasts.handle_history_detail(ctx))
    text, keyboard, _ = sender.messages[-1]
    assert "Аудитория: 👥 Все клиенты" in text
    assert "Всего клиентов: 4" in text
    assert "Отправлено: 1" in text
    assert "Ошибок: 1" in text
    assert "Заблокировали бота: 1" in text
    assert "Пропущено: 1" in text
    assert "Медиа: photo" in text
    assert BROADCAST_HOME_PAYLOAD in _payloads(keyboard)
    assert f"{BROADCAST_HISTORY_LIST_PREFIX}manual_broadcast:1" in _payloads(keyboard)

    ctx, sender = _ctx(f"{BROADCAST_HISTORY_DETAIL_PREFIX}missing")
    asyncio.run(broadcasts.handle_history_detail(ctx))
    assert "история устарела" in sender.messages[-1][0]


def test_filters_effectiveness_and_scope_safety(monkeypatch, tmp_path):
    db_path = tmp_path / "db.sqlite3"
    _seed(db_path)
    monkeypatch.setattr(broadcasts, "_database_path", lambda: str(db_path))
    monkeypatch.setattr(broadcasts, "_actor_role", lambda context: "admin")
    ctx, sender = _ctx(f"{BROADCAST_HISTORY_LIST_PREFIX}post_visit_rating:1")
    asyncio.run(broadcasts.handle_history_list(ctx))
    assert "Пока записей нет." in sender.messages[-1][0]

    ctx, sender = _ctx("broadcast:effectiveness")
    asyncio.run(broadcasts.handle_effectiveness_section(ctx))
    text = sender.messages[-1][0]
    assert "Отправлено уведомлений:" in text
    assert "Клики" in text
    assert "конверсия" in text
    assert "не показаны" in text
    assert not hasattr(sender, "network_called")
    assert "aiogram" not in Path("max_barbershop_bot/flows/broadcasts.py").read_text()
