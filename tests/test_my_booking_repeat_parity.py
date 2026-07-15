from pathlib import Path

from max_barbershop_bot.ui.buttons import (
    MY_BOOKINGS_REPEAT_START_PAYLOAD,
    my_booking_active_card_keyboard,
    my_booking_details_keyboard,
    my_bookings_empty_keyboard,
    my_bookings_history_keyboard,
)


def _buttons(keyboard):
    return [(button.text, button.payload) for row in keyboard.rows for button in row]


def test_pr037_plan_and_telegram_reference_prove_repeat_scope():
    plan = Path("docs/max_telegram_parity_plan_v2.md").read_text(encoding="utf-8")
    tg_my = Path("telegram_reference/app/handlers/my_bookings.py").read_text(encoding="utf-8")
    tg_booking = Path("telegram_reference/app/handlers/booking_flow.py").read_text(encoding="utf-8")

    assert "PR-037 — My booking repeat parity" in plan
    assert "Repeat from detail/history and prefill handoff" in plan
    assert "repeat visit automation" in plan
    assert "CB_REPEAT" in tg_my and "CB_HISTORY_REPEAT" in tg_my
    assert "_start_prefilled_date_flow" in tg_my
    assert "selected_service_id" in tg_my and "selected_staff_id" in tg_my
    assert "staff_id or \"0\"" in tg_booking


def test_repeat_button_visible_on_telegram_equivalent_cards():
    for keyboard in (
        my_booking_details_keyboard(can_cancel=True, is_active=True, can_reschedule=True),
        my_booking_details_keyboard(can_cancel=False, is_active=False, can_reschedule=False),
        my_booking_active_card_keyboard(index=0, total=1, can_cancel=True, can_reschedule=True),
        my_bookings_history_keyboard(page=0, has_next=False, include_repeat=True),
        my_bookings_empty_keyboard(show_history=True),
    ):
        assert any(payload == MY_BOOKINGS_REPEAT_START_PAYLOAD for _, payload in _buttons(keyboard))


def test_repeat_router_and_scope_symbols_are_real():
    source = Path("max_barbershop_bot/flows/my_bookings.py").read_text(encoding="utf-8")
    booking = Path("max_barbershop_bot/flows/booking.py").read_text(encoding="utf-8")
    assert "router.on_callback(MY_BOOKINGS_REPEAT_START_PAYLOAD, handle_my_booking_repeat_start)" in source
    assert "async def handle_my_booking_repeat_start" in source
    assert "async def start_repeat_booking_with_prefill" in booking
    assert "handle_repeat_visit_booking_start" in booking
    assert "aiogram" not in Path("max_barbershop_bot/flows/my_bookings.py").read_text(encoding="utf-8")
