from __future__ import annotations

from typing import Any, Mapping

from html import escape as html_escape

from app.utils.datetime import format_datetime

ROLE_LABELS = {
    "developer": "💻 Разработчик",
    "manager": "👑 Управляющий",
    "admin": "🛡 Администратор",
    "user": "👤 Пользователь",
}

ROLE_PRIORITY = {
    "developer": 0,
    "manager": 1,
    "admin": 2,
}

ISSUER_FALLBACK_NAME = "Разработчик"


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "user", "👤 Пользователь")


def normalize_name(name: str | None) -> str:
    value = (name or "").strip()
    return value if value else "Без имени"


def display_name(user: Mapping[str, Any]) -> str:
    return normalize_name(user.get("display_name") or user.get("name"))


def short_name(name: str | None, max_length: int = 20) -> str:
    value = normalize_name(name)
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}…"


def format_issuer_name(raw_name: str | None) -> str:
    if raw_name:
        return normalize_name(raw_name)
    return ISSUER_FALLBACK_NAME


def _html(text: str) -> str:
    return html_escape(text, quote=False)


def format_staff_card(user: Mapping[str, Any]) -> str:
    name = _html(display_name(user))
    tg_id = int(user.get("user_id") or user.get("tg_id") or 0)
    role = role_label(user.get("role"))
    assigned_at = format_datetime(user.get("role_assigned_at") or user.get("assigned_at"))
    assigned_by_raw = user.get("assigned_by_display_name") or user.get("assigned_by_name")
    assigned_by = _html(format_issuer_name(assigned_by_raw))
    protected_line = "\n🔒 Защищённый системный разработчик" if tg_id == 378881880 else ""
    return (
        "<b>Карточка сотрудника</b>\n"
        f"👤 Имя: {name}\n"
        f"🆔 Telegram ID: {tg_id}\n"
        f"🎖 Роль: {role}\n"
        f"🗓 Роль с: {assigned_at}\n"
        f"👤 Выдал: {assigned_by}{protected_line}"
    )
