"""Protected developer diagnostics screen for the MAX bot."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from os import getenv
import aiohttp

from max_barbershop_bot.core.config import DEFAULT_REMINDERS_ENABLED, DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS
from max_barbershop_bot.integrations.yclients.dto import YClientsHealthCheckResult
from max_barbershop_bot.integrations.yclients.exceptions import YCLIENTS_ERROR_AUTH, YCLIENTS_ERROR_CREDENTIALS, YCLIENTS_ERROR_RATE_LIMIT, YCLIENTS_ERROR_SERVER, YCLIENTS_ERROR_TRANSPORT
from max_barbershop_bot.max_api.client import MaxApiAuthError, MaxApiClient, MaxApiNetworkError, MaxApiRateLimitError
from max_barbershop_bot.repositories.notification_history import NotificationHistoryRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.diagnostics import sanitize_text
from max_barbershop_bot.services.reminders import get_reminder_loop_status
from max_barbershop_bot.services.yclients_settings import check_yclients_connection

NO_ACCESS_TEXT = "⛔️ Доступ только для разработчика."
_REQUIRED_TABLES = (
    "users",
    "staff_roles",
    "yclients_settings",
    "platform_attribution",
    "notification_history",
)


@dataclass(frozen=True)
class HealthLine:
    name: str
    ok: bool | None
    message: str


async def build_developer_diagnostics_text(*, database_path: str, max_bot_token: str | None = None) -> str:
    """Build a compact Telegram-style developer diagnostics screen."""

    lines = ["🛠 Диагностика разработчика", ""]
    max_api = await check_max_api_health(max_bot_token=max_bot_token)
    yclients = await check_yclients_health(database_path)
    db = check_db_health(database_path)
    reminders = format_reminders_status()
    last_errors = format_last_errors()
    failed_notifications = format_failed_notifications(database_path)

    lines.extend(
        [
            _format_line(max_api),
            _format_line(yclients),
            _format_line(db),
            f"Напоминания: {reminders}",
            f"Последние ошибки: {last_errors}",
            f"Ошибки уведомлений: {failed_notifications}",
        ]
    )
    return sanitize_text("\n".join(lines))


async def check_max_api_health(*, max_bot_token: str | None = None) -> HealthLine:
    """Check MAX token presence and safe read-only /me endpoint."""

    token = (max_bot_token if max_bot_token is not None else getenv("MAX_BOT_TOKEN", "")).strip()
    if not token:
        return HealthLine("MAX API", False, "ошибка конфигурации")
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with MaxApiClient(timeout=timeout) as client:
            await client.get_me()
        return HealthLine("MAX API", True, "OK")
    except MaxApiAuthError:
        return HealthLine("MAX API", False, "ошибка авторизации")
    except MaxApiRateLimitError:
        return HealthLine("MAX API", None, "rate limit")
    except MaxApiNetworkError:
        return HealthLine("MAX API", False, "транспортная ошибка")
    except Exception as exc:  # noqa: BLE001 - diagnostics must stay safe and compact.
        return HealthLine("MAX API", False, _safe_error(exc))


async def check_yclients_health(database_path: str) -> HealthLine:
    """Run read-only YClients company health check through existing setup helper."""

    settings = YClientsSettingsRepository(database_path).get_active()
    if settings is None:
        return HealthLine("YClients API", False, "не настроено")
    result = await check_yclients_connection(settings)
    return HealthLine("YClients API", result.ok, _yclients_message(result))


def check_db_health(database_path: str) -> HealthLine:
    """Check SQLite connectivity and important table presence without migrations."""

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("SELECT 1").fetchone()
            existing = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        missing = [table for table in _REQUIRED_TABLES if table not in existing]
        if missing:
            return HealthLine("DB", None, "нет таблиц: " + ", ".join(missing[:3]))
        return HealthLine("DB", True, "OK")
    except Exception as exc:  # noqa: BLE001 - diagnostics must not crash the screen.
        return HealthLine("DB", False, _safe_error(exc))


def format_reminders_status() -> str:
    """Return reminder-loop status with in-memory tracker details."""

    status = get_reminder_loop_status()
    enabled = _bool_env("REMINDERS_ENABLED", DEFAULT_REMINDERS_ENABLED)
    interval = _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    state = "работают" if status.is_running else ("включены, не запущены" if enabled else "выключены")
    parts = [f"{'✅' if status.is_running else '⚠️'} {state}", f"интервал {interval}с"]
    if status.last_success_at:
        parts.append(f"успех {_format_dt(status.last_success_at)}")
    if status.last_error_at:
        parts.append(f"ошибка {_format_dt(status.last_error_at)} {sanitize_text(status.last_error_class or '')}".strip())
    if status.is_running and status.last_started_at:
        parts.append(f"старт {_format_dt(status.last_started_at)}")
    return "; ".join(parts)


def format_last_errors() -> str:
    """Return compact in-memory last errors status.

    MAX currently sends developer alerts from the central error handler but does not
    persist/list them for screens, so diagnostics reports that storage is absent.
    """

    return "нет данных"


def format_failed_notifications(database_path: str) -> str:
    """Summarize failed notification rows from existing notification history."""

    try:
        repository = NotificationHistoryRepository(database_path)
        failed = repository.list_recent_failed(limit=50)
        counts = repository.count_by_status()
    except Exception as exc:  # noqa: BLE001 - keep diagnostics available even if table is absent.
        return _safe_error(exc)
    if not failed:
        return "0"
    interesting = []
    for key in ("failed", "blocked", "stopped", "rate_limited", "delivery_error"):
        if counts.get(key):
            interesting.append(f"{key}={counts[key]}")
    return f"{len(failed)}" + (" (" + ", ".join(interesting) + ")" if interesting else "")


def _format_line(line: HealthLine) -> str:
    icon = "✅" if line.ok is True else "⚠️" if line.ok is None else "❌"
    return f"{line.name}: {icon} {sanitize_text(line.message)}"


def _yclients_message(result: YClientsHealthCheckResult) -> str:
    if result.ok:
        return "OK"
    category = result.error_category or "error"
    labels = {
        YCLIENTS_ERROR_CREDENTIALS: "ошибка конфигурации",
        YCLIENTS_ERROR_AUTH: "ошибка авторизации",
        YCLIENTS_ERROR_RATE_LIMIT: "rate limit",
        YCLIENTS_ERROR_SERVER: "недоступен",
        YCLIENTS_ERROR_TRANSPORT: "транспортная ошибка",
    }
    suffix = f" ({result.status_code})" if result.status_code else ""
    return labels.get(category, sanitize_text(result.short_message)) + suffix


def _safe_error(exc: BaseException) -> str:
    return sanitize_text(f"{type(exc).__name__}: {str(exc)[:120]}")


def _format_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m %H:%M UTC")


def _bool_env(name: str, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed >= minimum else default
