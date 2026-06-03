"""Logging setup for the MAX bot runtime."""

from __future__ import annotations

import logging


_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(log_level: str) -> None:
    """Configure standard Python logging without exposing secrets."""

    level_name = log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
