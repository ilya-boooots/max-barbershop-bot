"""Transport-neutral booking service for YClients service selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.dto import (
    YClientsBookingRecord,
    YClientsService,
    YClientsServiceCategory,
    YClientsSlot,
    YClientsStaff,
)
from max_barbershop_bot.integrations.yclients.exceptions import YClientsError
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.integrations.yclients.utils import MAX_BOOKING_COMMENT_MARKER, normalize_phone
from max_barbershop_bot.repositories.yclients_settings import DEFAULT_BRANCH_TIMEZONE, YClientsSettings, YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

BOOKING_NOT_CONFIGURED_TEXT = "Запись пока не настроена 🙏\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
BOOKING_YCLIENTS_ERROR_TEXT = "Не удалось загрузить услуги 🙏\n\nПожалуйста, попробуйте позже."
BOOKING_MASTERS_NOT_CONFIGURED_TEXT = "Запись пока не настроена 🙏\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
BOOKING_MASTERS_YCLIENTS_ERROR_TEXT = "Не удалось загрузить мастеров 🙏\n\nПожалуйста, попробуйте позже."
BOOKING_SLOTS_NOT_CONFIGURED_TEXT = "Запись пока не настроена 🙏\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
BOOKING_SLOTS_YCLIENTS_ERROR_TEXT = "Не удалось загрузить свободное время 🙏\n\nПожалуйста, попробуйте позже."
BOOKING_CREATE_NOT_CONFIGURED_TEXT = "Запись пока не настроена 🙏\n\nПожалуйста, попробуйте позже или обратитесь к администратору."
BOOKING_CREATE_YCLIENTS_ERROR_TEXT = "Не удалось создать запись 🙏\n\nВозможно, это время уже заняли. Попробуйте выбрать другой слот."

_RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class BookingServiceError(RuntimeError):
    """Clean booking domain error safe for UI flow handling."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class BookingSettingsMissingError(BookingServiceError):
    """Raised when active YClients settings are absent or incomplete."""


class BookingYClientsError(BookingServiceError):
    """Raised when YClients cannot provide booking services safely."""


class BookingCreateError(BookingYClientsError):
    """Raised when YClients cannot create the final booking safely."""


@dataclass(frozen=True)
class BookingCategory:
    """A YClients service category normalized for booking step 1."""

    yclients_category_id: str
    title: str


@dataclass(frozen=True)
class BookingServiceItem:
    """A YClients service normalized for booking step 1."""

    yclients_service_id: str
    title: str
    yclients_category_id: str | None = None
    category_title: str | None = None
    price_min: int | float | None = None
    price_max: int | float | None = None


@dataclass(frozen=True)
class BookingMasterItem:
    """A YClients staff/master normalized for booking step 2."""

    yclients_master_id: str
    title: str
    specialization: str | None = None


@dataclass(frozen=True)
class BookingSlotItem:
    """A YClients slot normalized for booking step 3."""

    time: str
    datetime_iso: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class CreatedBooking:
    """Created YClients record normalized for local attribution and UI."""

    yclients_record_id: str
    yclients_client_id: str | None = None
    datetime_iso: str | None = None
    raw_payload: dict[str, Any] | list[Any] | None = None


@dataclass(frozen=True)
class BookingCatalog:
    """Service categories and services loaded from YClients."""

    categories: list[BookingCategory]
    services: list[BookingServiceItem]


class BookingService:
    """Load and normalize YClients services without bot transport dependencies."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    def load_active_settings_for_booking(self, *, operation: str) -> YClientsSettings | None:
        """Load active YClients settings through the shared DB-primary loader."""

        return load_active_yclients_settings(self._settings_repository, operation=operation)

    def get_branch_timezone(self) -> str:
        """Return the active branch timezone with a safe repository default."""

        try:
            settings = self.load_active_settings_for_booking(operation="get_booking_timezone")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking timezone lookup failed: operation=get_booking_timezone error_class=%s",
                type(exc).__name__,
            )
            return DEFAULT_BRANCH_TIMEZONE
        return _timezone_name(settings.branch_timezone if settings else None)

    def get_available_dates(self, *, days: int = 14) -> list[date]:
        """Return selectable dates in the active branch timezone."""

        return build_booking_dates(days=days, timezone_name=self.get_branch_timezone())


    async def create_booking(
        self,
        *,
        yclients_service_id: str,
        yclients_master_id: str,
        booking_date: str | date,
        booking_slot: str,
        client_name: str,
        client_phone: str,
        selected_datetime: str | None = None,
        comment: str = "",
    ) -> CreatedBooking:
        """Create the final YClients booking record for the selected slot."""

        payload = build_booking_payload(
            yclients_service_id=yclients_service_id,
            yclients_master_id=yclients_master_id,
            booking_date=booking_date,
            booking_slot=booking_slot,
            client_name=client_name,
            client_phone=client_phone,
            selected_datetime=selected_datetime,
            comment=comment,
        )
        service_id = str(payload["service_id"])
        master_id = str(payload.get("staff_id") or "")
        datetime_iso = str(payload["datetime_iso"])
        try:
            settings = self.load_active_settings_for_booking(operation="create_booking")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking settings lookup failed: operation=create_booking service_id=%s master_id=%s "
                "datetime=%s error_class=%s",
                service_id,
                master_id,
                datetime_iso,
                type(exc).__name__,
            )
            raise BookingSettingsMissingError(BOOKING_CREATE_NOT_CONFIGURED_TEXT) from exc

        if not has_required_yclients_credentials(settings):
            logger.info(
                "Booking create unavailable: operation=create_booking settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s service_id=%s master_id=%s datetime=%s",
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
                service_id,
                master_id,
                datetime_iso,
            )
            raise BookingSettingsMissingError(BOOKING_CREATE_NOT_CONFIGURED_TEXT)

        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                created = await yclients.create_booking(company_id=settings.company_id, **payload)
        except YClientsError as exc:
            logger.warning(
                "Booking YClients error: operation=create_booking service_id=%s master_id=%s datetime=%s "
                "error_class=%s status_code=%s",
                service_id,
                master_id,
                datetime_iso,
                type(exc).__name__,
                exc.status_code,
            )
            raise BookingCreateError(BOOKING_CREATE_YCLIENTS_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "Booking unexpected YClients error: operation=create_booking service_id=%s master_id=%s datetime=%s "
                "error_class=%s",
                service_id,
                master_id,
                datetime_iso,
                type(exc).__name__,
            )
            raise BookingCreateError(BOOKING_CREATE_YCLIENTS_ERROR_TEXT) from exc

        record_id = extract_yclients_record_id(created)
        if not record_id:
            logger.warning(
                "Booking create invalid response: operation=create_booking service_id=%s master_id=%s datetime=%s",
                service_id,
                master_id,
                datetime_iso,
            )
            raise BookingCreateError(BOOKING_CREATE_YCLIENTS_ERROR_TEXT)

        logger.info(
            "Booking created: operation=create_booking service_id=%s master_id=%s datetime=%s yclients_record_id=%s",
            service_id,
            master_id,
            created.datetime or datetime_iso,
            record_id,
        )
        return CreatedBooking(
            yclients_record_id=record_id,
            yclients_client_id=extract_yclients_client_id(created),
            datetime_iso=created.datetime or datetime_iso,
            raw_payload=created.raw_payload,
        )

    async def get_available_slots(
        self,
        *,
        yclients_service_id: str,
        yclients_master_id: str,
        booking_date: str | date,
    ) -> list[BookingSlotItem]:
        """Return YClients slots for the selected service, master and date."""

        service_id = _clean_text(yclients_service_id)
        master_id = _clean_text(yclients_master_id)
        booking_date_value = _booking_date_iso(booking_date)
        if not service_id or not master_id or not booking_date_value:
            logger.warning(
                "Booking slots unavailable: operation=get_booking_slots service_id_present=%s "
                "master_id_present=%s date_present=%s",
                bool(service_id),
                bool(master_id),
                bool(booking_date_value),
            )
            raise BookingYClientsError(BOOKING_SLOTS_YCLIENTS_ERROR_TEXT)

        try:
            settings = self.load_active_settings_for_booking(operation="get_booking_slots")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking settings lookup failed: operation=get_booking_slots service_id=%s master_id=%s "
                "date=%s error_class=%s",
                service_id,
                master_id,
                booking_date_value,
                type(exc).__name__,
            )
            raise BookingSettingsMissingError(BOOKING_SLOTS_NOT_CONFIGURED_TEXT) from exc

        timezone_name = _timezone_name(settings.branch_timezone if settings else None)
        if is_past_date(booking_date_value, timezone_name=timezone_name):
            logger.info(
                "Booking slots skipped for past date: operation=get_booking_slots service_id=%s master_id=%s date=%s",
                service_id,
                master_id,
                booking_date_value,
            )
            return []

        if not has_required_yclients_credentials(settings):
            logger.info(
                "Booking slots unavailable: operation=get_booking_slots settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s service_id=%s master_id=%s date=%s",
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
                service_id,
                master_id,
                booking_date_value,
            )
            raise BookingSettingsMissingError(BOOKING_SLOTS_NOT_CONFIGURED_TEXT)

        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                slots_payload = await yclients.get_available_slots(
                    company_id=settings.company_id,
                    service_id=service_id,
                    staff_id=master_id,
                    date=booking_date_value,
                )
        except YClientsError as exc:
            logger.warning(
                "Booking YClients error: operation=get_booking_slots service_id=%s master_id=%s date=%s "
                "error_class=%s status_code=%s",
                service_id,
                master_id,
                booking_date_value,
                type(exc).__name__,
                exc.status_code,
            )
            raise BookingYClientsError(BOOKING_SLOTS_YCLIENTS_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "Booking unexpected YClients error: operation=get_booking_slots service_id=%s master_id=%s date=%s "
                "error_class=%s",
                service_id,
                master_id,
                booking_date_value,
                type(exc).__name__,
            )
            raise BookingYClientsError(BOOKING_SLOTS_YCLIENTS_ERROR_TEXT) from exc

        slots = [_normalize_slot(item, timezone_name=timezone_name) for item in slots_payload]
        now = datetime.now(_zoneinfo(timezone_name))
        slots = [
            item
            for item in slots
            if item.time and _slot_is_future(item, booking_date=booking_date_value, now=now)
        ]
        logger.info(
            "Booking slots loaded: operation=get_booking_slots service_id=%s master_id=%s date=%s slots_count=%s",
            service_id,
            master_id,
            booking_date_value,
            len(slots),
        )
        return slots

    async def get_service_categories_and_services(self) -> BookingCatalog:
        """Return available service categories and services from active YClients settings."""

        try:
            settings = self.load_active_settings_for_booking(operation="get_booking_catalog")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking settings lookup failed: operation=get_booking_catalog error_class=%s",
                type(exc).__name__,
            )
            raise BookingSettingsMissingError(BOOKING_NOT_CONFIGURED_TEXT) from exc

        if not has_required_yclients_credentials(settings):
            logger.info(
                "Booking catalog unavailable: operation=get_booking_catalog settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s",
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
            )
            raise BookingSettingsMissingError(BOOKING_NOT_CONFIGURED_TEXT)

        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                categories_payload = await yclients.get_service_categories(company_id=settings.company_id)
                services_payload = await yclients.get_available_services(company_id=settings.company_id)
        except YClientsError as exc:
            logger.warning(
                "Booking YClients error: operation=get_booking_catalog error_class=%s status_code=%s "
                "partner_token_present=%s user_token_present=%s",
                type(exc).__name__,
                exc.status_code,
                exc.partner_token_present,
                exc.user_token_present,
            )
            raise BookingYClientsError(BOOKING_YCLIENTS_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "Booking unexpected YClients error: operation=get_booking_catalog error_class=%s",
                type(exc).__name__,
            )
            raise BookingYClientsError(BOOKING_YCLIENTS_ERROR_TEXT) from exc

        services = [_normalize_service(item) for item in services_payload]
        services = [item for item in services if item.yclients_service_id and item.title]
        service_category_ids = {item.yclients_category_id for item in services if item.yclients_category_id}

        categories = [_normalize_category(item) for item in categories_payload]
        categories = [
            item
            for item in categories
            if item.yclients_category_id and item.title and item.yclients_category_id in service_category_ids
        ]
        if not categories:
            categories = _categories_from_services(services)

        logger.info(
            "Booking catalog loaded: operation=get_booking_catalog category_count=%s service_count=%s",
            len(categories),
            len(services),
        )
        return BookingCatalog(categories=categories, services=services)

    async def get_available_masters_for_service(self, yclients_service_id: str) -> list[BookingMasterItem]:
        """Return masters from YClients filtered by the selected service id."""

        service_id = _clean_text(yclients_service_id)
        if not service_id:
            logger.warning("Booking masters unavailable: operation=get_booking_masters service_id_present=False")
            raise BookingYClientsError(BOOKING_MASTERS_YCLIENTS_ERROR_TEXT)

        try:
            settings = self.load_active_settings_for_booking(operation="get_booking_masters")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking settings lookup failed: operation=get_booking_masters error_class=%s service_id=%s",
                type(exc).__name__,
                service_id,
            )
            raise BookingSettingsMissingError(BOOKING_MASTERS_NOT_CONFIGURED_TEXT) from exc

        if not has_required_yclients_credentials(settings):
            logger.info(
                "Booking masters unavailable: operation=get_booking_masters settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s service_id=%s",
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
                service_id,
            )
            raise BookingSettingsMissingError(BOOKING_MASTERS_NOT_CONFIGURED_TEXT)

        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                masters_payload = await yclients.get_available_masters(
                    company_id=settings.company_id,
                    service_id=service_id,
                )
        except YClientsError as exc:
            logger.warning(
                "Booking YClients error: operation=get_booking_masters service_id=%s error_class=%s status_code=%s "
                "partner_token_present=%s user_token_present=%s",
                service_id,
                type(exc).__name__,
                exc.status_code,
                exc.partner_token_present,
                exc.user_token_present,
            )
            raise BookingYClientsError(BOOKING_MASTERS_YCLIENTS_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "Booking unexpected YClients error: operation=get_booking_masters service_id=%s error_class=%s",
                service_id,
                type(exc).__name__,
            )
            raise BookingYClientsError(BOOKING_MASTERS_YCLIENTS_ERROR_TEXT) from exc

        masters = [_normalize_master(item) for item in masters_payload]
        masters = [item for item in masters if item.yclients_master_id and item.title]
        logger.info(
            "Booking masters loaded: operation=get_booking_masters service_id=%s masters_count=%s",
            service_id,
            len(masters),
        )
        return masters



def build_booking_payload(
    *,
    yclients_service_id: str,
    yclients_master_id: str,
    booking_date: str | date,
    booking_slot: str,
    client_name: str,
    client_phone: str,
    selected_datetime: str | None = None,
    comment: str = "",
) -> dict[str, Any]:
    """Build YClients service-layer kwargs for final record creation."""

    service_id = _clean_text(yclients_service_id)
    master_id = _clean_text(yclients_master_id)
    booking_date_value = _booking_date_iso(booking_date)
    slot_time = _normalize_slot_time(booking_slot, timezone_name=DEFAULT_BRANCH_TIMEZONE) or _clean_text(booking_slot)
    datetime_iso = _clean_text(selected_datetime)
    if not datetime_iso and booking_date_value and slot_time:
        datetime_iso = f"{booking_date_value} {slot_time}:00"
    phone = normalize_phone(_clean_text(client_phone))
    fullname = _clean_text(client_name) or "Гость"
    if not service_id or not master_id or not datetime_iso or not phone:
        raise BookingCreateError(BOOKING_CREATE_YCLIENTS_ERROR_TEXT)
    return {
        "service_id": service_id,
        "staff_id": master_id,
        "datetime_iso": datetime_iso,
        "phone": phone,
        "fullname": fullname,
        "comment": comment or MAX_BOOKING_COMMENT_MARKER,
    }


def format_booking_summary(booking_state: dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format final confirmation text in the Telegram reference style."""

    return _format_booking_details("Подтвердите запись, пожалуйста 🙂\n", booking_state, timezone_name=timezone_name)


def format_booking_success(booking_state: dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format successful booking text in the Telegram reference style."""

    return _format_booking_details("✅ Готово! Вы записаны 💈\n", booking_state, timezone_name=timezone_name)


def extract_yclients_record_id(created: YClientsBookingRecord | CreatedBooking | dict[str, Any] | list[Any]) -> str | None:
    """Extract a YClients record id from known create-record response shapes."""

    if isinstance(created, CreatedBooking):
        return _clean_text(created.yclients_record_id) or None
    if isinstance(created, YClientsBookingRecord):
        return _clean_text(created.record_id) or None
    return _find_first_key(created, ("record_id", "id", "booking_id", "visit_id"))


def extract_yclients_client_id(created: YClientsBookingRecord | CreatedBooking | dict[str, Any] | list[Any]) -> str | None:
    """Extract a YClients client id from known create-record response shapes."""

    if isinstance(created, CreatedBooking):
        return _clean_text(created.yclients_client_id) or None
    if isinstance(created, YClientsBookingRecord):
        return _find_first_key(created.raw_payload, ("client_id", "client", "yclients_client_id"))
    return _find_first_key(created, ("client_id", "client", "yclients_client_id"))



def build_booking_dates(*, days: int = 14, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> list[date]:
    """Build today and future booking dates in the branch timezone."""

    total_days = max(1, days)
    today = datetime.now(_zoneinfo(timezone_name)).date()
    return [today + timedelta(days=offset) for offset in range(total_days)]


def format_date_button(value: date | str, *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format a date button in the Telegram reference style."""

    current = _date_value(value)
    today = datetime.now(_zoneinfo(timezone_name)).date()
    if current == today:
        return f"📅 Сегодня, {current.strftime('%d.%m')}"
    if current == today + timedelta(days=1):
        return f"📅 Завтра, {current.strftime('%d.%m')}"
    return f"📅 {_RU_WEEKDAYS[current.weekday()]} {current.strftime('%d.%m')}"


def format_slot_button(slot: BookingSlotItem | YClientsSlot | dict[str, Any]) -> str:
    """Format a slot button in the Telegram reference style."""

    normalized = _normalize_slot(slot, timezone_name=DEFAULT_BRANCH_TIMEZONE)
    return f"🕒 {normalized.time}" if normalized.time else "🕒 —"


def is_past_date(value: date | str, *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> bool:
    """Return True when value is before today in branch timezone."""

    return _date_value(value) < datetime.now(_zoneinfo(timezone_name)).date()


def _normalize_slot(slot: BookingSlotItem | YClientsSlot | dict[str, Any], *, timezone_name: str) -> BookingSlotItem:
    if isinstance(slot, BookingSlotItem):
        return slot
    if isinstance(slot, YClientsSlot):
        datetime_iso = _clean_text(slot.datetime) or None
        slot_time = _normalize_slot_time(slot.time or datetime_iso, timezone_name=timezone_name)
        return BookingSlotItem(time=slot_time or "", datetime_iso=datetime_iso, raw=_safe_raw_slot(slot.raw))
    datetime_iso = _clean_text(slot.get("datetime") or slot.get("date") or slot.get("time")) or None
    slot_time = _normalize_slot_time(slot.get("time") or slot.get("datetime") or slot.get("date"), timezone_name=timezone_name)
    return BookingSlotItem(time=slot_time or "", datetime_iso=datetime_iso, raw=_safe_raw_slot(slot))


def _normalize_slot_time(value: Any, *, timezone_name: str) -> str | None:
    raw = _clean_text(value)
    if not raw:
        return None
    parsed = _parse_datetime(raw, timezone_name=timezone_name)
    if parsed is not None:
        return parsed.astimezone(_zoneinfo(timezone_name)).strftime("%H:%M")
    for separator in ("T", " "):
        if separator in raw:
            raw = raw.split(separator, 1)[1]
    raw = raw[:5]
    if len(raw) == 5 and raw[2] == ":" and raw.replace(":", "").isdigit():
        return raw
    return None


def _slot_is_future(slot: BookingSlotItem, *, booking_date: str, now: datetime) -> bool:
    parsed = _parse_datetime(slot.datetime_iso, timezone_name=str(now.tzinfo) if now.tzinfo else DEFAULT_BRANCH_TIMEZONE)
    if parsed is None:
        parsed = _parse_datetime(f"{booking_date}T{slot.time}:00", timezone_name=str(now.tzinfo) if now.tzinfo else DEFAULT_BRANCH_TIMEZONE)
    if parsed is None:
        return False
    return parsed.astimezone(now.tzinfo) > now


def _parse_datetime(value: Any, *, timezone_name: str) -> datetime | None:
    raw = _clean_text(value)
    if not raw or len(raw) <= 5:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zoneinfo(timezone_name))
    return parsed


def _booking_date_iso(value: date | str) -> str:
    try:
        return _date_value(value).isoformat()
    except (TypeError, ValueError):
        return ""


def _date_value(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def _timezone_name(value: str | None) -> str:
    candidate = _clean_text(value) or DEFAULT_BRANCH_TIMEZONE
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        logger.warning("Booking invalid branch timezone: timezone=%s", candidate)
        return DEFAULT_BRANCH_TIMEZONE
    return candidate


def _zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)


def _safe_raw_slot(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {key: raw.get(key) for key in ("time", "datetime", "date", "staff_id") if key in raw}

def group_services_by_category(
    categories: list[BookingCategory] | list[dict[str, Any]],
    services: list[BookingServiceItem] | list[dict[str, Any]],
) -> dict[str, list[BookingServiceItem]]:
    """Group normalized services by category id, accepting dicts for simple smoke checks."""

    normalized_categories = [_normalize_category(item) for item in categories]
    normalized_services = [_normalize_service(item) for item in services]
    category_ids = {category.yclients_category_id for category in normalized_categories}
    grouped: dict[str, list[BookingServiceItem]] = {category_id: [] for category_id in category_ids if category_id}
    for service in normalized_services:
        category_id = service.yclients_category_id
        if category_id and category_id in grouped:
            grouped[category_id].append(service)
    return grouped


def has_available_masters(masters: list[BookingMasterItem] | list[dict[str, Any]]) -> bool:
    """Return True when YClients returned at least one displayable master."""

    normalized = [_normalize_master(master) for master in masters]
    return any(master.yclients_master_id and master.title for master in normalized)


def format_master_title(master: BookingMasterItem | dict[str, Any]) -> str:
    """Format a booking master button title in the reference bot style."""

    normalized = _normalize_master(master)
    suffix = f" ({normalized.specialization})" if normalized.specialization else ""
    return f"💈 {normalized.title}{suffix}"


def has_available_services(catalog: BookingCatalog) -> bool:
    """Return True when YClients returned at least one displayable service."""

    return bool(catalog.services)


def format_service_title(service: BookingServiceItem | dict[str, Any]) -> str:
    """Format a booking service button title in the reference bot style."""

    normalized = _normalize_service(service)
    details: list[str] = []
    price = _format_price(normalized)
    if price:
        details.append(price)
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{normalized.title}{suffix}"


def _format_booking_details(header: str, booking_state: dict[str, Any], *, timezone_name: str) -> str:
    service_name = _clean_text(booking_state.get("selected_service_name") or booking_state.get("service_name")) or "—"
    master_name = _clean_text(
        booking_state.get("selected_master_name")
        or booking_state.get("selected_staff_name")
        or booking_state.get("master_name")
    ) or "Любой мастер"
    booking_date_value, booking_time_value = _display_date_time(booking_state, timezone_name=timezone_name)
    lines = [header] if header else []
    lines.extend(
        [
            f"✂️ Услуга: {service_name}",
            f"👤 Мастер: {master_name}",
            f"📅 Дата: {booking_date_value}",
            f"🕒 Время: {booking_time_value}",
        ]
    )
    return "\n".join(lines)


def _display_date_time(booking_state: dict[str, Any], *, timezone_name: str) -> tuple[str, str]:
    selected_datetime = _clean_text(
        booking_state.get("selected_booking_datetime")
        or booking_state.get("selected_datetime")
        or booking_state.get("booking_datetime")
    )
    parsed = _parse_datetime(selected_datetime, timezone_name=timezone_name)
    if parsed is not None:
        local_dt = parsed.astimezone(_zoneinfo(timezone_name))
        return local_dt.strftime("%d.%m.%Y"), local_dt.strftime("%H:%M")
    raw_date = _clean_text(
        booking_state.get("selected_booking_date")
        or booking_state.get("selected_date")
        or booking_state.get("booking_date")
    )
    raw_time = _clean_text(
        booking_state.get("selected_booking_slot_time")
        or booking_state.get("selected_slot_time")
        or booking_state.get("booking_slot")
    )
    try:
        display_date = _date_value(raw_date).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        display_date = raw_date or "—"
    return display_date, raw_time or "—"


def _find_first_key(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if key == "client" and isinstance(candidate, dict):
                nested = _find_first_key(candidate, ("id", "client_id"))
                if nested:
                    return nested
            elif key == "client" and candidate not in (None, "") and not isinstance(candidate, (dict, list)):
                candidate_text = _clean_text(candidate)
                if candidate_text:
                    return candidate_text
            else:
                candidate_text = _clean_text(candidate)
                if candidate_text:
                    return candidate_text
        for nested_value in value.values():
            nested = _find_first_key(nested_value, keys)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_first_key(item, keys)
            if nested:
                return nested
    return None



def _normalize_master(item: BookingMasterItem | YClientsStaff | dict[str, Any]) -> BookingMasterItem:
    if isinstance(item, BookingMasterItem):
        return item
    if isinstance(item, YClientsStaff):
        return BookingMasterItem(
            yclients_master_id=item.id,
            title=item.name or "",
            specialization=item.specialization,
        )
    return BookingMasterItem(
        yclients_master_id=_clean_text(
            item.get("yclients_master_id")
            or item.get("yclients_staff_id")
            or item.get("id")
            or item.get("staff_id")
            or item.get("master_id")
        ),
        title=_clean_text(item.get("title") or item.get("name")),
        specialization=_clean_text(item.get("specialization") or item.get("position") or item.get("profession")) or None,
    )


def _normalize_category(item: BookingCategory | YClientsServiceCategory | dict[str, Any]) -> BookingCategory:
    if isinstance(item, BookingCategory):
        return item
    if isinstance(item, YClientsServiceCategory):
        return BookingCategory(yclients_category_id=item.id, title=item.title or "")
    category_id = _clean_text(item.get("yclients_category_id") or item.get("id") or item.get("category_id"))
    title = _clean_text(item.get("title") or item.get("name"))
    return BookingCategory(yclients_category_id=category_id, title=title)


def _normalize_service(item: BookingServiceItem | YClientsService | dict[str, Any]) -> BookingServiceItem:
    if isinstance(item, BookingServiceItem):
        return item
    if isinstance(item, YClientsService):
        category_title = _clean_text(item.raw.get("category_title") or item.raw.get("category_name")) or None
        return BookingServiceItem(
            yclients_service_id=item.id,
            title=item.title or "",
            yclients_category_id=item.category_id,
            category_title=category_title,
            price_min=item.price_min,
            price_max=item.price_max,
        )
    return BookingServiceItem(
        yclients_service_id=_clean_text(item.get("yclients_service_id") or item.get("id") or item.get("service_id")),
        title=_clean_text(item.get("title") or item.get("name")),
        yclients_category_id=_clean_text(item.get("yclients_category_id") or item.get("category_id") or item.get("category")) or None,
        category_title=_clean_text(item.get("category_title") or item.get("category_name")) or None,
        price_min=item.get("price_min") or item.get("price") or item.get("cost"),
        price_max=item.get("price_max") or item.get("price") or item.get("cost"),
    )


def _categories_from_services(services: list[BookingServiceItem]) -> list[BookingCategory]:
    grouped: dict[str, str] = {}
    for service in services:
        category_id = service.yclients_category_id
        if not category_id or _is_empty_category(category_id, service.category_title or ""):
            continue
        grouped[category_id] = service.category_title or "Другое"
    return [BookingCategory(yclients_category_id=category_id, title=title) for category_id, title in sorted(grouped.items(), key=lambda item: item[1])]


def _is_empty_category(category_id: str, title: str) -> bool:
    if not title.strip():
        return True
    if title.strip().lower() == "без группы":
        return True
    return category_id.strip().lower() in {"", "0", "none", "null"}


def _format_price(service: BookingServiceItem) -> str | None:
    price = service.price_min if service.price_min not in (None, "") else service.price_max
    if price in (None, ""):
        return None
    return f"{price} ₽"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
