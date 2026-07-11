import asyncio
from pathlib import Path

from max_barbershop_bot.core import state
from max_barbershop_bot.core.events import NormalizedEvent
from max_barbershop_bot.core.router import RouterContext
from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.flows import registration as registration_flow
from max_barbershop_bot.flows import start as start_flow
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserCreate, UsersRepository
from max_barbershop_bot.services.registration import is_registered, validate_birthdate, validate_name
from max_barbershop_bot.ui.texts import (
    REGISTRATION_BIRTHDATE_INVALID_TEXT,
    REGISTRATION_BIRTHDATE_TEXT,
    REGISTRATION_COMPLETE_TEXT,
    REGISTRATION_NAME_INVALID_TEXT,
    REGISTRATION_NAME_TEXT,
)


class FakeSender:
    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_to_chat(self, chat_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard, attachments))

    async def send_to_user(self, user_id, text, keyboard=None, attachments=None):
        self.messages.append((text, keyboard, attachments))

    async def answer_callback(self, callback_id):
        self.callbacks.append(callback_id)


def _event(text="/start", user="42", chat="100500", first_name="Max", last_name=None):
    return NormalizedEvent(
        update_type="message_created",
        platform_user_id=user,
        max_user_id=user,
        chat_id=chat,
        text=text,
        callback_payload=None,
        callback_id=None,
        first_name=first_name,
        last_name=last_name,
        username="maxuser",
    )


def _ctx(text="/start", user="42", chat="100500", first_name="Max"):
    sender = FakeSender()
    return RouterContext(event=_event(text=text, user=user, chat=chat, first_name=first_name), sender=sender), sender


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "bot.sqlite3"
    init_database(str(db))
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setattr(registration_flow.asyncio, "sleep", _no_sleep)
    state.clear_user_state("42", "100500")
    state.clear_user_state("77", "100500")
    state.clear_user_state("88", "100500")
    return db


async def _no_sleep(_seconds):
    return None


def _texts(sender):
    return [message[0] for message in sender.messages]


def test_plan_contains_pr_031_topic():
    assert "PR-031 — Registration first-run parity" in Path("docs/max_telegram_parity_plan_v2.md").read_text()


def test_owner_decision_name_birthdate_only_and_phone_not_required(tmp_path):
    db = tmp_path / "bot.sqlite3"
    init_database(str(db))
    repo = UsersRepository(str(db))
    user = repo.create(UserCreate(platform_user_id="42", first_name="Иван", birthdate="1999-01-31"))

    assert is_registered(user)
    assert user.phone is None


def test_new_user_start_opens_name_registration_not_menu(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    ctx, sender = _ctx(first_name="Пользователь")

    asyncio.run(start_flow.handle_start(ctx))

    assert _texts(sender) == [REGISTRATION_NAME_TEXT]
    assert state.get_current_screen("42", "100500") == state.REGISTRATION_NAME_SCREEN
    assert UsersRepository(str(db)).find_by_platform_user_id("42").birthdate is None
    assert "выберите действие" not in sender.messages[0][0].lower()


def test_invalid_name_rejected_and_not_persisted(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    ctx, sender = _ctx(text="/start", first_name="Пользователь")
    asyncio.run(start_flow.handle_start(ctx))

    bad_ctx, _ = _ctx(text=" А ", first_name="Пользователь")
    bad_ctx = RouterContext(event=bad_ctx.event, sender=sender)
    asyncio.run(registration_flow.handle_name_input(bad_ctx))

    user = UsersRepository(str(db)).find_by_platform_user_id("42")
    assert sender.messages[-1][0] == REGISTRATION_NAME_INVALID_TEXT
    assert user.first_name is None
    assert not is_registered(user)


def test_valid_name_then_invalid_birthdate_does_not_persist(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    ctx, sender = _ctx(text="  Иван  ", first_name="Пользователь")
    asyncio.run(start_flow.handle_start(RouterContext(event=_event(first_name="Пользователь"), sender=sender)))
    asyncio.run(registration_flow.handle_name_input(ctx))

    assert sender.messages[-1][0] == REGISTRATION_BIRTHDATE_TEXT
    assert state.get_current_screen("42", "100500") == state.REGISTRATION_BIRTHDATE_SCREEN

    bad_ctx, _ = _ctx(text="31-01-1999", first_name="Пользователь")
    asyncio.run(registration_flow.handle_birthdate_input(RouterContext(event=bad_ctx.event, sender=sender)))
    user = UsersRepository(str(db)).find_by_platform_user_id("42")

    assert sender.messages[-1][0] == REGISTRATION_BIRTHDATE_INVALID_TEXT
    assert user.birthdate is None
    assert user.first_name is None
    assert not is_registered(user)


def test_birthdate_validation_rejects_future_and_before_1900():
    assert validate_birthdate("31.01.1999").birthdate == "1999-01-31"
    assert not validate_birthdate("31-01-1999").is_valid
    assert not validate_birthdate("31.02.1999").is_valid
    assert not validate_birthdate("01.01.1899").is_valid
    assert not validate_birthdate("01.01.2999").is_valid


def test_complete_registration_persists_name_birthdate_without_phone_and_shows_menu(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    sender = FakeSender()
    asyncio.run(start_flow.handle_start(RouterContext(event=_event(first_name="Пользователь"), sender=sender)))
    asyncio.run(registration_flow.handle_name_input(RouterContext(event=_event(text="  Иван  ", first_name="Пользователь"), sender=sender)))
    asyncio.run(registration_flow.handle_birthdate_input(RouterContext(event=_event(text="31.01.1999", first_name="Пользователь"), sender=sender)))

    user = UsersRepository(str(db)).find_by_platform_user_id("42")
    assert user.first_name == "Иван"
    assert user.display_name == "Иван"
    assert user.birthdate == "1999-01-31"
    assert user.phone is None
    assert is_registered(user)
    assert REGISTRATION_COMPLETE_TEXT in _texts(sender)
    assert any("Иван" in text and "выберите действие" in text for text in _texts(sender))
    assert state.get_current_screen("42", "100500") == state.MAIN_MENU_SCREEN


def test_registered_start_does_not_restart_without_phone(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    repo = UsersRepository(str(db))
    repo.create(UserCreate(platform=PLATFORM_MAX, platform_user_id="77", max_user_id="77", chat_id="100500", first_name="Иван", display_name="Иван", birthdate="1999-01-31"))
    ctx, sender = _ctx(user="77", first_name="Иван")

    asyncio.run(start_flow.handle_start(ctx))

    assert all(text != REGISTRATION_NAME_TEXT for text in _texts(sender))
    assert any("Иван" in text and "выберите действие" in text for text in _texts(sender))
    assert UsersRepository(str(db)).find_by_platform_user_id("77").phone is None


def test_duplicate_start_does_not_create_duplicate_rows(monkeypatch, tmp_path):
    db = _setup(monkeypatch, tmp_path)
    sender = FakeSender()
    asyncio.run(start_flow.handle_start(RouterContext(event=_event(user="88", first_name="Пользователь"), sender=sender)))
    asyncio.run(start_flow.handle_start(RouterContext(event=_event(user="88", first_name="Пользователь"), sender=sender)))

    users = UsersRepository(str(db)).list_all_users()
    assert [user.platform_user_id for user in users].count("88") == 1


def test_removed_steps_not_shown_and_scope_markers_absent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sender = FakeSender()
    asyncio.run(start_flow.handle_start(RouterContext(event=_event(first_name="Пользователь"), sender=sender)))
    asyncio.run(registration_flow.handle_name_input(RouterContext(event=_event(text="Иван", first_name="Пользователь"), sender=sender)))
    all_text = "\n".join(_texts(sender)).lower()

    assert "телефон" not in all_text
    assert "контакт" not in all_text
    assert "пол" not in all_text
    assert "соглас" not in all_text
    assert "бонус" not in all_text
    assert all(message[1] is None for message in sender.messages if message[0] in {REGISTRATION_NAME_TEXT, REGISTRATION_BIRTHDATE_TEXT})


def test_no_aiogram_imports_in_max_package():
    for path in Path("max_barbershop_bot").rglob("*.py"):
        text = path.read_text()
        assert "from aiogram" not in text
        assert "import aiogram" not in text


def test_validate_name_trims_minimum_and_rejects_placeholder():
    assert validate_name("  Иван  ") == "Иван"
    assert validate_name("А") is None
    assert validate_name("Пользователь") is None
