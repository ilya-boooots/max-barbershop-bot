"""Transport-neutral service for viewing future YClients bookings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from max_barbershop_bot.integrations.yclients.client import YClientsClient
from max_barbershop_bot.integrations.yclients.exceptions import (
    YClientsAuthError,
    YClientsError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsValidationError,
)
from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.integrations.yclients.utils import safe_str
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.repositories.yclients_settings import DEFAULT_BRANCH_TIMEZONE, YClientsSettingsRepository

logger = logging.getLogger(__name__)

MY_BOOKINGS_NO_PROFILE_TEXT = "Не получилось найти ваши данные для записей 🙏\n\nНажмите /start и пройдите регистрацию заново."
MY_BOOKINGS_LOAD_ERROR_TEXT = "Не удалось загрузить ваши записи 🙏\n\nПожалуйста, попробуйте позже."
MY_BOOKINGS_EMPTY_TEXT = "📭 У вас пока нет активных записей."
MY_BOOKINGS_TITLE_TEXT = "📅 Ваши записи"
MY_BOOKING_NOT_FOUND_TEXT = "Запись не найдена"
MY_BOOKING_CANCEL_IN_PROGRESS_TEXT = "Отмена уже выполняется, подождите немного ⏳"
MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT = "Эту запись нельзя отменить через бота 🙏\n\nПожалуйста, напишите администратору."
MY_BOOKING_CANCEL_ALREADY_TEXT = "Эта запись уже отменена."
MY_BOOKING_CANCEL_ERROR_TEXT = "Не удалось отменить запись 🙏\n\nПожалуйста, попробуйте позже или напишите администратору."

_STATUS_LABELS = {
    "active": "Подтверждена",
    "confirmed": "Подтверждена",
    "approve": "Подтверждена",
    "approved": "Подтверждена",
    "pending": "Ожидает подтверждения",
    "new": "Новая",
    "cancelled": "Отменена",
    "canceled": "Отменена",
    "done": "Завершена",
    "completed": "Завершена",
    "visit": "Завершена",
    "no_show": "Неявка",
}
_CANCELLED_OR_PAST_STATUSES = {"cancelled", "canceled", "done", "completed", "visit"}


class MyBookingsError(RuntimeError):
    """Clean domain error safe for the MAX flow."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class MyBookingsProfileMissingError(MyBookingsError):
    """Raised when there is no client id or phone for booking lookup."""


class MyBookingsLoadError(MyBookingsError):
    """Raised when YClients/settings cannot provide records safely."""


class MyBookingCancellationError(MyBookingsError):
    """Raised when YClients cannot cancel a selected record."""


class MyBookingCancellationNotAllowedError(MyBookingCancellationError):
    """Raised when online cancellation is not allowed by YClients."""


class MyBookingAlreadyCancelledError(MyBookingCancellationError):
    """Raised when the selected record is already cancelled or gone."""


@dataclass(frozen=True)
class MyBookingItem:
    """Future YClients booking normalized for display."""

    yclients_record_id: str
    booking_datetime: datetime
    service_name: str
    master_name: str | None
    status: str | None
    raw_status: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class MyBookingsResult:
    """Result of loading future bookings for one user."""

    bookings: list[MyBookingItem]
    branch_timezone: str
    yclients_client_id: str | None
    phone_exists: bool

    @property
    def is_empty(self) -> bool:
        return not self.bookings


class MyBookingsService:
    """Load and format future YClients records without bot transport dependencies."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    async def get_future_bookings_for_user(self, user: User | None, *, platform_user_id: str | None = None) -> MyBookingsResult:
        """Return future YClients bookings for a stored MAX user profile."""

        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        if not yclients_client_id and not phone:
            logger.info(
                "My bookings profile unresolved: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
            )
            raise MyBookingsProfileMissingError(MY_BOOKINGS_NO_PROFILE_TEXT)

        try:
            settings = self._settings_repository.get_active()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "My bookings settings lookup failed: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s error_class=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
                type(exc).__name__,
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT) from exc

        timezone_name = _timezone_name(settings.branch_timezone if settings else None)
        if settings is None or not settings.company_id or not settings.partner_token or not settings.user_token:
            logger.info(
                "My bookings unavailable: operation=get_my_bookings platform_user_id=%s settings_present=%s "
                "company_id_present=%s partner_token_present=%s user_token_present=%s "
                "yclients_client_id_present=%s phone_present=%s",
                platform_user_id,
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
                bool(yclients_client_id),
                bool(phone),
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT)

        now = datetime.now(_zoneinfo(timezone_name))
        try:
            async with YClientsClient(
                partner_token=settings.partner_token,
                user_token=settings.user_token,
                company_id=settings.company_id,
            ) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                payload = await yclients.get_future_records(
                    company_id=settings.company_id,
                    yclients_client_id=yclients_client_id,
                    phone=phone if not yclients_client_id else None,
                    start_date=now.date().isoformat(),
                    end_date=(now.date() + timedelta(days=365)).isoformat(),
                    page=1,
                    count=200,
                )
        except YClientsError as exc:
            logger.warning(
                "My bookings YClients error: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s error_class=%s status_code=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            logger.warning(
                "My bookings unexpected error: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s error_class=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
                type(exc).__name__,
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT) from exc

        bookings = [_booking_from_payload(item, timezone_name=timezone_name) for item in _extract_record_rows(payload)]
        future = [item for item in bookings if item is not None and is_future_booking(item, timezone_name=timezone_name, now=now)]
        future = sort_bookings_by_datetime(future, timezone_name=timezone_name)
        logger.info(
            "My bookings loaded: operation=get_my_bookings platform_user_id=%s "
            "yclients_client_id_present=%s phone_present=%s future_bookings_count=%s branch_timezone=%s",
            platform_user_id,
            bool(yclients_client_id),
            bool(phone),
            len(future),
            timezone_name,
        )
        return MyBookingsResult(
            bookings=future,
            branch_timezone=timezone_name,
            yclients_client_id=yclients_client_id,
            phone_exists=bool(phone),
        )

    async def cancel_booking_for_user(
        self,
        user: User | None,
        *,
        yclients_record_id: str,
        platform_user_id: str | None = None,
    ) -> str | None:
        """Cancel one future YClients record and return the resulting status when present."""

        record_id = _clean_text(yclients_record_id)
        if not record_id:
            raise MyBookingCancellationError(MY_BOOKING_NOT_FOUND_TEXT)

        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        if not yclients_client_id and not phone:
            raise MyBookingsProfileMissingError(MY_BOOKINGS_NO_PROFILE_TEXT)

        try:
            settings = self._settings_repository.get_active()
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking cancellation settings lookup failed: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
            )
            raise MyBookingCancellationError(MY_BOOKING_CANCEL_ERROR_TEXT) from exc

        if settings is None or not settings.company_id or not settings.partner_token or not settings.user_token:
            logger.info(
                "Booking cancellation unavailable: operation=cancel_booking platform_user_id=%s yclients_record_id=%s "
                "settings_present=%s company_id_present=%s partner_token_present=%s user_token_present=%s",
                platform_user_id,
                record_id,
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
            )
            raise MyBookingCancellationError(MY_BOOKING_CANCEL_ERROR_TEXT)

        try:
            async with YClientsClient(
                partner_token=settings.partner_token,
                user_token=settings.user_token,
                company_id=settings.company_id,
            ) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                result = await yclients.cancel_booking(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                )
        except YClientsNotFoundError as exc:
            logger.info(
                "Booking cancellation record not found: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s status_code=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingAlreadyCancelledError(MY_BOOKING_CANCEL_ALREADY_TEXT) from exc
        except YClientsValidationError as exc:
            logger.info(
                "Booking cancellation rejected: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s status_code=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingCancellationNotAllowedError(MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT) from exc
        except (YClientsAuthError, YClientsRateLimitError, YClientsServerError, YClientsTransportError) as exc:
            logger.warning(
                "Booking cancellation YClients error: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s status_code=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingCancellationError(MY_BOOKING_CANCEL_ERROR_TEXT) from exc
        except YClientsError as exc:
            logger.warning(
                "Booking cancellation integration error: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s status_code=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingCancellationError(MY_BOOKING_CANCEL_ERROR_TEXT) from exc

        logger.info(
            "Booking cancelled in YClients: operation=cancel_booking platform_user_id=%s "
            "yclients_record_id=%s result_status=%s",
            platform_user_id,
            record_id,
            result.status,
        )
        return result.status


def format_booking_status(status: Any) -> str:
    """Return a friendly Russian booking status label."""

    raw = _clean_text(status)
    if not raw:
        return "Неизвестен"
    return _STATUS_LABELS.get(raw.lower(), raw if _is_safe_status(raw) else "Неизвестен")


def parse_booking_datetime(item: dict[str, Any] | MyBookingItem, *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> datetime | None:
    """Parse a YClients record datetime in the branch timezone."""

    if isinstance(item, MyBookingItem):
        return item.booking_datetime.astimezone(_zoneinfo(timezone_name))

    raw_value = _clean_text(item.get("datetime") or item.get("date_time") or item.get("start"))
    if not raw_value:
        booking_date = _clean_text(item.get("date"))
        booking_time = _clean_text(item.get("time") or item.get("booking_time") or item.get("seance_time"))
        if booking_date and booking_time:
            raw_value = f"{booking_date} {booking_time}"
        elif booking_date:
            raw_value = booking_date
    if not raw_value:
        return None

    normalized = raw_value.replace("T", " ").replace("Z", "+00:00")
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in formats:
            try:
                parsed = datetime.strptime(normalized, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None

    zone = _zoneinfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def is_future_booking(
    item: dict[str, Any] | MyBookingItem,
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """Return whether a record is future and not cancelled/completed."""

    status = item.raw_status if isinstance(item, MyBookingItem) else _clean_text(item.get("status") or item.get("record_status") or item.get("state"))
    if _clean_text(status).lower() in _CANCELLED_OR_PAST_STATUSES:
        return False
    parsed = parse_booking_datetime(item, timezone_name=timezone_name)
    if parsed is None:
        return False
    current = now or datetime.now(_zoneinfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=_zoneinfo(timezone_name))
    return parsed >= current.astimezone(_zoneinfo(timezone_name))


def sort_bookings_by_datetime(
    items: list[dict[str, Any]] | list[MyBookingItem],
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
) -> list[dict[str, Any]] | list[MyBookingItem]:
    """Sort records by booking datetime in the branch timezone."""

    return sorted(items, key=lambda item: parse_booking_datetime(item, timezone_name=timezone_name) or datetime.max.replace(tzinfo=_zoneinfo(timezone_name)))


def format_booking_item(item: MyBookingItem, *, index: int, timezone_name: str) -> str:
    """Format one booking card in the reference UX style."""

    booking_datetime = item.booking_datetime.astimezone(_zoneinfo(timezone_name))
    return "\n".join(
        [
            f"{index}. ✂️ Услуга: {item.service_name}",
            f"   👤 Мастер: {item.master_name or 'Любой мастер'}",
            f"   📅 Дата: {booking_datetime.strftime('%d.%m.%Y')}",
            f"   🕒 Время: {booking_datetime.strftime('%H:%M')}",
            f"   🧾 Статус: {format_booking_status(item.raw_status or item.status)}",
        ]
    )


def format_bookings_screen(bookings: list[MyBookingItem], *, timezone_name: str) -> str:
    """Format the full future bookings screen."""

    if not bookings:
        return MY_BOOKINGS_EMPTY_TEXT
    cards = [format_booking_item(item, index=index, timezone_name=timezone_name) for index, item in enumerate(bookings, start=1)]
    return f"{MY_BOOKINGS_TITLE_TEXT}\n\n" + "\n\n".join(cards)


def format_booking_details_text(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format selected booking details in the Telegram reference style."""

    display = booking_display_data(booking, timezone_name=timezone_name)
    return "\n".join(
        [
            "📋 Активная запись",
            "",
            f"✂️ Услуга: {display['service_name']}",
            f"👤 Мастер: {display['master_name'] or 'Любой мастер'}",
            f"📅 Дата: {display['date']}",
            f"🕒 Время: {display['time']}",
            f"🧾 Статус: {display['status']}",
        ]
    )


def format_cancel_confirmation_text(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format cancellation confirmation text."""

    display = booking_display_data(booking, timezone_name=timezone_name)
    return "\n".join(
        [
            "Вы точно хотите отменить запись? ❌",
            "",
            f"Услуга: {display['service_name']}",
            f"Мастер: {display['master_name'] or 'Любой мастер'}",
            f"Дата: {display['date']}",
            f"Время: {display['time']}",
        ]
    )


def format_cancel_success_text(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format successful cancellation message."""

    display = booking_display_data(booking, timezone_name=timezone_name)
    return "\n".join(
        [
            "Запись отменена ✅",
            "",
            f"✂️ {display['service_name']}",
            f"📅 {display['date']} в {display['time']}",
        ]
    )


def booking_display_data(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> dict[str, str | None]:
    """Return safe display fields for state and smoke tests."""

    if isinstance(booking, MyBookingItem):
        booking_datetime = booking.booking_datetime.astimezone(_zoneinfo(timezone_name))
        return {
            "yclients_record_id": booking.yclients_record_id,
            "service_name": booking.service_name,
            "master_name": booking.master_name,
            "date": booking_datetime.strftime("%d.%m.%Y"),
            "time": booking_datetime.strftime("%H:%M"),
            "status": format_booking_status(booking.raw_status or booking.status),
        }

    booking_date = _clean_text(booking.get("date"))
    booking_time = _clean_text(booking.get("time") or booking.get("booking_time"))
    parsed = parse_booking_datetime(booking, timezone_name=timezone_name)
    if parsed is not None:
        booking_date = booking_date or parsed.strftime("%d.%m.%Y")
        booking_time = booking_time or parsed.strftime("%H:%M")

    return {
        "yclients_record_id": _clean_text(booking.get("yclients_record_id") or booking.get("record_id") or booking.get("id")),
        "service_name": _clean_text(booking.get("service_name")) or _extract_service_name(booking),
        "master_name": _clean_text(booking.get("master_name")) or _extract_master_name(booking),
        "date": booking_date or "—",
        "time": booking_time or "—",
        "status": format_booking_status(booking.get("status") or booking.get("raw_status")),
    }


def _booking_from_payload(item: dict[str, Any], *, timezone_name: str) -> MyBookingItem | None:
    record_id = _clean_text(item.get("record_id") or item.get("id") or item.get("booking_id") or item.get("visit_id"))
    booking_datetime = parse_booking_datetime(item, timezone_name=timezone_name)
    if not record_id or booking_datetime is None:
        return None
    raw_status = _clean_text(item.get("status") or item.get("record_status") or item.get("state")) or None
    return MyBookingItem(
        yclients_record_id=record_id,
        booking_datetime=booking_datetime,
        service_name=_extract_service_name(item),
        master_name=_extract_master_name(item),
        status=format_booking_status(raw_status),
        raw_status=raw_status,
        raw=item,
    )


def _extract_record_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "records", "items", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_record_rows(value)
            if nested:
                return nested
    return [payload]


def _extract_service_name(item: dict[str, Any]) -> str:
    services = item.get("services")
    if isinstance(services, list) and services:
        names = []
        for service in services:
            if isinstance(service, dict):
                name = _clean_text(service.get("title") or service.get("name"))
                if name:
                    names.append(name)
        if names:
            return ", ".join(names)
    service = item.get("service")
    if isinstance(service, dict):
        name = _clean_text(service.get("title") or service.get("name"))
        if name:
            return name
    return _clean_text(item.get("service_name") or item.get("service") or item.get("title")) or "Услуга"


def _extract_master_name(item: dict[str, Any]) -> str | None:
    for key in ("staff_name", "master_name", "employee_name"):
        value = _clean_text(item.get(key))
        if value:
            return value
    for key in ("staff", "master", "employee"):
        value = item.get(key)
        if isinstance(value, dict):
            name = _clean_text(value.get("name") or value.get("title") or value.get("fullname"))
            if name:
                return name
    return None


def _is_safe_status(status: str) -> bool:
    if status.isdigit():
        return False
    return all(ch.isalnum() or ch in " _-А-Яа-яЁё" for ch in status) and len(status) <= 40


def _timezone_name(value: str | None) -> str:
    raw = _clean_text(value) or DEFAULT_BRANCH_TIMEZONE
    try:
        ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return DEFAULT_BRANCH_TIMEZONE
    return raw


def _zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_BRANCH_TIMEZONE)


def _clean_text(value: Any) -> str:
    return safe_str(value)
