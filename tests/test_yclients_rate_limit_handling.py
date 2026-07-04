from __future__ import annotations

import asyncio

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
