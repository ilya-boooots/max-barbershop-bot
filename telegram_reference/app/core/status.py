from __future__ import annotations

import subprocess
from datetime import timedelta

from app.config import get_db_path
from app.core.config import get_settings
from app.core.auth import normalize_role
from app.core.diagnostics_runtime import get_uptime_seconds
from app.core.logging import mask_secret
from app.repositories.yclients_settings import get_yclients_settings
from app.repositories.users import get_user as get_db_user


def _read_git_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=1,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def build_status_text(user_id: int) -> str:
    settings = get_settings()
    db_user = await get_db_user(user_id)
    role = normalize_role(user_id, db_user["role"] if db_user else None)

    uptime_text = str(timedelta(seconds=get_uptime_seconds()))
    db_path = get_db_path()
    db_status = "✅ найден" if db_path.exists() else "⚠️ не найден"
    service_hint = settings.systemd_unit

    db_settings = await get_yclients_settings()
    partner_token = (db_settings.partner_token if db_settings else None) or settings.yclients_partner_token
    user_token = (db_settings.user_token if db_settings else None) or settings.yclients_user_token
    company_id = (db_settings.company_id if db_settings else None) or settings.yclients_company_id
    configured = bool(partner_token and company_id)

    yclients_parts = [
        f"configured: {'yes' if configured else 'no'}",
        f"partner: {mask_secret(partner_token)}",
        f"user: {mask_secret(user_token)}",
        f"company: {mask_secret(company_id)}",
    ]

    return (
        "🩺 Статус бота\n"
        f"Версия: `{_read_git_hash()}`\n"
        f"Uptime: `{uptime_text}`\n"
        f"Окружение: `{settings.app_env}`\n"
        f"БД: `{db_path}` ({db_status})\n"
        f"YClients: {', '.join(yclients_parts)}\n"
        f"Ваша роль: `{role}`\n"
        f"Systemd unit: `{service_hint}`"
    )
