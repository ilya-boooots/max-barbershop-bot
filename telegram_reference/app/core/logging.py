from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings, mask_secret


def setup_logging() -> None:
    level_name = get_settings().log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )

    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def format_log_context(**kwargs: Any) -> str:
    parts: list[str] = []
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")
    return " | ".join(parts)
