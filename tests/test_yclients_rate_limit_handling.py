from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.flows import my_bookings as my_bookings_flow
from max_barbershop_bot.integrations.yclients.exceptions import YClientsRateLimitError
from max_barbershop_bot.services import my_bookings as mb


def test_yclients_429_retry_after_zero_uses_safe_fallback() -> None:
    exc = YClientsRateLimitError(
        "Превышен лимит запросов, попробуйте повторить запрос через 0 секунд.",
        status_code=429,
        endpoint="/api/v1/records/123",
    )

    assert exc.retry_after_seconds > 0
    assert exc.retry_after_seconds == 5
    assert exc.endpoint_name == "list_user_bookings"
    assert exc.safe_short_message == "YClients rate limit"


class _FakeYClients:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, str | None]] = []

    async def get_client_records(self, **kwargs):
        self.calls.append((kwargs.get("yclients_client_id"), kwargs.get("phone")))
        return {"data": self.pages.pop(0) if self.pages else []}

    async def get_booking_details(self, **kwargs):  # pragma: no cover - should not be called in this test
        raise AssertionError("booking details should not be fetched")


def test_user_with_client_id_and_phone_does_not_duplicate_records_call_when_client_id_has_rows() -> None:
    async def _run() -> None:
        yclients = _FakeYClients([[{"id": "r1", "client_id": "c1"}]])

        rows = await mb._fetch_all_relevant_records(
            yclients,  # type: ignore[arg-type]
            company_id="1",
            yclients_client_id="c1",
            phones={"79990000000"},
            attributed_record_ids=[],
            start_date="2026-07-04",
            end_date="2026-07-05",
        )

        assert rows
        assert yclients.calls == [("c1", None)]

    asyncio.run(_run())


def test_my_bookings_rate_limit_diagnostic_has_no_traceback() -> None:
    diagnostic = mb._rate_limit_diagnostic(
        function="MyBookingsService.get_bookings_for_user",
        max_user_id="u1",
        user=None,
        endpoint_name="list_user_bookings",
        request_mode="by_both",
        retry_after_seconds=5,
        duration_ms=10,
    )

    assert diagnostic["error_type"] == "YClientsRateLimitError"
    assert diagnostic["error_category"] == "rate_limit"
    assert "traceback_last_5_lines" not in diagnostic

class _RuntimeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None):
        self.messages.append((text, keyboard))

    async def answer_callback(self, callback_id: str):
        self.callbacks.append(callback_id)


def test_real_my_bookings_rate_limit_handler_never_sends_diagnostic_to_developer_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = mb._rate_limit_diagnostic(
        function="MyBookingsService.get_bookings_for_user",
        max_user_id="295169373",
        user=None,
        endpoint_name="list_user_bookings",
        request_mode="by_phone",
        retry_after_seconds=5,
        duration_ms=3682,
    )

    async def raise_rate_limit(_service, _user, *, platform_user_id=None):
        raise mb.MyBookingsLoadError(mb.MY_BOOKINGS_RATE_LIMIT_TEXT, diagnostic=diagnostic)

    monkeypatch.setattr(mb.MyBookingsService, "get_bookings_for_user", raise_rate_limit)
    monkeypatch.setattr(
        my_bookings_flow,
        "_current_user",
        lambda _context: SimpleNamespace(role="developer"),
    )

    sender = _RuntimeSender()
    context = RouterContext(
        event=NormalizedEvent(
            update_type="message_callback",
            platform_user_id="295169373",
            max_user_id="295169373",
            chat_id="900",
            text=None,
            callback_payload="menu:my_bookings",
            callback_id="cb-my-bookings",
        ),
        sender=sender,
    )

    asyncio.run(my_bookings_flow.handle_my_bookings_open(context))

    assert sender.callbacks == ["cb-my-bookings"]
    assert [text for text, _ in sender.messages] == [
        "⏳ Загружаю ваши записи…",
        mb.MY_BOOKINGS_RATE_LIMIT_TEXT,
    ]
    keyboard = sender.messages[-1][1]
    assert [(button.text, button.payload) for row in keyboard.rows for button in row] == [
        ("🔄 Повторить", "menu:my_bookings"),
        ("⬅️ Назад", "nav:back"),
        ("🏠 Главное меню", "nav:home"),
    ]
    assert all("diagnostic" not in text.lower() for text, _ in sender.messages)
    assert all("295169373" not in text for text, _ in sender.messages)

