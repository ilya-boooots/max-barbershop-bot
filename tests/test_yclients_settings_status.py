from __future__ import annotations

from max_barbershop_bot.flows.yclients_settings import _settings_status_text
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
