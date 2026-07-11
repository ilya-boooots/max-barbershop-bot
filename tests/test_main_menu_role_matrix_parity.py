"""Focused PR-032 main menu role matrix parity checks."""

from __future__ import annotations

import inspect
from pathlib import Path

from max_barbershop_bot.core.permissions import (
    ROLE_ADMIN,
    ROLE_DEVELOPER,
    ROLE_MANAGER,
    ROLE_USER,
    can_view_broadcasts,
    can_view_staff,
    can_view_statistics,
    can_view_yclients,
)
from max_barbershop_bot.flows import settings as settings_flow
from max_barbershop_bot.ui import buttons as max_buttons
from max_barbershop_bot.ui.buttons import main_menu_keyboard

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_BY_ROLE = {
    ROLE_USER: [
        "✂️ Записаться",
        "📅 Мои записи",
        "📍 Контакты",
        "🆘 Поддержка",
        "⚙️ Настройки",
    ],
    ROLE_MANAGER: [
        "✂️ Записаться",
        "📅 Мои записи",
        "📍 Контакты",
        "🆘 Поддержка",
        "📊 Статистика",
        "👥 Персонал",
        "⚙️ Настройки",
        "📣 Рассылка",
        "⚙️ Интеграция YClients",
    ],
    ROLE_ADMIN: [
        "✂️ Записаться",
        "📅 Мои записи",
        "📍 Контакты",
        "🆘 Поддержка",
        "👥 Персонал",
        "⚙️ Настройки",
        "📣 Рассылка",
    ],
    ROLE_DEVELOPER: [
        "✂️ Записаться",
        "📅 Мои записи",
        "📍 Контакты",
        "🆘 Поддержка",
        "📊 Статистика",
        "👥 Персонал",
        "⚙️ Настройки",
        "📣 Рассылка",
        "🛠️ Разработка: Диагностика",
        "⚙️ Интеграция YClients",
    ],
}


def _button_texts(role: str) -> list[str]:
    return [button.text for row in main_menu_keyboard(role).rows for button in row]


def _button_payloads(role: str) -> list[str]:
    return [button.payload or "" for row in main_menu_keyboard(role).rows for button in row]


def test_plan_contains_pr_032_topic() -> None:
    plan = (ROOT / "docs/max_telegram_parity_plan_v2.md").read_text(encoding="utf-8")
    assert "PR-032 — Main menu role matrix parity" in plan


def test_user_menu_matches_telegram_excluding_owner_approved_loyalty_gap() -> None:
    assert _button_texts(ROLE_USER) == EXPECTED_BY_ROLE[ROLE_USER]
    privileged = {"📊 Статистика", "👥 Персонал", "📣 Рассылка", "⚙️ Интеграция YClients", "🛠️ Разработка: Диагностика", "🧪 Админ-панель"}
    assert privileged.isdisjoint(_button_texts(ROLE_USER))


def test_manager_menu_matches_telegram_excluding_loyalty() -> None:
    assert _button_texts(ROLE_MANAGER) == EXPECTED_BY_ROLE[ROLE_MANAGER]


def test_admin_menu_matches_telegram_excluding_loyalty_and_admin_denials() -> None:
    assert _button_texts(ROLE_ADMIN) == EXPECTED_BY_ROLE[ROLE_ADMIN]
    assert "📊 Статистика" not in _button_texts(ROLE_ADMIN)
    assert "⚙️ Интеграция YClients" not in _button_texts(ROLE_ADMIN)


def test_developer_menu_matches_telegram_excluding_loyalty_and_hidden_dev_admin_panel() -> None:
    assert _button_texts(ROLE_DEVELOPER) == EXPECTED_BY_ROLE[ROLE_DEVELOPER]
    assert "🧪 Админ-панель" not in _button_texts(ROLE_DEVELOPER)


def test_top_level_permission_helpers_match_telegram_staff_permissions() -> None:
    assert [role for role in EXPECTED_BY_ROLE if can_view_statistics(role)] == [ROLE_MANAGER, ROLE_DEVELOPER]
    assert [role for role in EXPECTED_BY_ROLE if can_view_staff(role)] == [ROLE_MANAGER, ROLE_ADMIN, ROLE_DEVELOPER]
    assert [role for role in EXPECTED_BY_ROLE if can_view_broadcasts(role)] == [ROLE_MANAGER, ROLE_ADMIN, ROLE_DEVELOPER]
    assert [role for role in EXPECTED_BY_ROLE if can_view_yclients(role)] == [ROLE_MANAGER, ROLE_DEVELOPER]


def test_yclients_label_and_visibility_match_telegram() -> None:
    assert "⚙️ Интеграция YClients" in _button_texts(ROLE_MANAGER)
    assert "⚙️ Интеграция YClients" in _button_texts(ROLE_DEVELOPER)
    assert "⚙️ Интеграция YClients" not in _button_texts(ROLE_ADMIN)
    assert "⚙️ Интеграция YClients" not in _button_texts(ROLE_USER)
    assert "🧩 YClients" not in _button_texts(ROLE_MANAGER)
    assert "🧩 YClients" not in _button_texts(ROLE_DEVELOPER)


def test_developer_diagnostics_uses_existing_real_settings_handler_payload() -> None:
    assert "🛠️ Разработка: Диагностика" in _button_texts(ROLE_DEVELOPER)
    assert max_buttons.SETTINGS_DIAGNOSTICS_PAYLOAD in _button_payloads(ROLE_DEVELOPER)
    assert "🛠️ Разработка: Диагностика" not in _button_texts(ROLE_MANAGER)
    assert "🛠️ Разработка: Диагностика" not in _button_texts(ROLE_ADMIN)
    assert "🛠️ Разработка: Диагностика" not in _button_texts(ROLE_USER)
    register_source = inspect.getsource(settings_flow.register_settings_routes)
    assert "router.on_callback(SETTINGS_DIAGNOSTICS_PAYLOAD, handle_settings_diagnostics)" in register_source


def test_visible_menu_payloads_are_existing_non_placeholder_payloads() -> None:
    expected_payloads = {
        max_buttons.MENU_BOOKING_PAYLOAD,
        max_buttons.MENU_MY_BOOKINGS_PAYLOAD,
        max_buttons.MENU_CONTACTS_PAYLOAD,
        max_buttons.MENU_SUPPORT_PAYLOAD,
        max_buttons.ADMIN_SETTINGS_PAYLOAD,
        max_buttons.ADMIN_STATISTICS_PAYLOAD,
        max_buttons.ADMIN_STAFF_PAYLOAD,
        max_buttons.ADMIN_BROADCASTS_PAYLOAD,
        max_buttons.ADMIN_YCLIENTS_PAYLOAD,
        max_buttons.SETTINGS_DIAGNOSTICS_PAYLOAD,
    }
    for role in EXPECTED_BY_ROLE:
        payloads = _button_payloads(role)
        assert all(payload in expected_payloads for payload in payloads)
        assert all("placeholder" not in payload and "soon" not in payload for payload in payloads)


def test_owner_approved_loyalty_gap_has_no_max_button_or_payload() -> None:
    # Owner decision for PR-032: Telegram's optional loyalty button is out of scope
    # because MAX has no real top-level loyalty handler yet.
    for role in EXPECTED_BY_ROLE:
        assert "🎁 Система лояльности" not in _button_texts(role)
        assert all("loyalty" not in payload.lower() for payload in _button_payloads(role))


def test_scope_safety_no_aiogram_or_role_assignment_in_pr_032_targets() -> None:
    target_paths = [
        ROOT / "max_barbershop_bot/flows/menu.py",
        ROOT / "max_barbershop_bot/core/permissions.py",
        ROOT / "max_barbershop_bot/ui/buttons.py",
        ROOT / "max_barbershop_bot/ui/texts.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in target_paths)
    assert "from aiogram" not in combined
    assert "import aiogram" not in combined
    menu_source = (ROOT / "max_barbershop_bot/flows/menu.py").read_text(encoding="utf-8")
    for forbidden in ("assign_role", "role_onboarding", "set_role", "update_role"):
        assert forbidden not in menu_source
