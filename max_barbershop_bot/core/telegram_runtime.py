"""Single source of truth for Telegram broadcast runtime status."""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from max_barbershop_bot.core.config import Config
from max_barbershop_bot.repositories.telegram_users import TelegramUsersRepository


@dataclass(frozen=True)
class TelegramRuntimeStatus:
    token_configured: bool
    db_path_configured: bool
    db_path_value_masked_or_present_only: str
    db_exists: bool
    db_readable: bool
    users_table_found: bool
    users_count: int
    users_with_chat_id_count: int
    adapter_kind: str
    unavailable_reason: str | None
    config_source: str
    project_cwd: str
    project_path: str
    env_file_checked: tuple[str, ...]
    git_commit: str | None
    runtime_version: str | None
    users_with_any_phone_count: int = 0
    users_with_yclients_client_id_count: int = 0
    tables: tuple[str, ...] = ()
    identity_columns_by_table: dict[str, tuple[str, ...]] | None = None
    masked_phone_samples: tuple[str, ...] = ()
    normalized_phone_key_samples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_telegram_runtime_status(config: Config) -> TelegramRuntimeStatus:
    """Inspect Telegram runtime configuration and DB without exposing secrets."""

    token_configured = bool((config.telegram_bot_token or "").strip())
    db_path = (config.telegram_db_path or "").strip()
    db_path_configured = bool(db_path)
    diagnostics = None
    if db_path_configured:
        diagnostics = TelegramUsersRepository(db_path).inspect_database(token_configured=token_configured)

    db_exists = bool(diagnostics and diagnostics.db_exists)
    db_readable = bool(diagnostics and diagnostics.db_readable)
    users_table_found = bool(diagnostics and diagnostics.users_table_found)
    users_count = int(diagnostics.users_count) if diagnostics else 0
    users_with_chat_id_count = int(diagnostics.users_with_chat_id_count) if diagnostics else 0
    users_with_any_phone_count = int(diagnostics.users_with_phone_count) if diagnostics else 0
    users_with_yclients_client_id_count = int(diagnostics.users_with_yclients_client_id_count) if diagnostics else 0

    reason = _unavailable_reason(
        token_configured=token_configured,
        db_path_configured=db_path_configured,
        db_exists=db_exists,
        db_readable=db_readable,
        users_table_found=users_table_found,
    )
    adapter_kind = "real" if reason is None else "unavailable"
    project_path = str(_project_root())
    return TelegramRuntimeStatus(
        token_configured=token_configured,
        db_path_configured=db_path_configured,
        db_path_value_masked_or_present_only=_mask_db_path(db_path),
        db_exists=db_exists,
        db_readable=db_readable,
        users_table_found=users_table_found,
        users_count=users_count,
        users_with_chat_id_count=users_with_chat_id_count,
        users_with_any_phone_count=users_with_any_phone_count,
        users_with_yclients_client_id_count=users_with_yclients_client_id_count,
        tables=diagnostics.tables if diagnostics else (),
        identity_columns_by_table=diagnostics.identity_columns_by_table if diagnostics else None,
        masked_phone_samples=diagnostics.masked_phone_samples if diagnostics else (),
        normalized_phone_key_samples=diagnostics.normalized_phone_key_samples if diagnostics else (),
        adapter_kind=adapter_kind,
        unavailable_reason=reason,
        config_source=config.config_source,
        project_cwd=str(Path.cwd()),
        project_path=project_path,
        env_file_checked=tuple(getattr(config, "env_file_checked", ()) or ()),
        git_commit=_git_commit(_project_root()),
        runtime_version=_git_commit(_project_root()),
    )


def _unavailable_reason(*, token_configured: bool, db_path_configured: bool, db_exists: bool, db_readable: bool, users_table_found: bool) -> str | None:
    if not token_configured:
        return "token_missing"
    if not db_path_configured:
        return "db_path_missing"
    if not db_exists:
        return "db_not_found"
    if not db_readable:
        return "db_unreadable"
    if not users_table_found:
        return "users_table_missing"
    return None


def _mask_db_path(db_path: str) -> str:
    if not db_path:
        return "not_configured"
    path = Path(db_path)
    return f"configured:{path.name}" if path.name else "configured"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_commit(project_root: Path) -> str | None:
    env_commit = os.environ.get("GIT_COMMIT") or os.environ.get("COMMIT_SHA") or os.environ.get("RELEASE_SHA")
    if env_commit:
        return env_commit[:40]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root,
            check=True,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except Exception:
        return None
    return result.stdout.strip() or None
