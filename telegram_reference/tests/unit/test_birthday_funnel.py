from __future__ import annotations

import pytest

from app.services.birthday_funnel import (
    BIRTHDAY_MESSAGE_TEXT,
    BIRTHDAY_WARNING,
    apply_birthday_warning,
    build_birthday_booking_keyboard,
)

pytestmark = pytest.mark.unit


def test_birthday_message_text_is_current_ux_copy() -> None:
    assert BIRTHDAY_MESSAGE_TEXT == (
        "Скоро ваш день рождения, поздравляем 🎉 😊\n\n"
        "Хотим сделать вам приятный подарок - покажите это сообщение администратору при оплате."
    )


def test_apply_birthday_warning_appends_to_birthday_funnel_comment_once() -> None:
    base_comment = "Клиент записался из телеграм бота 20.05.2026 в 17:36"

    first = apply_birthday_warning(
        base_comment,
        booking_source="birthday_funnel",
        birthday_discount_context=True,
    )
    second = apply_birthday_warning(
        first,
        booking_source="birthday_funnel",
        birthday_discount_context=True,
    )

    assert first == f"{base_comment}\n\n{BIRTHDAY_WARNING}"
    assert second == first


def test_apply_birthday_warning_does_not_touch_normal_or_other_funnel_comments() -> None:
    base_comment = "Клиент записался из телеграм бота 20.05.2026 в 17:36"

    assert apply_birthday_warning(base_comment, booking_source=None, birthday_discount_context=False) == base_comment
    assert apply_birthday_warning(
        base_comment,
        booking_source="lost_client",
        birthday_discount_context=True,
    ) == base_comment
    assert apply_birthday_warning(
        base_comment,
        booking_source="birthday_funnel",
        birthday_discount_context=False,
    ) == base_comment


def test_birthday_notification_keyboard_contains_only_booking_button() -> None:
    keyboard = build_birthday_booking_keyboard(42)

    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 1
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "✂️ Записаться"
    assert button.callback_data == "birthday_funnel:book:42"
