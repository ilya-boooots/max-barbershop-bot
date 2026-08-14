"""Small key-value app settings repository for MAX bot runtime switches."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime


NOTIFICATIONS_ENABLED_KEY = "notifications_enabled"
DEFAULT_NOTIFICATIONS_ENABLED = True

AUTOMATION_SETTINGS_DEFAULTS: dict[str, dict[str, object]] = {
    "post_visit_review": {
        "enabled": True,
        "delay_hours": 2,
        "message_text": "Как прошёл ваш визит?\n\nОцените, пожалуйста, от 1 до 5 ⭐",
        "rating_scale": "1-5",
        "high_rating_behavior": "4-5: ask_public_review",
        "low_rating_behavior": "1-3: ask_comment_notify_admin",
    },
    "cancellation_return": {
        "enabled": True,
        "delay_hours": 2,
        "message_text": "Видим, что вы отменили запись 😔\n\nМожем подобрать другое удобное время.",
        "exclude_has_future_booking": True,
    },
    "lost_clients": {
        "enabled": True,
        "threshold_days": [30, 60, 90],
        "exclude_has_future_booking": True,
        "text_30": "Давно вас не видели 😊\n\nХотите записаться снова? Подберём удобное время.",
        "text_60": "Похоже, вы давно не заглядывали к нам.\n\nПодберём удобное время?",
        "text_90": "Мы скучаем 😄\n\nДля вас есть специальное предложение на возвращение.",
    },
    "birthday": {
        "enabled": True,
        "send_days_before": 7,
        "once_per_year": True,
        "message_text": "Скоро ваш день рождения, поздравляем 🎉 😊\n\nХотим сделать вам приятный подарок - покажите это сообщение администратору при оплате.",
        "gift_text": "Покажите это сообщение администратору при оплате.",
    },
    "repeat_visit": {
        "enabled": True,
        "delay_days": 30,
        "exclude_has_future_booking": True,
        "respect_marketing_unsubscribe": True,
        "respect_anti_spam": True,
        "respect_working_hours": True,
        "templates": [
            "Пора позаботиться о себе? 😊\n\nПодберём удобное время для следующего визита.",
            "Кажется, самое время снова заглянуть к нам ✨\n\nПодберём удобное окно для визита?",
            "Хотите снова уделить время себе? 😊\n\nПоможем выбрать подходящую услугу и удобное время.",
            "Давно не виделись ✨\n\nМожем подобрать удобное время к вашему мастеру.",
            "Хотите снова выглядеть свежо? 😊\n\nЗапишитесь на удобное время — мы всё подготовим.",
        ],
        "service_rules": [],
    },
    "anti_spam": {
        "enabled": True,
        "max_weekly_marketing": 2,
        "min_interval_hours": 48,
        "respect_marketing_unsubscribe": True,
        "service_notifications_ignore_marketing_unsubscribe": True,
        "block_duplicate_same_event": True,
    },
    "review_links": {"yandex_url": "", "two_gis_url": ""},
    "quiet_hours": {
        "enabled": True,
        "start": "21:00",
        "end": "09:00",
        "outside_allowed_behavior": "postpone_to_next_allowed",
        "working_hours_source": "yclients",
    },
}


class AppSettingsRepository:
    """Persist global bot settings with explicit-value semantics."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def get_bool(self, key: str, *, default: bool) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ? LIMIT 1",
                (str(key),),
            ).fetchone()
        if row is None or row["value"] is None:
            return bool(default)
        raw_value = row["value"]
        try:
            payload = json.loads(str(raw_value))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("enabled"), bool):
            return bool(payload["enabled"])
        return _value_to_bool(raw_value, default=default)

    def set_bool(self, key: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), "1" if enabled else "0", now, now),
            )
            connection.commit()


    def get_json(self, key: str) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ? LIMIT 1",
                (str(key),),
            ).fetchone()
        if row is None or row["value"] is None:
            return None
        try:
            payload = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def set_json(self, key: str, value: dict[str, object]) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), json.dumps(dict(value), ensure_ascii=False, sort_keys=True), now, now),
            )
            connection.commit()


    def get_automation_setting(self, key: str) -> dict[str, object]:
        default = AUTOMATION_SETTINGS_DEFAULTS.get(str(key), {})
        merged = copy.deepcopy(default)
        stored = self.get_json(str(key))
        if stored is None:
            stored = self.get_json(f"automation:{key}")
        if stored:
            merged.update(stored)
        return merged

    def set_automation_setting(self, key: str, value: dict[str, object]) -> None:
        self.set_json(str(key), value)

    def notifications_enabled(self) -> bool:
        """Return global notification switch; missing means enabled."""

        return self.get_bool(NOTIFICATIONS_ENABLED_KEY, default=DEFAULT_NOTIFICATIONS_ENABLED)

    def notification_setting_source(self) -> str:
        """Return whether notification setting is explicit or defaulted."""

        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM app_settings WHERE key = ? LIMIT 1",
                (NOTIFICATIONS_ENABLED_KEY,),
            ).fetchone()
        return "app_settings" if row is not None else "default_enabled"

    def set_notifications_enabled(self, enabled: bool) -> None:
        self.set_bool(NOTIFICATIONS_ENABLED_KEY, enabled)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection


def _value_to_bool(value: object, *, default: bool) -> bool:
    clean = str(value).strip().lower()
    if clean in {"1", "true", "yes", "on", "enabled", "включено"}:
        return True
    if clean in {"0", "false", "no", "off", "disabled", "выключено"}:
        return False
    return bool(default)
