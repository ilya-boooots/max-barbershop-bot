from __future__ import annotations

import pytest

from app.repositories.automation_settings import DEFAULTS, get_setting, upsert_setting
from tests.sync import run

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "key",
    [
        "post_visit_review",
        "cancellation_return",
        "lost_clients",
        "birthday",
        "repeat_visit",
        "anti_spam",
        "quiet_hours",
    ],
)
def test_missing_automation_settings_are_enabled_by_default(initialized_db, key: str) -> None:
    setting = run(get_setting(key))
    assert setting.get("enabled") is True


def test_existing_disabled_setting_is_preserved(initialized_db) -> None:
    run(upsert_setting("lost_clients", {"enabled": False, "threshold_days": [30, 60, 90]}, updated_by_tg_id=101))

    setting = run(get_setting("lost_clients"))

    assert setting.get("enabled") is False


def test_review_links_callbacks_are_within_telegram_limit() -> None:
    callbacks = [
        "broadcast:settings:edit:review_links:yandex_url",
        "broadcast:settings:edit:review_links:two_gis_url",
        "broadcast:settings:edit:review_links:clear_yandex",
        "broadcast:settings:edit:review_links:clear_two_gis",
        "nav:back",
        "nav:home",
    ]
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)


def test_defaults_snapshot_for_core_modules() -> None:
    assert DEFAULTS["post_visit_review"]["enabled"] is True
    assert DEFAULTS["cancellation_return"]["enabled"] is True
    assert DEFAULTS["lost_clients"]["enabled"] is True
    assert DEFAULTS["birthday"]["enabled"] is True
    assert DEFAULTS["repeat_visit"]["enabled"] is True
    assert DEFAULTS["anti_spam"]["enabled"] is True
