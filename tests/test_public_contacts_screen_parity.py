from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.flows import contacts as contacts_flow
from max_barbershop_bot.services.contacts import (
    ContactInfo,
    ContactsService,
    contact_info_from_override,
    contact_info_from_yclients,
    fallback_contact_info,
    format_contacts_text,
    merge_contact_info,
)
from max_barbershop_bot.ui.buttons import (
    MENU_CONTACTS_PAYLOAD,
    NAV_BACK_PAYLOAD,
    NAV_HOME_PAYLOAD,
    contacts_keyboard,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.callbacks: list[str] = []

    async def send_to_chat(self, chat_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def send_to_user(self, user_id: int, text: str, *, keyboard=None, attachments=None) -> None:
        self.messages.append({"user_id": user_id, "text": text, "keyboard": keyboard, "attachments": attachments})

    async def answer_callback(self, callback_id: str) -> None:
        self.callbacks.append(callback_id)


def _event(payload: str = MENU_CONTACTS_PAYLOAD, *, user: str = "100", chat: str = "200") -> NormalizedEvent:
    return NormalizedEvent(
        update_type="message_callback",
        platform_user_id=user,
        max_user_id=user,
        chat_id=chat,
        text=None,
        callback_payload=payload,
        callback_id="cb-1",
    )


def _button_texts(keyboard) -> list[str]:
    return [button.text for row in keyboard.rows for button in row]


def _button_payloads(keyboard) -> list[str | None]:
    return [button.payload for row in keyboard.rows for button in row]


def test_plan_and_active_telegram_contacts_reference_proof() -> None:
    plan = (ROOT / "docs/max_telegram_parity_plan_v2.md").read_text(encoding="utf-8")
    sections = (ROOT / "telegram_reference/app/handlers/sections.py").read_text(encoding="utf-8")
    navigation = (ROOT / "telegram_reference/app/ui/navigation.py").read_text(encoding="utf-8")

    assert "PR-033 — Public contacts screen parity" in plan
    handle_contacts = sections[sections.index("async def handle_contacts") : sections.index("@router.message(F.text == SUPPORT_BTN)")]
    assert "resolve_contacts()" in handle_contacts
    assert "render_contacts_block(contacts.resolved)" in handle_contacts
    assert "nav_inline_kb()" in handle_contacts
    nav_inline = navigation[navigation.index("def nav_inline_kb") :]
    assert "InlineKeyboardButton(text=BACK" in nav_inline
    assert "InlineKeyboardButton(text=HOME" in nav_inline
    assert "Яндекс" not in nav_inline
    assert "2GIS" not in nav_inline
    assert "Google" not in nav_inline
    assert "Позвонить" not in nav_inline


def test_exact_public_contacts_text() -> None:
    text = format_contacts_text(
        ContactInfo(
            address="ул. Примерная, 1",
            phone="+7 999 000-00-00",
            schedule="Ежедневно 10:00–22:00",
        )
    )
    assert text == (
        "📍 Контакты Барбершоп\n\n"
        "🏠 Адрес: ул. Примерная, 1\n"
        "📞 Телефон: +7 999 000-00-00\n"
        "⏰ Режим работы: Ежедневно 10:00–22:00"
    )


def test_missing_values_render_dash() -> None:
    assert format_contacts_text(ContactInfo()) == (
        "📍 Контакты Барбершоп\n\n"
        "🏠 Адрес: —\n"
        "📞 Телефон: —\n"
        "⏰ Режим работы: —"
    )


def test_full_override_wins_over_yclients() -> None:
    resolved = merge_contact_info(
        override=contact_info_from_override(
            {"address": "Адрес вручную", "phone": "+7 ручной", "schedule": "По записи"}
        ),
        fallback=ContactInfo(address="Адрес API", phone="+7 API", schedule="10-20", source="yclients"),
    )
    assert (resolved.address, resolved.phone, resolved.schedule) == ("Адрес вручную", "+7 ручной", "По записи")


def test_partial_override_is_field_by_field() -> None:
    resolved = merge_contact_info(
        override=contact_info_from_override({"address": "Адрес вручную"}),
        fallback=ContactInfo(address="Адрес API", phone="+7 API", schedule="10-20", source="yclients"),
    )
    assert (resolved.address, resolved.phone, resolved.schedule) == ("Адрес вручную", "+7 API", "10-20")


def test_empty_override_falls_back_to_yclients() -> None:
    resolved = merge_contact_info(
        override=contact_info_from_override({"address": "   ", "phone": "", "schedule": "\t"}),
        fallback=ContactInfo(address="Адрес API", phone="+7 API", schedule="10-20", source="yclients"),
    )
    assert (resolved.address, resolved.phone, resolved.schedule) == ("Адрес API", "+7 API", "10-20")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": {"address": "Полный адрес"}}, ("Полный адрес", None, None)),
        ({"data": {"short_address": "Короткий адрес"}}, ("Короткий адрес", None, None)),
        ({"data": {"phone": "+7 111"}}, (None, "+7 111", None)),
        ({"data": {"phones": [{"number": "+7 111"}, {"phone": "+7 222"}]}}, (None, "+7 111, +7 222", None)),
        ({"data": {"schedule": "schedule"}}, (None, None, "schedule")),
        ({"data": {"schedule_text": "schedule_text"}}, (None, None, "schedule_text")),
        ({"data": {"work_time": "work_time"}}, (None, None, "work_time")),
        ({"data": {"working_hours": "working_hours"}}, (None, None, "working_hours")),
        ({"data": {"work_schedule": "work_schedule"}}, (None, None, "work_schedule")),
    ],
)
def test_yclients_payload_extraction(payload: dict[str, object], expected: tuple[str | None, str | None, str | None]) -> None:
    info = contact_info_from_yclients(payload)
    assert (info.address, info.phone, info.schedule) == expected
    assert "{" not in (info.phone or "")
    assert "}" not in (info.phone or "")


def test_yclients_failure_safe_fallback_keeps_override_values(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repo:
        def get_contacts_override(self) -> dict[str, str]:
            return {"address": "Адрес вручную"}

    async def fail_yclients(self: ContactsService) -> ContactInfo:
        return fallback_contact_info()

    monkeypatch.setattr(ContactsService, "_get_yclients_contacts", fail_yclients)
    info = asyncio.run(ContactsService(Repo()).get_contacts())  # type: ignore[arg-type]
    text = format_contacts_text(info)

    assert "🏠 Адрес: Адрес вручную" in text
    assert "📞 Телефон: —" in text
    assert "⏰ Режим работы: —" in text
    forbidden = ["token", "Traceback", "trace_id", "raw", "response", "YClientsError", "secret"]
    assert not any(value in text for value in forbidden)


def test_public_contacts_keyboard_is_back_home_only() -> None:
    keyboard = contacts_keyboard()
    assert _button_texts(keyboard) == ["⬅️ Назад", "🏠 Главное меню"]
    assert _button_payloads(keyboard) == [NAV_BACK_PAYLOAD, NAV_HOME_PAYLOAD]
    forbidden = [
        "Яндекс Карты",
        "2GIS",
        "Google Maps",
        "Позвонить",
        "website",
        "Telegram",
        "Instagram",
        "Редактировать контакты",
        "Предпросмотр",
        "Сбросить",
    ]
    assert not any(text in _button_texts(keyboard) for text in forbidden)


def test_router_registers_menu_contacts_payload_to_real_handler() -> None:
    router = Router()
    contacts_flow.register_contacts_routes(router)
    assert router._callback_handlers[MENU_CONTACTS_PAYLOAD] is contacts_flow.handle_contacts  # noqa: SLF001


def test_opening_contacts_sets_screen_and_pushes_previous_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_contacts(self: ContactsService) -> ContactInfo:
        return ContactInfo(address="ул. Примерная, 1", phone="+7 999", schedule="10-22")

    monkeypatch.setattr(ContactsService, "get_contacts", fake_get_contacts)
    user = "100033"
    chat = "200033"
    state.clear_user_state(user, chat)
    state.set_current_screen(user, chat, state.MAIN_MENU_SCREEN)
    sender = FakeSender()
    context = RouterContext(_event(user=user, chat=chat), sender)  # type: ignore[arg-type]

    asyncio.run(contacts_flow.handle_contacts(context))
    first_stack = list(state._get_state(user, chat).screen_stack)  # noqa: SLF001
    asyncio.run(contacts_flow.handle_contacts(context))
    second_stack = list(state._get_state(user, chat).screen_stack)  # noqa: SLF001

    assert state.get_current_screen(user, chat) == state.CONTACTS_SCREEN
    assert first_stack == [state.MAIN_MENU_SCREEN]
    assert second_stack == first_stack
    assert sender.messages[-1]["text"] == (
        "📍 Контакты Барбершоп\n\n"
        "🏠 Адрес: ул. Примерная, 1\n"
        "📞 Телефон: +7 999\n"
        "⏰ Режим работы: 10-22"
    )
    assert _button_texts(sender.messages[-1]["keyboard"]) == ["⬅️ Назад", "🏠 Главное меню"]


def test_scope_safety_forbidden_files_and_aiogram_imports() -> None:
    changed = set(
        __import__("subprocess")
        .check_output(["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True)
        .splitlines()
    )
    assert not any(path.startswith("telegram_reference/") for path in changed)
    assert "max_barbershop_bot/flows/settings.py" not in changed
    assert not any(path.startswith("max_barbershop_bot/flows/support") for path in changed)
    assert not any("main_menu" in path and path.startswith("max_barbershop_bot/") for path in changed)
    output = __import__("subprocess").run(
        ["rg", "from aiogram|import aiogram", "max_barbershop_bot"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert output.stdout == ""
