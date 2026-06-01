from __future__ import annotations

import pytest

from app.handlers.booking_flow import _append_lost_client_discount_comment
from app.services.lost_clients import build_lost_client_booking_keyboard
from app.services.repeat_visit import build_repeat_visit_booking_keyboard

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("days", [30, 60, 90])
def test_lost_client_keyboard_has_single_booking_button(days: int) -> None:
    kb = build_lost_client_booking_keyboard(days)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    button = kb.inline_keyboard[0][0]
    assert button.text == "✂️ Записаться"
    assert button.callback_data == f"lost_clients:book:{days}"
    assert len(button.callback_data.encode("utf-8")) <= 64


def test_repeat_visit_keyboard_has_single_booking_button() -> None:
    kb = build_repeat_visit_booking_keyboard(123)
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 1
    button = kb.inline_keyboard[0][0]
    assert button.text == "✂️ Записаться"
    assert button.callback_data == "repeat_visit:book:123"
    assert len(button.callback_data.encode("utf-8")) <= 64


def test_lost_client_comment_appended_once() -> None:
    base = "Клиент записался из телеграм бота 20.05.2026 в 17:36"
    first = _append_lost_client_discount_comment(base, booking_origin_type="lost_client", lost_days=60)
    second = _append_lost_client_discount_comment(first, booking_origin_type="lost_client", lost_days=60)
    expected = "Клиент не посещал 60 дней. НУЖНО СДЕЛАТЬ СКИДКУ"
    assert expected in first
    assert second == first


def test_lost_client_comment_not_appended_for_repeat_or_normal() -> None:
    base = "Клиент записался из телеграм бота 20.05.2026 в 17:36"
    assert _append_lost_client_discount_comment(base, booking_origin_type="repeat_visit", lost_days=30) == base
    assert _append_lost_client_discount_comment(base, booking_origin_type=None, lost_days=None) == base


@pytest.mark.parametrize("days", [30, 60, 90])
def test_lost_client_comment_appended_for_allowed_thresholds(days: int) -> None:
    base = "Клиент записался из телеграм бота 20.05.2026 в 17:36"
    result = _append_lost_client_discount_comment(base, booking_origin_type="lost_client", lost_days=days)
    assert result.startswith(base)
    assert f"Клиент не посещал {days} дней. НУЖНО СДЕЛАТЬ СКИДКУ" in result


def test_lost_client_comment_not_appended_for_unsupported_days() -> None:
    base = "Клиент записался из телеграм бота 20.05.2026 в 17:36"
    assert _append_lost_client_discount_comment(base, booking_origin_type="lost_client", lost_days=45) == base
