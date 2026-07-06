from __future__ import annotations

from max_barbershop_bot.flows.yclients_settings import _friendly_check_reason, _settings_status_text
from max_barbershop_bot.integrations.yclients.exceptions import (
    YCLIENTS_ERROR_AUTH,
    YCLIENTS_ERROR_CREDENTIALS,
    YCLIENTS_ERROR_RATE_LIMIT,
    YCLIENTS_ERROR_SERVER,
    YCLIENTS_ERROR_TRANSPORT,
    sanitize_yclients_diagnostic,
    sanitize_yclients_endpoint,
)
from max_barbershop_bot.repositories.yclients_settings import YClientsSettings
from max_barbershop_bot.ui.buttons import yclients_settings_keyboard


def _button_texts(keyboard) -> list[str]:
    return [button.text for row in keyboard.rows for button in row]


def test_yclients_settings_empty_status_matches_telegram_meaning() -> None:
    text = _settings_status_text(None)

    assert text == (
        "⚙️ Интеграция YClients\n\n"
        "Статус: ❌ Не подключено\n"
        "🏢 Company ID: —\n"
        "🔐 Partner token: —\n"
        "👤 User token: —\n"
        "🌐 Base URL: по умолчанию"
    )


def test_yclients_settings_saved_status_masks_tokens_and_has_no_raw_secrets() -> None:
    settings = YClientsSettings(
        company_id="123456789",
        partner_token="partner-secret-token",
        user_token="user-secret-token",
        branch_timezone="Europe/Moscow",
        branch_title="Центр",
    )

    text = _settings_status_text(settings)

    assert "Статус: ✅ Подключено" in text
    assert "🏢 Company ID: 123***89" in text
    assert "🔐 Partner token: par***en" in text
    assert "👤 User token: use***en" in text
    assert "🌐 Base URL: по умолчанию" in text
    assert "partner-secret-token" not in text
    assert "user-secret-token" not in text
    assert "Europe/Moscow" not in text
    assert "Центр" not in text


def test_yclients_settings_buttons_match_telegram_role_variants() -> None:
    assert _button_texts(yclients_settings_keyboard(can_manage=True)) == [
        "🧩 Настроить / Изменить",
        "🔌 Проверить подключение",
        "🧹 Сбросить настройки",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]
    assert _button_texts(yclients_settings_keyboard(can_manage=False)) == [
        "🔌 Проверить подключение",
        "⬅️ Назад",
        "🏠 Главное меню",
    ]


def test_yclients_connection_check_error_reasons_match_telegram_texts() -> None:
    assert _friendly_check_reason(YCLIENTS_ERROR_AUTH) == "🔐 Не удалось авторизоваться. Проверьте токены и попробуйте снова."
    assert _friendly_check_reason(YCLIENTS_ERROR_RATE_LIMIT) == "⏳ Слишком много запросов. Попробуйте повторить немного позже."
    assert _friendly_check_reason(YCLIENTS_ERROR_SERVER) == "🛠️ Сервис YClients временно недоступен. Повторите позже."
    assert _friendly_check_reason(YCLIENTS_ERROR_TRANSPORT) == "🌐 Не удалось связаться с YClients. Проверьте URL и интернет-соединение."
    assert _friendly_check_reason(YCLIENTS_ERROR_CREDENTIALS) == "⚠️ Не хватает обязательных полей. Начните настройку заново."


def test_yclients_diagnostics_mask_tokens_authorization_payload_and_url() -> None:
    raw_token = "partner-secret-token-1234567890"
    raw_user_token = "user-secret-token-1234567890"
    text = sanitize_yclients_diagnostic(
        "Authorization: Bearer "
        + raw_token
        + ", User "
        + raw_user_token
        + " payload={'partner_token':'"
        + raw_token
        + "','phone':'+79991234567'}"
    )
    endpoint = sanitize_yclients_endpoint(f"/api/v1/company/123?partner_token={raw_token}&page=1&user_token={raw_user_token}")

    assert raw_token not in text
    assert raw_user_token not in text
    assert raw_token not in endpoint
    assert raw_user_token not in endpoint
    assert "Authorization: ***" in text
    assert "***phone***" in text
    assert "partner_token=%2A%2A%2A" in endpoint or "partner_token=***" in endpoint
    assert "user_token=%2A%2A%2A" in endpoint or "user_token=***" in endpoint
