from pathlib import Path

from max_barbershop_bot.flows.clients_directory import NO_ACCESS_TEXT
from max_barbershop_bot.repositories.app_settings import AUTOMATION_SETTINGS_DEFAULTS
from max_barbershop_bot.services.contacts import ContactInfo, format_contacts_text
from max_barbershop_bot.services.feedback import FEEDBACK_ADMIN_REPLY_CLIENT_TEXT
from max_barbershop_bot.ui.buttons import main_menu_keyboard
from max_barbershop_bot.ui.texts import (
    BOOKING_HUB_TEXT,
    BOOKING_MASTER_TEXT,
    REPEAT_VISIT_FALLBACK_TEXT,
    STAFF_LIST_EMPTY_TEXT,
    STAFF_LIST_TITLE_TEXT,
    START_GREETING_TEXT,
)


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_BEAUTY_COPY = (
    "барбершоп",
    "барбер",
    "💈",
    "✂️",
    "стрижк",
    "бород",
    "брить",
    "мужск",
)


def _assert_neutral(text: str) -> None:
    normalized = text.casefold()
    assert not [token for token in FORBIDDEN_BEAUTY_COPY if token in normalized]


def test_active_python_copy_has_no_barbershop_specific_terms() -> None:
    for path in (ROOT / "max_barbershop_bot").rglob("*.py"):
        _assert_neutral(path.read_text(encoding="utf-8"))


def test_core_runtime_screens_use_neutral_salon_master_service_copy() -> None:
    contacts_text = format_contacts_text(
        ContactInfo(address="ул. Тестовая, 1", phone="+79990000000", schedule="10:00–20:00")
    )
    runtime_texts = [
        START_GREETING_TEXT,
        BOOKING_HUB_TEXT,
        BOOKING_MASTER_TEXT,
        REPEAT_VISIT_FALLBACK_TEXT,
        STAFF_LIST_TITLE_TEXT,
        STAFF_LIST_EMPTY_TEXT,
        NO_ACCESS_TEXT,
        FEEDBACK_ADMIN_REPLY_CLIENT_TEXT,
        contacts_text,
    ]
    runtime_texts.extend(str(value) for value in AUTOMATION_SETTINGS_DEFAULTS.values())
    runtime_texts.extend(button.text for row in main_menu_keyboard("user").rows for button in row)

    for text in runtime_texts:
        _assert_neutral(text)

    assert "салона красоты" in START_GREETING_TEXT
    assert "📍 Контакты салона" in contacts_text
    assert "✨ Записаться" in [button.text for row in main_menu_keyboard("user").rows for button in row]
