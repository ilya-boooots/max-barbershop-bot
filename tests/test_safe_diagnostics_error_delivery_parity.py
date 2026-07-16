from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core import error_handler
from max_barbershop_bot.core.config import Config
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.diagnostics import DiagnosticsRepository
from max_barbershop_bot.services.diagnostics import GENERIC_ERROR_TEXT


@dataclass
class Sender:
    chats: list[tuple[int, str, object]] = field(default_factory=list)
    users: list[tuple[int, str, object]] = field(default_factory=list)

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.chats.append((chat_id, text, keyboard))
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.users.append((user_id, text, keyboard))
        return SimpleNamespace(ok=True, status_code=200, error_code=None)

    async def answer_callback(self, callback_id: str):
        return None


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "diagnostics.sqlite3"
    init_database(str(path))
    monkeypatch.setenv("DATABASE_PATH", str(path))
    error_handler._last_alert_by_fingerprint.clear()
    error_handler._alert_timestamps.clear()
    return path


def _event(user_id: str, payload: str) -> NormalizedEvent:
    return NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user_id,
        max_user_id=user_id,
        chat_id=str(700 + int(user_id)),
        text=None,
        callback_payload=payload,
        callback_id=f"callback-{user_id}",
        first_name="Иван",
    )


def _error_logs(db: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM bot_logs WHERE source = 'error_handler' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


def test_safe_error_real_router_handler_sends_generic_user_text_masked_dev_alert_and_persists_every_repeat(
    db: Path,
) -> None:
    config = Config(max_bot_token="unused", dev_max_user_id="999", database_path=str(db))
    router = Router(config)

    async def failing_real_handler(context):
        raise RuntimeError(
            "Authorization: Bearer super-secret-token-12345678901234567890 "
            "phone +79991234567 payload internal-client-42"
        )

    router.on_callback("danger:internal-client-42", failing_real_handler)
    sender = Sender()
    asyncio.run(router.dispatch(_event("1", "danger:internal-client-42"), sender))
    asyncio.run(router.dispatch(_event("2", "danger:internal-client-42"), sender))

    assert [text for _, text, _ in sender.chats] == [GENERIC_ERROR_TEXT, GENERIC_ERROR_TEXT]
    for _, _, keyboard in sender.chats:
        assert [(button.text, button.payload) for row in keyboard.rows for button in row] == [
            ("🏠 Главное меню", "nav:home")
        ]
    assert len(sender.users) == 1
    developer_text = sender.users[0][1]
    assert sender.users[0][0] == 999
    assert "<payload_hidden>" in developer_text
    assert "super-secret" not in developer_text
    assert "+79991234567" not in developer_text
    assert "danger:internal-client-42" not in developer_text
    assert "internal-client-42" not in developer_text
    assert "<message_hidden>" in developer_text and "<traceback_hidden>" in developer_text
    assert "timestamp_utc:" in developer_text

    rows = _error_logs(db)
    assert len(rows) == 2
    details = "\n".join(str(row["details_json"]) for row in rows)
    assert "<payload_hidden>" in details
    assert "super-secret" not in details and "+79991234567" not in details
    assert "internal-client-42" not in details


def test_safe_error_repeated_suppression_expires_after_telegram_cooldown(
    db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    monkeypatch.setattr(error_handler.time, "monotonic", lambda: now)
    assert error_handler._alert_allowed("same") is True
    assert error_handler._alert_allowed("same") is False
    now += 599.9
    assert error_handler._alert_allowed("same") is False
    now += 0.1
    assert error_handler._alert_allowed("same") is True


def test_safe_error_global_burst_suppression_limits_developer_delivery_to_three_per_minute(
    db: Path,
) -> None:
    sender = Sender()
    diagnostics = error_handler.ErrorDiagnostics(dev_max_user_id="999")
    for index in range(4):
        asyncio.run(
            diagnostics.handle_runtime_exception(
                exception=RuntimeError(f"safe failure {index}"),
                sender=sender,
                location=f"runtime-{index}",
            )
        )
    assert len(sender.users) == 3
    assert not sender.chats
    assert len(_error_logs(db)) == 4


@pytest.mark.parametrize(
    ("diagnostics", "expected_alerts"),
    [
        (error_handler.ErrorDiagnostics(dev_max_user_id=None), 0),
        (error_handler.ErrorDiagnostics(dev_max_user_id="999", enabled=False), 0),
        (error_handler.ErrorDiagnostics(dev_max_user_id="999", enabled=True), 1),
    ],
)
def test_safe_error_developer_delivery_is_config_gated_and_never_replaces_generic_user_error(
    db: Path,
    diagnostics: error_handler.ErrorDiagnostics,
    expected_alerts: int,
) -> None:
    sender = Sender()
    asyncio.run(
        diagnostics.handle_handler_exception(
            exception=RuntimeError("safe test"),
            event=_event("3", "test:error"),
            sender=sender,
            handler_name="safe_test_handler",
        )
    )
    assert sender.chats[0][1] == GENERIC_ERROR_TEXT
    assert len(sender.users) == expected_alerts
    assert len(_error_logs(db)) == 1


def test_safe_error_repository_masks_nested_tokens_phones_payloads_and_bounds_output(db: Path) -> None:
    repository = DiagnosticsRepository(str(db))
    repository.log_error_event(
        {
            "exception_class": "RuntimeError",
            "location": "test",
            "token": "secret-token-value",
            "phone": "+79991234567",
            "callback_payload": "Authorization=Bearer abcdefghijklmnopqrstuvwxyz123456",
            "extra": {"user_token": "hidden-value", "message": "+79997654321"},
        }
    )
    row = _error_logs(db)[0]
    details = json.loads(row["details_json"])
    serialized = json.dumps(details, ensure_ascii=False)
    assert details["token"] == "***"
    assert details["phone"] == "<contact_hidden>"
    assert "secret-token-value" not in serialized
    assert "+79991234567" not in serialized and "+79997654321" not in serialized
    assert len(row["message"]) <= 2000
