from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from max_barbershop_bot.repositories.users import PLATFORM_MAX, User
from max_barbershop_bot.services import my_bookings as mb
from max_barbershop_bot.ui import buttons

TZ = "Europe/Moscow"
NOW = datetime(2026, 7, 3, 12, 0, tzinfo=ZoneInfo(TZ))
USER = User(1, PLATFORM_MAX, "u1", "u1", "c1", None, None, None, None, "+79198332692", None, "user", "123", True)


def future_iso(days: int = 1) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def past_iso() -> str:
    return (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def base_record(**overrides):
    record = {
        "id": "r1",
        "datetime": future_iso(),
        "services": [{"id": "s1", "title": "Стрижка", "price": 1200}],
        "staff": {"id": "m1", "name": "Максим"},
        "client": {"id": "123", "phone": "+79198332692"},
        "attendance": "0",
    }
    record.update(overrides)
    return record


def visible(records, *, client_id="123", phones={"79198332692"}):
    rows, counts = mb._filter_owned_active_rows(
        records,
        yclients_client_id=client_id,
        attributed_record_ids=[],
        known_phones=phones,
        timezone_name=TZ,
        now=NOW,
    )
    bookings = [mb._booking_from_payload(row, timezone_name=TZ) for row in rows]
    return [booking for booking in bookings if booking is not None], counts


def test_valid_future_minimal_scoped_record_renders_without_client_payload() -> None:
    records = mb._mark_rows_request_context(
        [{"id": "r1", "datetime": future_iso(), "attendance": "0"}],
        request_client_id="123",
        request_phone=None,
    )

    bookings, counts = visible(records)

    assert counts["hidden_not_owned_count"] == 0
    assert len(bookings) == 1
    assert "Услуга" in mb.format_bookings_screen(bookings, timezone_name=TZ)


def test_record_missing_services_uses_fallback_service_name() -> None:
    record = base_record()
    record.pop("services")
    bookings, _ = visible([record])
    assert bookings[0].service_name == "Услуга"


def test_record_missing_staff_does_not_crash() -> None:
    record = base_record()
    record.pop("staff")
    bookings, _ = visible([record])
    assert bookings[0].master_name is None


def test_record_missing_client_matches_by_scoped_request_context() -> None:
    record = mb._mark_rows_request_context([{"id": "r1", "datetime": future_iso()}], request_client_id="123", request_phone=None)[0]
    bookings, _ = visible([record])
    assert len(bookings) == 1


def test_future_unknown_or_missing_status_is_not_rejected() -> None:
    bookings_unknown, _ = visible([base_record(status="mystery")])
    bookings_missing, _ = visible([base_record()])
    assert len(bookings_unknown) == 1
    assert len(bookings_missing) == 1


def test_cancelled_or_deleted_records_are_hidden() -> None:
    bookings, counts = visible([base_record(id="c", status="cancelled"), base_record(id="d", deleted=True)])
    assert bookings == []
    assert counts["hidden_cancelled_count"] == 1
    assert counts["hidden_deleted_count"] == 1


def test_past_record_is_hidden() -> None:
    bookings, counts = visible([base_record(datetime=past_iso())])
    assert bookings == []
    assert counts["hidden_past_count"] == 1


def test_empty_yclients_list_is_empty_state_not_error() -> None:
    bookings, counts = visible([])
    assert bookings == []
    assert counts["owned_records_count"] == 0


def test_user_without_phone_and_client_id_has_no_identity_mode() -> None:
    assert mb._request_mode(None, set()) == "no_identity"


def test_one_malformed_record_does_not_hide_valid_record() -> None:
    bookings, counts = visible([base_record(id="bad", datetime=None), base_record(id="good")])
    assert [booking.yclients_record_id for booking in bookings] == ["good"]
    assert counts["hidden_parse_error_count"] == 1


def test_client_id_int_and_str_match() -> None:
    bookings, _ = visible([base_record(client={"id": 123})], client_id="123")
    assert len(bookings) == 1


def test_phone_formats_match() -> None:
    expected = {"79198332692"}
    for raw_phone in ["+79198332692", "79198332692", "89198332692", "9198332692"]:
        assert mb._normalize_phone_digits(raw_phone) in expected
    for raw_phone in ["+79198332692", "79198332692", "89198332692", "9198332692"]:
        bookings, _ = visible([base_record(client={"phone": raw_phone})], client_id=None, phones=expected)
        assert len(bookings) == 1


def test_active_empty_state_text_and_buttons_match_telegram_root() -> None:
    keyboard = buttons.my_bookings_empty_keyboard()

    assert mb.MY_BOOKINGS_EMPTY_TEXT == "📭 У вас пока нет активных записей."
    assert [[button.text for button in row] for row in keyboard.rows] == [
        ["🔁 Повторить запись"],
        ["🕘 История визитов"],
        ["⬅️ Назад"],
        ["🏠 Главное меню"],
    ]
    assert [[button.payload for button in row] for row in keyboard.rows] == [
        [buttons.MY_BOOKINGS_REPEAT_START_PAYLOAD],
        [f"{buttons.MY_BOOKINGS_HISTORY_PAYLOAD_PREFIX}0"],
        [buttons.NAV_BACK_PAYLOAD],
        [buttons.NAV_HOME_PAYLOAD],
    ]


def test_active_one_future_booking_root_card_matches_telegram_format() -> None:
    bookings, _ = visible([base_record(status="active", duration=3600)])

    text = mb.format_booking_details_text(bookings[0], timezone_name=TZ, title="📅 Моя ближайшая запись")
    keyboard = buttons.my_booking_entry_keyboard(show_all=False)

    assert text.splitlines() == [
        "📅 Моя ближайшая запись",
        "",
        "✂️ Услуга: Стрижка",
        "👤 Мастер: Максим",
        "📅 Дата: 04.07.2026",
        "🕒 Время: 12:00",
        "⏳ Длительность: 60 мин",
        "💰 Цена: 1200 ₽",
        "📍 Адрес: —",
        "📞 Контакты: —",
        "🧾 Статус: Подтверждена",
    ]
    assert [[button.text for button in row] for row in keyboard.rows] == [
        ["🔁 Перенести запись"],
        ["❌ Отменить запись"],
        ["🔁 Повторить запись"],
        ["🕘 История визитов"],
        ["⬅️ Назад"],
        ["🏠 Главное меню"],
    ]


def test_active_multiple_future_bookings_show_all_button_matches_telegram_order() -> None:
    keyboard = buttons.my_booking_entry_keyboard(show_all=True)

    assert [[button.text for button in row] for row in keyboard.rows] == [
        ["🔁 Перенести запись"],
        ["❌ Отменить запись"],
        ["🔁 Повторить запись"],
        ["📋 Показать все активные записи"],
        ["🕘 История визитов"],
        ["⬅️ Назад"],
        ["🏠 Главное меню"],
    ]


def test_active_cancelled_deleted_and_past_are_absent_like_telegram() -> None:
    bookings, counts = visible(
        [
            base_record(id="future", status="active"),
            base_record(id="cancelled", status="cancelled"),
            base_record(id="deleted", deleted=True),
            base_record(id="past", datetime=past_iso()),
        ]
    )

    assert [booking.yclients_record_id for booking in bookings] == ["future"]
    assert counts["hidden_cancelled_count"] == 1
    assert counts["hidden_deleted_count"] == 1
    assert counts["hidden_past_count"] == 1


def test_active_ownership_by_client_id_phone_and_local_attribution_matches_telegram_order() -> None:
    client_owned = base_record(id="client", client={"id": "123", "phone": "+70000000000"})
    phone_owned = base_record(id="phone", client={"id": "999", "phone": "+79198332692"})
    attributed_owned = base_record(id="linked", client={"id": "777", "phone": "+70000000001"})
    other = base_record(id="other", client={"id": "999", "phone": "+70000000002"})

    assert mb.ownership_source_for_record(client_owned, yclients_client_id="123", attributed_record_ids=set(), known_phones=set()) == "client_id"
    assert mb.ownership_source_for_record(phone_owned, yclients_client_id=None, attributed_record_ids=set(), known_phones={"79198332692"}) == "phone"
    assert mb.ownership_source_for_record(attributed_owned, yclients_client_id=None, attributed_record_ids={"linked"}, known_phones=set()) == "attribution"
    assert mb.ownership_source_for_record(other, yclients_client_id="123", attributed_record_ids=set(), known_phones={"79198332692"}) == "none"

    rows, counts = mb._filter_owned_active_rows(
        [client_owned, phone_owned, attributed_owned, other],
        yclients_client_id="123",
        attributed_record_ids=["linked"],
        known_phones={"79198332692"},
        timezone_name=TZ,
        now=NOW,
    )

    assert [row["id"] for row in rows] == ["client", "phone", "linked"]
    assert counts["hidden_not_owned_count"] == 1


def test_active_carousel_pagination_buttons_match_telegram() -> None:
    first = buttons.my_booking_active_card_keyboard(index=0, total=3)
    middle = buttons.my_booking_active_card_keyboard(index=1, total=3)
    stale_high = buttons.my_booking_active_card_keyboard(index=99, total=3)

    assert [button.text for button in first.rows[0]] == ["1/3", "▶️"]
    assert [button.payload for button in first.rows[0]] == [
        f"{buttons.MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}0",
        f"{buttons.MY_BOOKINGS_ACTIVE_PAGE_PAYLOAD_PREFIX}1",
    ]
    assert [button.text for button in middle.rows[0]] == ["◀️", "2/3", "▶️"]
    assert [button.text for button in stale_high.rows[0]] == ["◀️", "3/3"]
    assert [[button.text for button in row] for row in middle.rows[1:]] == [
        ["🔁 Перенести"],
        ["❌ Отменить"],
        ["🔁 Повторить"],
        ["⬅️ Назад"],
        ["🏠 Главное меню"],
    ]
