from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

from app.db.sqlite import execute, fetchone


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULTS: dict[str, dict[str, Any]] = {
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
        "text_30": "Давно вас не видели 😊\n\nСамое время обновить стрижку.",
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
            "Пора обновить стрижку? 😊\n\nОбычно к этому времени форма уже начинает теряться.",
            "Кажется, самое время снова заглянуть к нам ✂️\n\nПодберём удобное окно для визита?",
            "Ваша стрижка уже могла немного потерять форму 😊\n\nСамое время освежить образ.",
            "Давно не виделись ✂️\n\nМожем подобрать удобное время к вашему мастеру.",
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


async def get_setting(key: str) -> dict[str, Any]:
    row = await fetchone("SELECT value_json FROM automation_settings WHERE key=?", (key,))
    if not row:
        return copy.deepcopy(DEFAULTS.get(key, {}))
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    merged = copy.deepcopy(DEFAULTS.get(key, {}))
    if isinstance(payload, dict):
        merged.update(payload)
    return merged


async def upsert_setting(key: str, value: dict[str, Any], *, updated_by_tg_id: int) -> None:
    await execute(
        """
        INSERT INTO automation_settings (key, value_json, updated_by_tg_id, updated_at_utc)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value_json=excluded.value_json,
            updated_by_tg_id=excluded.updated_by_tg_id,
            updated_at_utc=excluded.updated_at_utc
        """,
        (key, json.dumps(value, ensure_ascii=False), updated_by_tg_id, _now_utc_iso()),
    )
