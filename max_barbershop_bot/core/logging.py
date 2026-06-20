"""Logging setup for the MAX bot runtime."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from max_barbershop_bot.services.diagnostics import sanitize_text


_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(log_level: str) -> None:
    """Configure standard Python logging without exposing secrets."""

    level_name = log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.getenv("BOT_LOG_FILE") or os.getenv("LOG_FILE")
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def add_database_log_handler(database_path: str) -> None:
    """Mirror runtime logs to SQLite diagnostics storage."""

    root = logging.getLogger()
    if any(isinstance(handler, _SQLiteDiagnosticsLogHandler) for handler in root.handlers):
        return
    handler = _SQLiteDiagnosticsLogHandler(database_path)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)


class _SQLiteDiagnosticsLogHandler(logging.Handler):
    """Best-effort SQLite log sink that never interrupts application logging."""

    def __init__(self, database_path: str) -> None:
        super().__init__()
        self._database_path = database_path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from max_barbershop_bot.repositories.diagnostics import DiagnosticsRepository

            DiagnosticsRepository(self._database_path).log_bot_event(
                level=record.levelname,
                source=record.name,
                message=sanitize_text(self.format(record)),
            )
        except Exception:
            return
