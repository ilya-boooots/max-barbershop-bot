"""Protected developer diagnostics screen for the MAX bot."""

from __future__ import annotations

import sqlite3
import subprocess
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
from max_barbershop_bot.services.diagnostics import recent_errors, sanitize_text
from max_barbershop_bot.services.reminder_lifecycle import get_lifecycle_status
from max_barbershop_bot.services.yclients_settings import check_yclients_connection

NO_ACCESS_TEXT = "⛔️ Доступ только для разработчика."
_REQUIRED_TABLES = (
    "users",
    "staff_roles",
    "yclients_settings",
    "platform_attribution",
    "notification_history",
    "app_settings",
)


@dataclass(frozen=True)
class HealthLine:
    name: str
    ok: bool | None
    message: str


async def build_developer_diagnostics_text(*, database_path: str, max_bot_token: str | None = None) -> str:
    """Build the clean Telegram-style developer diagnostics menu header."""

    now = datetime.now(UTC).strftime("%d.%m.%Y %H:%M:%S UTC")
    return sanitize_text(
        "✅ Бот запущен и работает.\n"
        f"🕒 Время сервера: {now}\n"
        f"📦 Версия: {_git_short_sha()}\n\n"
        "🛠 Разработка: Диагностика\n"
        "Выберите действие:"
    )


async def build_developer_status_text(*, database_path: str, max_bot_token: str | None = None) -> str:
    """Build a detailed protected developer system status screen."""

    lines = [
        "💡 Статус системы",
        "",
        "✅ Бот запущен и работает.",
        f"🕒 Время сервера: {datetime.now(UTC).strftime('%d.%m.%Y %H:%M:%S UTC')}",
        f"📦 Версия: {_git_short_sha()}",
        f"🌍 Окружение: {sanitize_text(getenv('APP_ENV', 'local'))}",
        "",
    ]
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
            "",
            render_recent_errors_block(),
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

    status = get_lifecycle_status()
    enabled = _bool_env("REMINDERS_ENABLED", DEFAULT_REMINDERS_ENABLED)
    interval = status.interval_seconds or _int_env("REMINDERS_POLL_INTERVAL_SECONDS", DEFAULT_REMINDERS_POLL_INTERVAL_SECONDS, minimum=30)
    if status.is_running:
        state = "работают"
    elif status.state == "disabled" or not enabled:
        state = "остановлены"
    elif status.state == "error":
        state = "ошибка"
    else:
        state = "не запущены"
    parts = [f"{'✅' if status.is_running else '⚠️'} {state}", f"интервал {interval}с"]
    if status.last_success_at:
        parts.append(f"успех {_format_dt(status.last_success_at)}")
    if status.last_error_at:
        parts.append(f"ошибка {_format_dt(status.last_error_at)} {sanitize_text(status.last_error_class or '')}".strip())
    if status.is_running and status.last_started_at:
        parts.append(f"старт {_format_dt(status.last_started_at)}")
    return "; ".join(parts)


def format_last_errors() -> str:
    """Return compact in-memory last errors status."""

    errors = recent_errors(5)
    if not errors:
        return "нет"
    parts = []
    for item in errors[:3]:
        parts.append(
            f"{item.get('error_id', '—')} {item.get('exception_class', 'Error')} "
            f"@ {item.get('screen_id', '—')}"
        )
    return "; ".join(parts)


def render_recent_errors_block(limit: int = 10) -> str:
    """Render detailed recent error rows for the status screen."""

    errors = recent_errors(limit)
    if not errors:
        return "Последние ошибки: нет"
    lines = ["Последние ошибки:"]
    for item in errors:
        lines.append(
            "• "
            f"{item.get('error_id', '—')} | {item.get('exception_class', 'Error')} | "
            f"user={item.get('platform_user_id', '—')} | screen={item.get('screen_id', '—')} | "
            f"payload={item.get('callback_payload', '—')}"
        )
    return sanitize_text("\n".join(lines))


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


def _git_short_sha() -> str:
    value = getenv("GIT_COMMIT") or getenv("COMMIT_SHA") or getenv("APP_VERSION")
    if value:
        return sanitize_text(value[:12])
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=1,
        )
        return sanitize_text(result.stdout.strip() or "unknown")
    except Exception:
        return "unknown"


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
