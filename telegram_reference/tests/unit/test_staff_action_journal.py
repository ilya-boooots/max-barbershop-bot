from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.staff_permissions import can_view_personnel
from app.db.sqlite import execute
from app.repositories.staff_action_logs import (
    count_staff_action_logs,
    get_staff_action_logs,
    log_role_assigned,
    log_role_removed,
    log_staff_action,
)
from app.repositories.users import upsert_telegram_user
from app.utils.datetime import format_datetime_in_timezone
from tests.sync import run


FORBIDDEN_UI_FRAGMENTS = ("staff:assign", "callback_data", "{", "}", "SELECT ", "Traceback")


def _seed_user(tg_id: int, name: str, role: str = "user") -> None:
    run(upsert_telegram_user(tg_id=tg_id, username=None, phone="", name=name))
    run(execute("UPDATE users SET role = ?, display_name = ? WHERE user_id = ?", (role, name, tg_id)))


def test_role_assignment_creates_human_readable_target_log(initialized_db):
    _seed_user(1001, "Илья", "developer")
    _seed_user(2002, "Иван", "user")

    run(
        log_role_assigned(
            actor_tg_id=1001,
            actor_role="developer",
            target_tg_id=2002,
            target_name="Иван",
            new_role="manager",
            old_role="user",
        )
    )

    rows = run(get_staff_action_logs(2002))
    assert run(count_staff_action_logs(2002)) == 1
    assert rows[0]["action_type"] == "role_assigned"
    assert rows[0]["human_text"] == "Разработчик Илья назначил роль «Управляющий» пользователю Иван."
    assert all(fragment not in rows[0]["human_text"] for fragment in FORBIDDEN_UI_FRAGMENTS)


def test_role_removal_creates_human_readable_target_log(initialized_db):
    _seed_user(1001, "Илья", "developer")
    _seed_user(2002, "Иван", "admin")

    run(
        log_role_removed(
            actor_tg_id=1001,
            actor_role="developer",
            target_tg_id=2002,
            target_name="Иван",
            old_role="admin",
        )
    )

    rows = run(get_staff_action_logs(2002))
    assert rows[0]["action_type"] == "role_removed"
    assert rows[0]["human_text"] == "Разработчик Илья снял роль «Администратор» с пользователя Иван."
    assert all(fragment not in rows[0]["human_text"] for fragment in FORBIDDEN_UI_FRAGMENTS)


def test_selected_staff_journal_includes_actor_and_target_logs_newest_first(initialized_db):
    _seed_user(1001, "Мария", "admin")
    _seed_user(2002, "Иван", "manager")

    run(
        log_staff_action(
            actor_tg_id=1001,
            actor_role="admin",
            actor_name="Мария",
            action_type="settings_phone_changed",
            human_text="Администратор Мария изменила контактный телефон филиала.",
            branch_timezone="Europe/Moscow",
        )
    )
    run(
        log_staff_action(
            actor_tg_id=2002,
            actor_role="manager",
            actor_name="Иван",
            action_type="role_removed",
            human_text="Управляющий Иван снял роль «Администратор» с пользователя Мария.",
            target_tg_id=1001,
            target_name="Мария",
            branch_timezone="Europe/Moscow",
        )
    )

    rows = run(get_staff_action_logs(1001))
    assert [row["action_type"] for row in rows] == ["role_removed", "settings_phone_changed"]
    assert "с пользователя Мария" in rows[0]["human_text"]
    assert "изменила контактный телефон" in rows[1]["human_text"]


def test_staff_action_pagination_and_timezone_format(initialized_db):
    _seed_user(1001, "Илья", "developer")
    for idx in range(12):
        run(
            log_staff_action(
                actor_tg_id=1001,
                actor_role="developer",
                actor_name="Илья",
                action_type="test_action",
                human_text=f"Разработчик Илья выполнил действие {idx}.",
                branch_timezone="Europe/Moscow",
            )
        )

    first_page = run(get_staff_action_logs(1001, limit=10, offset=0))
    second_page = run(get_staff_action_logs(1001, limit=10, offset=10))
    assert len(first_page) == 10
    assert len(second_page) == 2
    assert format_datetime_in_timezone("2026-05-27T15:03:00+00:00", "Europe/Moscow") == "27.05.2026 в 18:03:00"


@pytest.mark.parametrize("role", [None, "user", "client"])
def test_regular_user_cannot_view_staff_journal(role):
    assert can_view_personnel(role or "user") is False
