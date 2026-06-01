from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required configuration is invalid or incomplete."""


_DEFAULT_PROTECTED_DEV_TG_ID = 378881880


@dataclass(frozen=True)
class Settings:
    bot_token: str
    protected_dev_tg_id: int
    db_path: Path
    app_secret_key: str
    business_name: str
    support_contact: str
    business_address: str
    business_phone: str
    contact_address: str
    booking_phone: str
    yandex_review_url: str
    two_gis_review_url: str
    staff_group_id: int
    yclients_partner_token: str | None
    yclients_user_token: str | None
    yclients_company_id: str | None
    yclients_base_url: str | None
    app_env: str
    log_level: str
    systemd_unit: str
    throttle_msg_seconds: float
    throttle_cb_seconds: float
    yclients_timeout_seconds: float
    yclients_retry_max: int
    barbershop_photo_file_id: str | None
    yclients_smoke_test_phone: str
    loyalty_enabled: bool


def mask_secret(value: str | None, keep: int = 3) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}***{value[-keep:]}"


def load_local_dotenv() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value)


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_required(name: str, value: str | None, missing: list[str]) -> str:
    cleaned = _clean_optional(value)
    if cleaned is None:
        missing.append(name)
        return ""
    return cleaned


def _parse_float(name: str, value: str, errors: list[str]) -> float:
    try:
        return float(value)
    except ValueError:
        errors.append(f"{name} must be a number")
        return 0.0


def _parse_int(name: str, value: str, errors: list[str]) -> int:
    try:
        return int(value)
    except ValueError:
        errors.append(f"{name} must be an integer")
        return 0


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_local_dotenv()

    missing: list[str] = []
    errors: list[str] = []

    bot_token = _clean_required("BOT_TOKEN", os.getenv("BOT_TOKEN"), missing)
    protected_dev_raw = _clean_required(
        "PROTECTED_DEV_TG_ID",
        os.getenv("PROTECTED_DEV_TG_ID", str(_DEFAULT_PROTECTED_DEV_TG_ID)),
        missing,
    )
    db_path_raw = _clean_required("DB_PATH", os.getenv("DB_PATH"), missing)

    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ConfigError(f"Missing required environment variables: {missing_keys}")

    protected_dev_tg_id = _parse_int("PROTECTED_DEV_TG_ID", protected_dev_raw, errors)
    staff_group_id = _parse_int("STAFF_GROUP_ID", os.getenv("STAFF_GROUP_ID", "0"), errors)
    throttle_msg_seconds = _parse_float("THROTTLE_MSG_SECONDS", os.getenv("THROTTLE_MSG_SECONDS", "1.2"), errors)
    throttle_cb_seconds = _parse_float("THROTTLE_CB_SECONDS", os.getenv("THROTTLE_CB_SECONDS", "0.8"), errors)
    yclients_timeout_seconds = _parse_float("YCLIENTS_TIMEOUT_SECONDS", os.getenv("YCLIENTS_TIMEOUT_SECONDS", "10"), errors)
    yclients_retry_max = _parse_int("YCLIENTS_RETRY_MAX", os.getenv("YCLIENTS_RETRY_MAX", "3"), errors)

    if errors:
        raise ConfigError("; ".join(errors))

    db_path = Path(db_path_raw).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    else:
        db_path = db_path.resolve()

    app_secret_key = _clean_optional(os.getenv("APP_SECRET_KEY")) or bot_token

    return Settings(
        bot_token=bot_token,
        protected_dev_tg_id=protected_dev_tg_id,
        db_path=db_path,
        app_secret_key=app_secret_key,
        business_name=os.getenv("BUSINESS_NAME", "Барбершоп"),
        support_contact=os.getenv("SUPPORT_CONTACT", "@XXX"),
        business_address=os.getenv("BUSINESS_ADDRESS", os.getenv("CONTACT_ADDRESS", "Саратов, улица Пушкина, 1")),
        business_phone=os.getenv("BUSINESS_PHONE", os.getenv("BOOKING_PHONE", "+7 (8452) 00-00-00")),
        contact_address=os.getenv("BUSINESS_ADDRESS", os.getenv("CONTACT_ADDRESS", "Саратов, улица Пушкина, 1")),
        booking_phone=os.getenv("BUSINESS_PHONE", os.getenv("BOOKING_PHONE", "+7 (8452) 00-00-00")),
        yandex_review_url=os.getenv("YANDEX_REVIEW_URL", "https://yandex.ru/maps/"),
        two_gis_review_url=os.getenv("TWO_GIS_REVIEW_URL", "https://2gis.ru/"),
        staff_group_id=staff_group_id,
        yclients_partner_token=_clean_optional(os.getenv("YCLIENTS_PARTNER_TOKEN")),
        yclients_user_token=_clean_optional(os.getenv("YCLIENTS_USER_TOKEN")),
        yclients_company_id=_clean_optional(os.getenv("YCLIENTS_COMPANY_ID")),
        yclients_base_url=_clean_optional(os.getenv("YCLIENTS_BASE_URL")),
        app_env=os.getenv("APP_ENV", "dev"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        systemd_unit=os.getenv("SYSTEMD_UNIT", "cafe-bot@<name>.service"),
        throttle_msg_seconds=max(0.2, throttle_msg_seconds),
        throttle_cb_seconds=max(0.2, throttle_cb_seconds),
        yclients_timeout_seconds=max(2.0, yclients_timeout_seconds),
        yclients_retry_max=max(1, yclients_retry_max),
        barbershop_photo_file_id=_clean_optional(os.getenv("BARBERSHOP_PHOTO_FILE_ID")),
        yclients_smoke_test_phone=os.getenv("YCLIENTS_SMOKE_TEST_PHONE", "+79990000000"),
        loyalty_enabled=_parse_bool(os.getenv("LOYALTY_ENABLED"), default=False),
    )
