"""No-network smoke check for MAX user display-name persistence."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from max_barbershop_bot.db.sqlite import init_database
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UsersRepository
from max_barbershop_bot.services.registration import save_registration_profile
from max_barbershop_bot.services.user_names import resolve_user_display_name
from max_barbershop_bot.ui.screens import main_menu_screen


def _assert_menu_contains(user: object | None, profile_name: str | None, expected_name: str) -> None:
    display_name = resolve_user_display_name(user, profile_name)
    screen = main_menu_screen(display_name=display_name)
    expected = f"✨ {expected_name}, выберите действие в меню ниже 👇"
    assert expected in screen.text, screen.text


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        database_path = str(Path(tmpdir) / "smoke.sqlite3")
        init_database(database_path)
        repository = UsersRepository(database_path)

        repository.create_or_update_user(
            platform=PLATFORM_MAX,
            platform_user_id="accepted-profile",
            first_name="Ilya",
            chat_id="chat-1",
        )
        save_registration_profile(
            repository,
            platform_user_id="accepted-profile",
            phone="+79990000001",
            first_name="Ilya",
            birthdate="1990-01-01",
        )
        user = repository.find_by_platform_user_id("accepted-profile")
        assert user is not None
        assert resolve_user_display_name(user) == "Ilya"
        _assert_menu_contains(user, None, "Ilya")

        repository.create_or_update_user(
            platform=PLATFORM_MAX,
            platform_user_id="manual-name",
            first_name=None,
            chat_id="chat-2",
        )
        save_registration_profile(
            repository,
            platform_user_id="manual-name",
            phone="+79990000002",
            first_name="Илья",
            birthdate="1991-02-03",
        )
        user = repository.find_by_platform_user_id("manual-name")
        assert user is not None
        _assert_menu_contains(user, None, "Илья")

        repository.create_or_update_user(
            platform=PLATFORM_MAX,
            platform_user_id="accepted-profile",
            first_name="   ",
            last_name=None,
            username=None,
            chat_id="chat-1",
        )
        user = repository.find_by_platform_user_id("accepted-profile")
        assert user is not None
        assert resolve_user_display_name(user) == "Ilya"
        _assert_menu_contains(user, "", "Ilya")

        _assert_menu_contains(None, None, "Пользователь")


if __name__ == "__main__":
    main()
