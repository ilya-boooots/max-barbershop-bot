from aiogram.types import InlineKeyboardMarkup

from app.services import booking_reminders
from app.handlers import notifications
from app.handlers.my_bookings import _main_actions_kb, _cancel_confirm_kb, _history_kb, _all_cards_kb
from app.keyboards import booking as booking_kb, reports, staff


def _collect_callbacks(markup: InlineKeyboardMarkup):
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data is not None:
                out.append(btn.callback_data)
    return out


def _assert_callbacks_valid(callbacks):
    for cb in callbacks:
        assert isinstance(cb, str) and cb.strip()
        assert len(cb.encode("utf-8")) <= 64, cb


def test_callback_data_length_across_keyboards():
    markups = [
        notifications.broadcast_root_kb(378881880),
        notifications.one_time_audience_kb(),
        notifications.segment_root_kb(),
        notifications.preview_kb(True),
        notifications.dev_tests_kb(),
        notifications.automation_root_kb(),
        notifications.efficiency_kb(90),
        booking_reminders._reminder_2h_kb(),
        booking_reminders._confirm_kb(123456),
        _main_actions_kb(has_active=True, show_all=True),
        _cancel_confirm_kb(),
        _history_kb(page=3, has_next=True),
        _all_cards_kb(index=1, total=5, record_id="1234567890"),
        booking_kb.build_calendar(2026, 5, __import__("datetime").date(2026,5,1)),
        booking_kb.build_time_keyboard([__import__("datetime").time(10,0)]),
        reports.reports_menu_kb(),
        staff.broadcast_segment_kb(),
    ]
    all_callbacks = []
    for kb in markups:
        all_callbacks.extend(_collect_callbacks(kb))
    _assert_callbacks_valid(all_callbacks)
