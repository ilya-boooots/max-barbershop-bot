import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging() -> None:
    log_dir = Path("/opt/cafe-bot/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Файл с ротацией: 10MB * 10 бэкапов = максимум ~100MB
    file_handler = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # В консоль (journalctl)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    # Чтобы не дублировать хендлеры при перезапусках
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # Тише про aiohttp, чтобы не спамил
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

