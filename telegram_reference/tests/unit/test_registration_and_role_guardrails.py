from app.handlers.start import _is_developer_user, _parse_birthdate
from app.services.role_onboarding import role_onboarding_keyboard


def test_parse_birthdate_accepts_valid_date():
    assert _parse_birthdate("31.01.1999") == "1999-01-31"


def test_parse_birthdate_rejects_invalid_date():
    assert _parse_birthdate("31-01-1999") is None
    assert _parse_birthdate("31.02.1999") is None


def test_parse_birthdate_rejects_future_and_too_old():
    assert _parse_birthdate("01.01.1899") is None
    assert _parse_birthdate("01.01.2999") is None


def test_protected_developer_detected_even_with_non_dev_db_role():
    assert _is_developer_user(378881880, "user") is True


def test_role_onboarding_callback_data_fits_telegram_limit():
    kb = role_onboarding_keyboard(role="manager", step=10, target_tg_id=378881880)
    for row in kb.inline_keyboard:
        for button in row:
            if button.callback_data is None:
                continue
            assert len(button.callback_data.encode("utf-8")) <= 64
