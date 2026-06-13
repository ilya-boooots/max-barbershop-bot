"""Transport-neutral service for viewing future YClients bookings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from max_barbershop_bot.integrations.yclients.utils import normalize_phone, safe_str
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactsService
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE, normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

MY_BOOKINGS_NO_PROFILE_TEXT = "Не получилось найти ваши данные для записей 🙏\n\nНажмите /start и пройдите регистрацию заново."
MY_BOOKINGS_LOAD_ERROR_TEXT = "Не удалось загрузить ваши записи 🙏\n\nПожалуйста, попробуйте позже."
MY_BOOKINGS_EMPTY_TEXT = "📭 У вас пока нет активных записей."
MY_BOOKINGS_TITLE_TEXT = "📅 Ваши записи"
MY_BOOKING_NOT_FOUND_TEXT = "Эта запись уже неактуальна 🙏\n\nОткройте список записей заново."
MY_BOOKING_CANCEL_IN_PROGRESS_TEXT = "Отмена уже выполняется, подождите немного ⏳"
MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT = "Эту запись нельзя отменить через бота 🙏\n\nПожалуйста, напишите администратору."
MY_BOOKING_CANCEL_ALREADY_TEXT = "Эта запись уже отменена."
MY_BOOKING_CANCEL_ERROR_TEXT = "Не удалось отменить запись 🙏\n\nПожалуйста, попробуйте позже или напишите администратору."
MY_BOOKING_RESCHEDULE_UNAVAILABLE_TEXT = "Перенос записи через бота пока недоступен 🙏\n\nПожалуйста, напишите администратору."
MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT = "Не получилось подготовить перенос записи 🙏\n\nПожалуйста, напишите администратору."
MY_BOOKING_RESCHEDULE_ERROR_TEXT = "Не удалось перенести запись 🙏\n\nВозможно, это время уже заняли. Попробуйте выбрать другой слот."
MY_BOOKING_RESCHEDULE_NOT_ALLOWED_TEXT = "Эту запись уже нельзя перенести 🙏\n\nВы можете отменить её и создать новую запись."
MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT = (
    "Новая запись создана, но старую не удалось отменить автоматически 🙏\n\n"
    "Администратор уже получит информацию для проверки."
)
MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT = "Перенос уже выполняется, подождите немного ⏳"
MY_BOOKING_RESCHEDULE_DATES_TEXT = "🔁 Перенос записи\n\nВыберите новую дату:"
MY_BOOKING_RESCHEDULE_SLOTS_TEXT = "🔁 Перенос записи\n\nВыберите новое время:"
MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT = "На эту дату свободного времени нет 🙏\n\nВыберите другой день."
MY_BOOKING_REPEAT_PREPARE_ERROR_TEXT = "Не получилось подготовить повтор записи 🙏\n\nПожалуйста, попробуйте позже."
MY_BOOKING_REPEAT_SERVICE_UNAVAILABLE_TEXT = "Эта услуга сейчас недоступна 🙏\n\nВыберите другую услугу для записи."
MY_BOOKING_REPEAT_MASTER_UNAVAILABLE_TEXT = "Этот мастер сейчас недоступен для повторной записи 🙏\n\nВыберите другого мастера или услугу."
RESCHEDULE_CREATE_MARKER = "Клиент перенёс запись из MAX бота"
RESCHEDULE_CANCEL_MARKER_PREFIX = "Запись перенесена из MAX бота"

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


class MyBookingRescheduleError(MyBookingsError):
    """Raised when YClients cannot reschedule a selected record."""


class MyBookingReschedulePrepareError(MyBookingRescheduleError):
    """Raised when required record data cannot be prepared for reschedule."""


class MyBookingRescheduleNotAllowedError(MyBookingRescheduleError):
    """Raised when YClients does not allow record update/reschedule."""


@dataclass(frozen=True)
class MyBookingItem:
    """Future YClients booking normalized for display."""

    yclients_record_id: str
    booking_datetime: datetime
    service_name: str
    master_name: str | None
    status: str | None
    raw_status: str | None = None
    duration_minutes: int | None = None
    price: str | None = None
    address: str | None = None
    phone: str | None = None
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
            settings = load_active_yclients_settings(self._settings_repository, operation="get_my_bookings")
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
        if not has_required_yclients_credentials(settings):
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
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                if not yclients_client_id and phone:
                    yclients_client_id = await _resolve_client_id_by_phone(yclients, settings.company_id, phone)
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

        contacts = await ContactsService(self._settings_repository).get_contacts()
        bookings = [
            _booking_from_payload(item, timezone_name=timezone_name, address=contacts.address, phone=contacts.phone)
            for item in _extract_record_rows(payload)
        ]
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
        cancellation_marker: str | None = None,
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
            settings = load_active_yclients_settings(self._settings_repository, operation="get_my_bookings")
        except Exception as exc:  # noqa: BLE001 - keep technical details away from users.
            logger.warning(
                "Booking cancellation settings lookup failed: operation=cancel_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
            )
            raise MyBookingCancellationError(MY_BOOKING_CANCEL_ERROR_TEXT) from exc

        if not has_required_yclients_credentials(settings):
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
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                result = await yclients.cancel_booking(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                    cancellation_marker=cancellation_marker,
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


    async def prepare_reschedule_context(
        self,
        user: User | None,
        *,
        yclients_record_id: str,
        platform_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Load selected record from YClients and extract fields needed for direct reschedule."""

        record_id = _clean_text(yclients_record_id)
        if not record_id:
            raise MyBookingReschedulePrepareError(MY_BOOKING_NOT_FOUND_TEXT)
        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        if not yclients_client_id and not phone:
            raise MyBookingsProfileMissingError(MY_BOOKINGS_NO_PROFILE_TEXT)

        settings = self._active_settings_for_reschedule(platform_user_id=platform_user_id, record_id=record_id)
        timezone_name = _timezone_name(settings.branch_timezone)
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                details = await yclients.get_booking_details(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                )
        except YClientsError as exc:
            logger.warning(
                "Booking reschedule details failed: operation=prepare_reschedule platform_user_id=%s "
                "yclients_record_id=%s error_class=%s status_code=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingReschedulePrepareError(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT) from exc
        except Exception as exc:  # noqa: BLE001 - keep raw details away from users.
            logger.warning(
                "Booking reschedule details unexpected error: operation=prepare_reschedule platform_user_id=%s "
                "yclients_record_id=%s error_class=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
            )
            raise MyBookingReschedulePrepareError(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT) from exc

        row = _extract_record_detail_row(details)
        if not row:
            raise MyBookingReschedulePrepareError(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT)

        service_ids = _extract_service_ids(row)
        staff_id = _extract_staff_id(row)
        client_data = _extract_client_data(row)
        seance_length = _extract_seance_length(row)
        old_datetime = parse_booking_datetime(row, timezone_name=timezone_name)
        if not service_ids or not staff_id or not client_data or not seance_length or old_datetime is None:
            logger.info(
                "Booking reschedule context incomplete: operation=prepare_reschedule platform_user_id=%s "
                "yclients_record_id=%s service_ids_present=%s staff_id_present=%s client_present=%s "
                "seance_length_present=%s old_datetime_present=%s",
                platform_user_id,
                record_id,
                bool(service_ids),
                bool(staff_id),
                bool(client_data),
                bool(seance_length),
                old_datetime is not None,
            )
            raise MyBookingReschedulePrepareError(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT)

        old_local = old_datetime.astimezone(_zoneinfo(timezone_name))
        return {
            "yclients_record_id": record_id,
            "service_ids": service_ids,
            "service_id": service_ids[0],
            "staff_id": staff_id,
            "client_data": client_data,
            "seance_length": seance_length,
            "old_date": old_local.strftime("%d.%m.%Y"),
            "old_time": old_local.strftime("%H:%M"),
            "old_datetime": old_local.isoformat(),
            "branch_timezone": timezone_name,
        }

    async def prepare_repeat_context(
        self,
        user: User | None,
        *,
        yclients_record_id: str,
        platform_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Load selected YClients record and return service/master for repeat booking."""

        context = await self.prepare_reschedule_context(
            user,
            yclients_record_id=yclients_record_id,
            platform_user_id=platform_user_id,
        )
        return {
            "yclients_record_id": context.get("yclients_record_id"),
            "service_id": context.get("service_id"),
            "service_ids": context.get("service_ids"),
            "staff_id": context.get("staff_id"),
            "service_name": None,
            "staff_name": None,
            "branch_timezone": context.get("branch_timezone"),
        }

    async def reschedule_booking_for_user(
        self,
        user: User | None,
        *,
        reschedule_context: dict[str, Any],
        new_datetime_iso: str,
        platform_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Reschedule via Telegram-compatible safe rebooking: create new, then cancel old."""

        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        if not yclients_client_id and not phone:
            raise MyBookingsProfileMissingError(MY_BOOKINGS_NO_PROFILE_TEXT)

        record_id = _clean_text(reschedule_context.get("yclients_record_id"))
        staff_id = _clean_text(reschedule_context.get("staff_id"))
        services = [sid for sid in reschedule_context.get("service_ids", []) if _clean_text(sid)] if isinstance(reschedule_context.get("service_ids"), list) else []
        client_data = reschedule_context.get("client_data") if isinstance(reschedule_context.get("client_data"), dict) else {}
        client_phone = _clean_text(client_data.get("phone") or (user.phone if user else None))
        client_name = _clean_text(client_data.get("name") or (user.display_name if user else None) or (user.first_name if user else None)) or "Гость"
        seance_length = _to_int(reschedule_context.get("seance_length"))
        datetime_iso = _clean_text(new_datetime_iso)
        if not record_id or not staff_id or not services or not client_data or not client_phone or not seance_length or not datetime_iso:
            raise MyBookingReschedulePrepareError(MY_BOOKING_RESCHEDULE_PREPARE_ERROR_TEXT)

        settings = self._active_settings_for_reschedule(platform_user_id=platform_user_id, record_id=record_id)
        created_record_id = ""
        cancel_success = False
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                created = await yclients.create_booking(
                    company_id=settings.company_id,
                    service_id=services[0],
                    datetime_iso=datetime_iso,
                    phone=client_phone,
                    fullname=client_name,
                    staff_id=staff_id,
                    marker=RESCHEDULE_CREATE_MARKER,
                )
                created_record_id = _clean_text(getattr(created, "record_id", None))
                cancel_marker = _build_reschedule_cancel_marker(datetime_iso, _timezone_name(settings.branch_timezone))
                await yclients.cancel_booking(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                    cancellation_marker=cancel_marker,
                )
                cancel_success = True
        except (YClientsValidationError, YClientsNotFoundError) as exc:
            self._log_reschedule_diagnostic(platform_user_id, record_id, created_record_id, datetime_iso, cancel_success, exc)
            if created_record_id and not cancel_success:
                raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT) from exc
            raise MyBookingRescheduleNotAllowedError(MY_BOOKING_RESCHEDULE_NOT_ALLOWED_TEXT) from exc
        except (YClientsAuthError, YClientsRateLimitError, YClientsServerError, YClientsTransportError, YClientsError) as exc:
            self._log_reschedule_diagnostic(platform_user_id, record_id, created_record_id, datetime_iso, cancel_success, exc)
            if created_record_id and not cancel_success:
                raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT) from exc
            raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_ERROR_TEXT) from exc

        self._log_reschedule_diagnostic(platform_user_id, record_id, created_record_id, datetime_iso, cancel_success, None)
        return {"old_record_id": record_id, "new_record_id": created_record_id, "new_datetime": datetime_iso}

    def _log_reschedule_diagnostic(
        self,
        platform_user_id: str | None,
        record_id: str,
        created_record_id: str,
        datetime_iso: str,
        cancel_success: bool,
        exc: Exception | None,
    ) -> None:
        log = logger.warning if exc else logger.info
        log(
            "MAX booking reschedule diagnostic: platform_user_id_present=%s old_record_id_present=%s "
            "new_record_id_present=%s native_reschedule_supported=%s fallback_cancel_create_used=%s "
            "selected_date_present=%s selected_time_present=%s old_cancel_started=%s old_cancel_success=%s "
            "new_create_started=%s new_create_success=%s error_class=%s http_status=%s",
            bool(platform_user_id),
            bool(record_id),
            bool(created_record_id),
            True,
            True,
            bool(datetime_iso[:10]),
            bool(datetime_iso[11:16]),
            bool(created_record_id),
            cancel_success,
            True,
            bool(created_record_id),
            type(exc).__name__ if exc else "none",
            getattr(exc, "status_code", None) if exc else None,
        )

    def _active_settings_for_reschedule(self, *, platform_user_id: str | None, record_id: str):
        try:
            settings = load_active_yclients_settings(self._settings_repository, operation="get_my_bookings")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Booking reschedule settings lookup failed: operation=reschedule_booking platform_user_id=%s "
                "yclients_record_id=%s error_class=%s",
                platform_user_id,
                record_id,
                type(exc).__name__,
            )
            raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_ERROR_TEXT) from exc
        if not has_required_yclients_credentials(settings):
            logger.info(
                "Booking reschedule unavailable: operation=reschedule_booking platform_user_id=%s yclients_record_id=%s "
                "settings_present=%s company_id_present=%s partner_token_present=%s user_token_present=%s",
                platform_user_id,
                record_id,
                settings is not None,
                bool(settings and settings.company_id),
                bool(settings and settings.partner_token),
                bool(settings and settings.user_token),
            )
            raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_ERROR_TEXT)
        return settings


def _build_reschedule_cancel_marker(datetime_iso: str, timezone_name: str) -> str:
    value = _clean_text(datetime_iso).replace("T", " ")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_zoneinfo(timezone_name))
        local = parsed.astimezone(_zoneinfo(timezone_name))
        return f"{RESCHEDULE_CANCEL_MARKER_PREFIX} {local.strftime('%d.%m.%Y')} в {local.strftime('%H:%M')}"
    return RESCHEDULE_CANCEL_MARKER_PREFIX

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

    return sorted(
        items,
        key=lambda item: parse_booking_datetime(item, timezone_name=timezone_name)
        or datetime.max.replace(tzinfo=_zoneinfo(timezone_name)),
    )


def format_booking_item(item: MyBookingItem, *, index: int, timezone_name: str) -> str:
    """Format one booking card in the reference UX style."""

    booking_datetime = item.booking_datetime.astimezone(_zoneinfo(timezone_name))
    return "\n".join(
        [
            f"{index}. ✂️ Услуга: {item.service_name}",
            f"   👤 Мастер: {item.master_name or 'Любой мастер'}",
            f"   📅 Дата: {booking_datetime.strftime('%d.%m.%Y')}",
            f"   🕒 Время: {booking_datetime.strftime('%H:%M')}",
            f"   ⏳ Длительность: {str(item.duration_minutes) + ' мин' if item.duration_minutes else '—'}",
            f"   💰 Цена: {item.price or '—'}",
            f"   📍 Адрес: {item.address or '—'}",
            f"   📞 Контакты: {item.phone or '—'}",
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
            f"⏳ Длительность: {display['duration_minutes'] + ' мин' if display['duration_minutes'] else '—'}",
            f"💰 Цена: {display['price'] or '—'}",
            f"📍 Адрес: {display['address'] or '—'}",
            f"📞 Контакты: {display['phone'] or '—'}",
            f"🧾 Статус: {display['status']}",
        ]
    )


def format_cancel_confirmation_text(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format cancellation confirmation text using Telegram reference wording."""

    return "❗️Вы уверены, что хотите отменить запись?"


def format_cancel_success_text(booking: MyBookingItem | dict[str, Any], *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format successful cancellation message using Telegram reference wording."""

    return "✅ Запись отменена."


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
            "duration_minutes": str(booking.duration_minutes) if booking.duration_minutes else None,
            "price": booking.price,
            "address": booking.address,
            "phone": booking.phone,
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
        "duration_minutes": _clean_text(booking.get("duration_minutes")) or None,
        "price": _clean_text(booking.get("price")) or None,
        "address": _clean_text(booking.get("address")) or None,
        "phone": _clean_text(booking.get("phone")) or None,
    }



def format_reschedule_confirmation_text(data: dict[str, Any]) -> str:
    """Format final reschedule confirmation text."""

    return "\n".join(
        [
            "Проверьте перенос записи 🔁",
            "",
            "Было:",
            f"🗓 {_clean_text(data.get('old_date')) or '—'}",
            f"🕒 {_clean_text(data.get('old_time')) or '—'}",
            "",
            "Станет:",
            f"🗓 {_clean_text(data.get('new_date')) or '—'}",
            f"🕒 {_clean_text(data.get('new_time')) or '—'}",
        ]
    )


def format_reschedule_success_text(data: dict[str, Any]) -> str:
    """Format successful reschedule message."""

    return "\n".join(
        [
            "Запись перенесена ✅",
            "",
            f"Новая дата: {_clean_text(data.get('new_date')) or '—'}",
            f"Новое время: {_clean_text(data.get('new_time')) or '—'}",
        ]
    )


def build_new_datetime_iso(booking_date: str | date, booking_time: str, *, selected_datetime: str | None = None) -> str:
    """Build YClients datetime value from selected reschedule date and slot."""

    raw_datetime = _clean_text(selected_datetime)
    if raw_datetime and len(raw_datetime) > 5:
        return raw_datetime.replace("T", " ")
    date_value = booking_date.isoformat() if isinstance(booking_date, date) else _clean_text(booking_date)
    time_value = _clean_text(booking_time)
    if len(time_value) == 5:
        time_value = f"{time_value}:00"
    return f"{date_value} {time_value}" if date_value and time_value else ""


def format_display_date(value: str | date, *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> str:
    """Format ISO date for Russian user-facing text."""

    try:
        parsed = value if isinstance(value, date) else datetime.fromisoformat(str(value)).date()
    except ValueError:
        return _clean_text(value) or "—"
    return parsed.strftime("%d.%m.%Y")


async def _resolve_client_id_by_phone(yclients: YClientsServiceLayer, company_id: str, phone: str) -> str | None:
    """Resolve one YClients client id by normalized phone, following Telegram's safe single-match rule."""

    normalized = normalize_phone(phone)
    keys = {normalized, normalized.lstrip("+")}
    if normalized.startswith("+7") and len(normalized) == 12:
        keys.add("8" + normalized[2:])
    candidates: dict[str, Any] = {}
    for key in sorted(item for item in keys if item):
        for card in await yclients.find_client(company_id=company_id, query=key, by_phone=True, page=1, count=50):
            if card.id:
                candidates[card.id] = card
    matches = []
    expected = {normalize_phone(key).lstrip("+") for key in keys if key}
    for client_id, card in candidates.items():
        candidate_phone = normalize_phone(card.phone or "").lstrip("+")
        if candidate_phone and candidate_phone in expected:
            matches.append(client_id)
    return matches[0] if len(matches) == 1 else None


def _format_price(value: Any) -> str | None:
    raw = _clean_text(value)
    if not raw:
        return None
    cleaned = raw.replace("₽", "").replace(" ", "").replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return raw if "₽" in raw else f"{raw} ₽"
    return f"{int(number)} ₽" if number.is_integer() else f"{number:.2f} ₽".replace(".", ",")


def _extract_price(item: dict[str, Any]) -> str | None:
    for key in ("final_price", "total_price", "amount", "sum", "price", "cost", "price_min"):
        price = _format_price(item.get(key))
        if price:
            return price
    services = item.get("services")
    if isinstance(services, list):
        total = 0.0
        first_text = None
        for service in services:
            if not isinstance(service, dict):
                continue
            for key in ("discount_price", "price", "cost", "price_min"):
                text = _format_price(service.get(key))
                if text and first_text is None:
                    first_text = text
                raw = _clean_text(service.get(key)).replace(",", ".")
                try:
                    if raw:
                        total += float(raw)
                        break
                except ValueError:
                    continue
        if total:
            return _format_price(total)
        return first_text
    return None

def _booking_from_payload(
    item: dict[str, Any],
    *,
    timezone_name: str,
    address: str | None = None,
    phone: str | None = None,
) -> MyBookingItem | None:
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
        duration_minutes=_extract_seance_length(item),
        price=_extract_price(item),
        address=address,
        phone=phone,
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



def _extract_record_detail_row(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload, dict):
        return payload
    return {}


def _extract_service_ids(row: dict[str, Any]) -> list[str]:
    services = row.get("services")
    result: list[str] = []
    if isinstance(services, list):
        for item in services:
            if isinstance(item, dict):
                value = _clean_text(item.get("id") or item.get("service_id"))
            else:
                value = _clean_text(item)
            if value:
                result.append(value)
    service = row.get("service")
    single = _clean_text(row.get("service_id"))
    if not single and isinstance(service, dict):
        single = _clean_text(service.get("id") or service.get("service_id"))
    if single and single not in result:
        result.append(single)
    return result


def _extract_staff_id(row: dict[str, Any]) -> str | None:
    value = _clean_text(row.get("staff_id") or row.get("master_id") or row.get("employee_id"))
    if value:
        return value
    for key in ("staff", "master", "employee"):
        nested = row.get(key)
        if isinstance(nested, dict):
            nested_id = _clean_text(nested.get("id") or nested.get("staff_id") or nested.get("master_id"))
            if nested_id:
                return nested_id
    return None


def _extract_client_data(row: dict[str, Any]) -> dict[str, Any]:
    client = row.get("client") if isinstance(row.get("client"), dict) else {}
    client_id = _clean_text(client.get("id") or client.get("client_id") or row.get("client_id"))
    if not client_id:
        return {}
    data = {"id": client_id}
    for source_key, target_key in (("name", "name"), ("fullname", "name"), ("phone", "phone"), ("email", "email"), ("sex", "sex")):
        value = _clean_text(client.get(source_key) or row.get(source_key))
        if value and target_key not in data:
            data[target_key] = value
    return data


def _extract_seance_length(row: dict[str, Any]) -> int | None:
    value = _to_int(row.get("seance_length") or row.get("length") or row.get("duration"))
    if value:
        return value
    services = row.get("services")
    if isinstance(services, list):
        total = 0
        for item in services:
            if isinstance(item, dict):
                total += _to_int(item.get("seance_length") or item.get("duration")) or 0
        if total:
            return total
    return None


def _to_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None

def _is_safe_status(status: str) -> bool:
    if status.isdigit():
        return False
    return all(ch.isalnum() or ch in " _-А-Яа-яЁё" for ch in status) and len(status) <= 40


def _timezone_name(value: str | None) -> str:
    return normalize_branch_timezone(value, flow="my_bookings", operation="_timezone_name")


def _zoneinfo(timezone_name: str) -> ZoneInfo:
    return zoneinfo_or_default(timezone_name, flow="my_bookings", operation="_zoneinfo")


def _clean_text(value: Any) -> str:
    return safe_str(value)
