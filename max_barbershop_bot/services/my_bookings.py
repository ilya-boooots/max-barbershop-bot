"""Transport-neutral service for viewing future YClients bookings."""

from __future__ import annotations

import logging
import time
import traceback
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
from max_barbershop_bot.repositories.platform_attribution import PLATFORM_MAX, PlatformAttributionRepository
from max_barbershop_bot.repositories.users import User
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.contacts import ContactsService
from max_barbershop_bot.services.company_time import DEFAULT_BRANCH_TIMEZONE, build_yclients_action_comment, normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.yclients_context import (
    build_yclients_client_from_active_settings,
    has_required_yclients_credentials,
    load_active_yclients_settings,
)

logger = logging.getLogger(__name__)

_MY_BOOKINGS_PAGE_SIZE = 200
_MY_BOOKINGS_MAX_PAGES = 10
_RATE_LIMIT_COOLDOWNS: dict[tuple[str, str, str, str], float] = {}

MY_BOOKINGS_NO_PROFILE_TEXT = "Не получилось найти ваши данные для записей 🙏\n\nНажмите /start и пройдите регистрацию заново."
MY_BOOKINGS_LOAD_ERROR_TEXT = "Не удалось загрузить ваши записи 🙏\n\nПожалуйста, попробуйте позже."
MY_BOOKINGS_RATE_LIMIT_TEXT = "YClients временно ограничил количество запросов 🙏\n\nПопробуйте ещё раз через пару минут."
MY_BOOKINGS_EMPTY_TEXT = "📭 У вас пока нет активных записей."
MY_BOOKINGS_TITLE_TEXT = "📅 Ваши записи"
MY_BOOKING_NOT_FOUND_TEXT = "Эта запись уже неактуальна 🙏\n\nОткройте список записей заново."
MY_BOOKING_CANCEL_IN_PROGRESS_TEXT = "⏳ Уже выполняем действие, секундочку 🙂"
MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT = "Эту запись уже нельзя отменить через бота 🙏\n\nПожалуйста, напишите администратору."
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
MY_BOOKING_RESCHEDULE_IN_PROGRESS_TEXT = "⏳ Уже выполняем действие, секундочку 🙂"
MY_BOOKING_RESCHEDULE_DATES_TEXT = "🔁 Перенос записи\n\nВыберите новую дату:"
MY_BOOKING_RESCHEDULE_SLOTS_TEXT = "🔁 Перенос записи\n\nВыберите новое время:"
MY_BOOKING_RESCHEDULE_NO_SLOTS_TEXT = "На эту дату свободного времени нет 🙏\n\nВыберите другой день."
MY_BOOKING_RESCHEDULE_NO_DATES_TEXT = "Пока нет доступных дат для переноса 🙏\n\nПопробуйте позже или напишите администратору."
MY_BOOKING_RESCHEDULE_STALE_SLOT_TEXT = "Это время уже недоступно 🙏\n\nПожалуйста, выберите другое время."
MY_BOOKING_RESCHEDULE_STALE_DATE_TEXT = "На эту дату пока нет свободных окон 🙏\n\nВыберите другую дату."
MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT = "⏳ Слишком много запросов. Попробуйте через минуту 🙂"
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
_CANCELLED_STATUSES = {"cancelled", "canceled", "cancel", "deleted", "delete", "removed"}
_COMPLETED_VISIT_STATUSES = {"done", "completed", "complete", "visit", "visited", "arrived", "paid", "finished"}
_NO_SHOW_STATUSES = {"no_show", "noshow", "not_come", "did_not_come"}
_ACTIVE_BOOKING_STATUSES = {"active", "confirmed", "approve", "approved", "pending", "new", "booked", "created", "reserved"}
_TELEGRAM_RESCHEDULE_STATUSES = {"active", "confirmed", "approve", "approved", "pending", "new"}
_CANCELLED_OR_PAST_STATUSES = _CANCELLED_STATUSES | _COMPLETED_VISIT_STATUSES | _NO_SHOW_STATUSES
_ACTIVE_CANCELABLE_STATUSES = _ACTIVE_BOOKING_STATUSES
_CANCEL_CUTOFF_MINUTES = 10


class MyBookingsError(RuntimeError):
    """Clean domain error safe for the MAX flow."""

    def __init__(self, user_message: str, *, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.diagnostic = diagnostic or {}


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
    yclients_staff_id: str | None = None
    raw_status: str | None = None
    duration_minutes: int | None = None
    price: str | None = None
    address: str | None = None
    phone: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class MyBookingsSplit:
    """Bookings split by appointment datetime in branch timezone."""

    upcoming: list[MyBookingItem]
    past: list[MyBookingItem]


@dataclass(frozen=True)
class MyBookingsResult:
    """Result of loading YClients bookings for one user."""

    bookings: list[MyBookingItem]
    branch_timezone: str
    yclients_client_id: str | None
    phone_exists: bool

    @property
    def is_empty(self) -> bool:
        return not self.bookings


class MyBookingsService:
    """Load and format YClients records without bot transport dependencies."""

    def __init__(self, settings_repository: YClientsSettingsRepository) -> None:
        self._settings_repository = settings_repository

    async def get_bookings_for_user(self, user: User | None, *, platform_user_id: str | None = None) -> MyBookingsResult:
        """Return recent past and future YClients bookings for a stored MAX user profile."""

        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        attribution_rows = _platform_user_attributions(self._settings_repository.database_path, platform_user_id)
        known_phones = collect_known_user_phones(user, attribution_rows)
        attributed_record_ids = _attributed_record_ids(attribution_rows)
        if not yclients_client_id and not known_phones and not attributed_record_ids:
            logger.info(
                "My bookings profile unresolved: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
            )
            return MyBookingsResult(bookings=[], branch_timezone=DEFAULT_BRANCH_TIMEZONE, yclients_client_id=None, phone_exists=False)

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
        started_at = time.monotonic()
        endpoint_name = "list_user_bookings"
        request_mode = _request_mode(yclients_client_id, known_phones)
        cooldown_key = (endpoint_name, str(settings.company_id), str(platform_user_id or "n/a"), request_mode)
        if _rate_limit_cooldown_active(cooldown_key):
            diagnostic = _rate_limit_diagnostic(
                function="MyBookingsService.get_bookings_for_user",
                max_user_id=platform_user_id,
                user=user,
                endpoint_name=endpoint_name,
                request_mode=request_mode,
                retry_after_seconds=max(1, int(_RATE_LIMIT_COOLDOWNS[cooldown_key] - time.monotonic())),
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            raise MyBookingsLoadError(MY_BOOKINGS_RATE_LIMIT_TEXT, diagnostic=diagnostic)
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                if not yclients_client_id and phone:
                    yclients_client_id = await _resolve_client_id_by_phone(yclients, settings.company_id, phone)
                payload = await _fetch_all_relevant_records(
                    yclients,
                    company_id=settings.company_id,
                    yclients_client_id=yclients_client_id,
                    phones=known_phones,
                    attributed_record_ids=attributed_record_ids,
                    start_date=now.date().isoformat(),
                    end_date=(now.date() + timedelta(days=365)).isoformat(),
                )
        except YClientsError as exc:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            if isinstance(exc, YClientsRateLimitError):
                _set_rate_limit_cooldown(cooldown_key, exc.retry_after_seconds)
                diagnostic = _rate_limit_diagnostic(
                    function="MyBookingsService.get_bookings_for_user",
                    max_user_id=platform_user_id,
                    user=user,
                    endpoint_name=endpoint_name,
                    request_mode=request_mode,
                    retry_after_seconds=exc.retry_after_seconds,
                    duration_ms=duration_ms,
                )
                logger.warning("MAX YClients rate limit diagnostic: %s", diagnostic)
                raise MyBookingsLoadError(MY_BOOKINGS_RATE_LIMIT_TEXT, diagnostic=diagnostic) from exc
            diagnostic = _runtime_error_diagnostic(
                function="MyBookingsService.get_bookings_for_user",
                max_user_id=platform_user_id,
                platform_user_id=platform_user_id,
                user=user,
                company_id_present=bool(settings and settings.company_id),
                endpoint_name=endpoint_name,
                request_mode=request_mode,
                records_count=0,
                parsed_records_count=0,
                active_records_count=0,
                skipped_malformed_count=0,
                exc=exc,
                duration_ms=duration_ms,
            )
            logger.warning("MAX my bookings runtime error: %s", diagnostic)
            logger.warning(
                "My bookings YClients error: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s error_class=%s status_code=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
                type(exc).__name__,
                exc.status_code,
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT, diagnostic=diagnostic) from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected integration errors to domain errors.
            duration_ms = int((time.monotonic() - started_at) * 1000)
            diagnostic = _runtime_error_diagnostic(
                function="MyBookingsService.get_bookings_for_user",
                max_user_id=platform_user_id,
                platform_user_id=platform_user_id,
                user=user,
                company_id_present=bool(settings and settings.company_id),
                endpoint_name=endpoint_name,
                request_mode=_request_mode(yclients_client_id, known_phones),
                records_count=0,
                parsed_records_count=0,
                active_records_count=0,
                skipped_malformed_count=0,
                exc=exc,
                duration_ms=duration_ms,
            )
            logger.warning("MAX my bookings runtime error: %s", diagnostic)
            logger.warning(
                "My bookings unexpected error: operation=get_my_bookings platform_user_id=%s "
                "yclients_client_id_present=%s phone_present=%s error_class=%s",
                platform_user_id,
                bool(yclients_client_id),
                bool(phone),
                type(exc).__name__,
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT, diagnostic=diagnostic) from exc

        try:
            contacts = await ContactsService(self._settings_repository).get_contacts()
            raw_rows = _deduplicate_record_rows(_extract_record_rows(payload))
            filtered_rows, filter_counts = _filter_owned_active_rows(
                raw_rows,
                yclients_client_id=yclients_client_id,
                attributed_record_ids=attributed_record_ids,
                known_phones=known_phones,
                timezone_name=timezone_name,
                now=now,
            )
            status_diagnostics = _status_diagnostics(raw_rows)
            bookings = [
                _booking_from_payload(item, timezone_name=timezone_name, address=contacts.address, phone=contacts.phone)
                for item in filtered_rows
            ]
            normalized_bookings = [item for item in bookings if item is not None]
            all_bookings = sort_bookings_by_datetime(normalized_bookings, timezone_name=timezone_name)
            hidden_cancelled_count = filter_counts["hidden_cancelled_count"]
            hidden_parse_error_count = filter_counts["hidden_parse_error_count"]
        except Exception as exc:  # noqa: BLE001 - bad contact/settings/payload shape must not make the callback silent.
            duration_ms = int((time.monotonic() - started_at) * 1000)
            diagnostic = _runtime_error_diagnostic(
                function="MyBookingsService.get_bookings_for_user",
                max_user_id=platform_user_id,
                platform_user_id=platform_user_id,
                user=user,
                company_id_present=bool(settings and settings.company_id),
                endpoint_name=endpoint_name,
                request_mode=_request_mode(yclients_client_id, known_phones),
                records_count=len(_extract_record_rows(payload)) if "payload" in locals() else 0,
                parsed_records_count=0,
                active_records_count=0,
                skipped_malformed_count=0,
                exc=exc,
                duration_ms=duration_ms,
            )
            logger.warning("MAX my bookings runtime error: %s", diagnostic)
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT, diagnostic=diagnostic) from exc
        logger.info(
            "MAX my bookings ownership diagnostic: platform_user_id_present=%s yclients_client_id_present=%s "
            "registered_phone_present_masked=%s known_phones_count=%s attributed_record_ids_count=%s yclients_candidates_count=%s fresh_records_count=%s owned_records_count=%s future_count=%s visible_active_count=%s "
            "hidden_not_owned_count=%s hidden_cancelled_count=%s hidden_deleted_count=%s hidden_past_count=%s "
            "hidden_parse_error_count=%s status_raw=%s status_mapped=%s branch_timezone=%s telegram_reference_rule_used=%s",
            bool(platform_user_id),
            bool(yclients_client_id),
            _mask_phone(phone),
            len(known_phones),
            len(attributed_record_ids),
            len(raw_rows),
            len(raw_rows),
            filter_counts["owned_records_count"],
            len(all_bookings),
            len(all_bookings),
            filter_counts["hidden_not_owned_count"],
            hidden_cancelled_count,
            filter_counts["hidden_deleted_count"],
            filter_counts["hidden_past_count"],
            hidden_parse_error_count,
            status_diagnostics["raw"],
            status_diagnostics["mapped"],
            timezone_name,
            "future_and_not_explicitly_cancelled_or_completed",
        )
        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "MAX my bookings diagnostic: max_user_id=%s has_yclients_client_id=%s has_phone=%s "
            "yclients_records_count=%s active_records_count=%s endpoint_name=%s error_type=%s duration_ms=%s",
            platform_user_id or "n/a",
            bool(yclients_client_id),
            bool(phone),
            len(raw_rows),
            len(all_bookings),
            endpoint_name,
            "none",
            duration_ms,
        )
        logger.info(
            "MAX my bookings list diagnostic: platform_user_id_present=%s phone_present_masked=%s "
            "yclients_client_id_present=%s raw_records_count=%s after_status_filter_count=%s "
            "upcoming_count=%s past_count=%s rendered_buttons_count=%s state_map_size=%s page=%s page_size=%s "
            "branch_timezone=%s",
            bool(platform_user_id),
            _mask_phone(phone),
            bool(yclients_client_id),
            len(raw_rows),
            len(normalized_bookings),
            len(all_bookings),
            0,
            len(all_bookings),
            len(all_bookings),
            "all",
            _MY_BOOKINGS_PAGE_SIZE,
            timezone_name,
        )
        return MyBookingsResult(
            bookings=all_bookings,
            branch_timezone=timezone_name,
            yclients_client_id=yclients_client_id,
            phone_exists=bool(phone),
        )

    async def get_future_bookings_for_user(self, user: User | None, *, platform_user_id: str | None = None) -> MyBookingsResult:
        """Backward-compatible alias for loading the My bookings list."""

        return await self.get_bookings_for_user(user, platform_user_id=platform_user_id)

    async def get_booking_for_user(
        self,
        user: User | None,
        *,
        yclients_record_id: str,
        platform_user_id: str | None = None,
    ) -> MyBookingItem:
        """Fetch one fresh YClients record and normalize it for details/cancelability decisions."""

        record_id = _clean_text(yclients_record_id)
        if not record_id:
            raise MyBookingsLoadError(MY_BOOKING_NOT_FOUND_TEXT)
        yclients_client_id = _clean_text(user.yclients_client_id if user else None)
        phone = _clean_text(user.phone if user else None)
        attribution_rows = _platform_user_attributions(self._settings_repository.database_path, platform_user_id)
        known_phones = collect_known_user_phones(user, attribution_rows)
        attributed_record_ids = set(_attributed_record_ids(attribution_rows))
        if not yclients_client_id and not known_phones and not attributed_record_ids:
            raise MyBookingsProfileMissingError(MY_BOOKINGS_NO_PROFILE_TEXT)
        try:
            settings = load_active_yclients_settings(self._settings_repository, operation="get_my_bookings")
        except Exception as exc:  # noqa: BLE001 - keep details away from users.
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT) from exc
        timezone_name = _timezone_name(settings.branch_timezone if settings else None)
        if not has_required_yclients_credentials(settings):
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT)
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                payload = await yclients.get_booking_details(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                )
        except YClientsNotFoundError as exc:
            raise MyBookingsLoadError(MY_BOOKING_NOT_FOUND_TEXT) from exc
        except YClientsError as exc:
            logger.warning(
                "MAX my bookings cancellation diagnostic: platform_user_id_present=%s yclients_record_id_present=%s yclients_error_category=%s http_status=%s trace_id=%s",
                bool(platform_user_id),
                bool(record_id),
                getattr(exc, "error_category", type(exc).__name__),
                exc.status_code,
                getattr(exc, "trace_id", None),
            )
            raise MyBookingsLoadError(MY_BOOKINGS_LOAD_ERROR_TEXT) from exc
        row = _extract_record_detail_row(payload)
        contacts = await ContactsService(self._settings_repository).get_contacts()
        booking = _booking_from_payload(row, timezone_name=timezone_name, address=contacts.address, phone=contacts.phone) if row else None
        if booking is None:
            raise MyBookingsLoadError(MY_BOOKING_NOT_FOUND_TEXT)
        if ownership_source_for_record(row, yclients_client_id=yclients_client_id, attributed_record_ids=attributed_record_ids, known_phones=known_phones) == "none":
            raise MyBookingsLoadError(MY_BOOKING_NOT_FOUND_TEXT)
        if not is_future_booking(booking, timezone_name=timezone_name):
            raise MyBookingsLoadError(MY_BOOKING_NOT_FOUND_TEXT)
        return booking

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
        attribution_rows = _platform_user_attributions(self._settings_repository.database_path, platform_user_id)
        known_phones = collect_known_user_phones(user, attribution_rows)
        attributed_record_ids = set(_attributed_record_ids(attribution_rows))
        if not yclients_client_id and not known_phones and not attributed_record_ids:
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

        timezone_name = _timezone_name(settings.branch_timezone if settings else None)
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                details = await yclients.get_booking_details(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                )
                row = _extract_record_detail_row(details)
                current = _booking_from_payload(row, timezone_name=timezone_name) if row else None
                current_status_raw = _clean_text(row.get("status") or row.get("record_status") or row.get("state")) if row else ""
                current_status_mapped = format_booking_status(current_status_raw)
                appointment_datetime = parse_booking_datetime(current, timezone_name=timezone_name) if current else None
                is_owned = bool(row and ownership_source_for_record(row, yclients_client_id=yclients_client_id, attributed_record_ids=attributed_record_ids, known_phones=known_phones) != "none")
                is_future = bool(current and is_owned and is_future_booking(current, timezone_name=timezone_name))
                is_past = bool(appointment_datetime and not is_future)
                is_cancelable = bool(current and is_owned and is_booking_cancelable(current, timezone_name=timezone_name))
                logger.info(
                    "MAX my bookings cancelability diagnostic: yclients_record_id_present=%s raw_status=%s "
                    "mapped_status=%s appointment_datetime_present=%s branch_timezone=%s is_past=%s "
                    "is_future=%s is_cancelable=%s cancel_button_visible=%s",
                    bool(record_id),
                    current_status_raw or None,
                    current_status_mapped,
                    appointment_datetime is not None,
                    timezone_name,
                    is_past,
                    is_future,
                    is_cancelable,
                    is_cancelable,
                )
                if not is_future or not is_cancelable:
                    raise MyBookingCancellationNotAllowedError(MY_BOOKING_CANCEL_NOT_ALLOWED_TEXT)
                result = await yclients.cancel_booking(
                    company_id=settings.company_id,
                    yclients_record_id=record_id,
                    cancellation_marker=cancellation_marker,
                )
                logger.info(
                    "MAX my bookings cancelability diagnostic: yclients_record_id_present=%s raw_status=%s "
                    "mapped_status=%s appointment_datetime_present=%s branch_timezone=%s is_past=%s "
                    "is_future=%s is_cancelable=%s cancel_button_visible=%s",
                    bool(record_id),
                    current_status_raw or None,
                    current_status_mapped,
                    appointment_datetime is not None,
                    timezone_name,
                    is_past,
                    is_future,
                    is_cancelable,
                    is_cancelable,
                )
        except MyBookingCancellationNotAllowedError:
            raise
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

        result_status = getattr(result, "status", None)
        logger.info(
            "Booking cancelled in YClients: operation=cancel_booking platform_user_id=%s "
            "yclients_record_id=%s result_status=%s",
            platform_user_id,
            record_id,
            result_status,
        )
        return result_status


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
        attribution_rows = _platform_user_attributions(self._settings_repository.database_path, platform_user_id)
        known_phones = collect_known_user_phones(user, attribution_rows)
        attributed_record_ids = set(_attributed_record_ids(attribution_rows))
        if not yclients_client_id and not known_phones and not attributed_record_ids:
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
        if ownership_source_for_record(row, yclients_client_id=yclients_client_id, attributed_record_ids=attributed_record_ids, known_phones=known_phones) == "none":
            raise MyBookingReschedulePrepareError(MY_BOOKING_NOT_FOUND_TEXT)
        if not is_record_visible_active(row, datetime.now(_zoneinfo(timezone_name)), timezone_name=timezone_name):
            raise MyBookingReschedulePrepareError(MY_BOOKING_NOT_FOUND_TEXT)

        selected_booking = _booking_from_payload(row, timezone_name=timezone_name)
        service_ids = _extract_service_ids(row)
        staff_id = _extract_staff_id(row)
        client_data = _extract_client_data(row)
        seance_length = _extract_seance_length(row)
        old_datetime = parse_booking_datetime(row, timezone_name=timezone_name)
        service_name = _extract_service_name(row)
        staff_name = _extract_master_name(row)
        if selected_booking is not None:
            service_name = selected_booking.service_name or service_name
            staff_name = selected_booking.master_name or staff_name
            staff_id = staff_id or selected_booking.yclients_staff_id
            seance_length = seance_length or selected_booking.duration_minutes
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
            "service_name": service_name,
            "staff_id": staff_id,
            "staff_name": staff_name,
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
                    marker=build_yclients_action_comment(
                        RESCHEDULE_CREATE_MARKER,
                        timezone_name=settings.branch_timezone,
                        action_type="booking_reschedule",
                    ),
                )
                created_record_id = _clean_text(getattr(created, "record_id", None))
                cancel_marker = _build_reschedule_cancel_marker(_timezone_name(settings.branch_timezone))
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
        except YClientsRateLimitError as exc:
            self._log_reschedule_diagnostic(platform_user_id, record_id, created_record_id, datetime_iso, cancel_success, exc)
            if created_record_id and not cancel_success:
                raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_CANCEL_OLD_FAILED_TEXT) from exc
            raise MyBookingRescheduleError(MY_BOOKING_RESCHEDULE_RATE_LIMIT_TEXT) from exc
        except (YClientsAuthError, YClientsServerError, YClientsTransportError, YClientsError) as exc:
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


def _build_reschedule_cancel_marker(timezone_name: str) -> str:
    return build_yclients_action_comment(
        RESCHEDULE_CANCEL_MARKER_PREFIX,
        timezone_name=timezone_name,
        action_type="booking_reschedule_cancel_old",
    )


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
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y")
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
    """Return whether a record is a fresh future active YClients booking."""

    if not _is_yclients_record_active(item):
        return False
    parsed = parse_booking_datetime(item, timezone_name=timezone_name)
    if parsed is None:
        return False
    current = now or datetime.now(_zoneinfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=_zoneinfo(timezone_name))
    return parsed >= current.astimezone(_zoneinfo(timezone_name)) - timedelta(minutes=5)


def is_completed_visit(
    item: dict[str, Any] | MyBookingItem,
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """Return whether a record is a real completed past visit."""

    raw_status = item.raw_status if isinstance(item, MyBookingItem) else _clean_text(item.get("raw_status") or item.get("status") or item.get("record_status") or item.get("state"))
    if _normalize_status(raw_status) not in _COMPLETED_VISIT_STATUSES:
        return False
    parsed = parse_booking_datetime(item, timezone_name=timezone_name)
    if parsed is None:
        return False
    current = now or datetime.now(_zoneinfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=_zoneinfo(timezone_name))
    return parsed < current.astimezone(_zoneinfo(timezone_name))


def is_visible_my_booking(
    item: dict[str, Any] | MyBookingItem,
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """Return whether a record belongs to active bookings or real visit history."""

    return is_future_booking(item, timezone_name=timezone_name, now=now) or is_completed_visit(item, timezone_name=timezone_name, now=now)


def is_booking_cancelable(
    item: dict[str, Any] | MyBookingItem,
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """Return whether the bot should show/call YClients cancellation for this record.

    Telegram reference shows cancellation for upcoming active cards without
    hiding the action only because a status is unknown/unmapped.
    """

    if not is_future_booking(item, timezone_name=timezone_name, now=now):
        return False
    parsed = parse_booking_datetime(item, timezone_name=timezone_name)
    if parsed is None:
        return False
    current = now or datetime.now(_zoneinfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=_zoneinfo(timezone_name))
    current = current.astimezone(_zoneinfo(timezone_name))
    if parsed - current <= timedelta(minutes=_CANCEL_CUTOFF_MINUTES):
        return False
    return _is_yclients_record_active(item)


def is_booking_reschedulable(
    item: dict[str, Any] | MyBookingItem,
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> bool:
    """Return whether Telegram would show the My Bookings reschedule action."""

    if not is_future_booking(item, timezone_name=timezone_name, now=now):
        return False
    raw_status = item.raw_status if isinstance(item, MyBookingItem) else _clean_text(item.get("raw_status") or item.get("status") or item.get("record_status") or item.get("state"))
    normalized = _normalize_status(raw_status)
    if not normalized:
        return True
    return normalized in _TELEGRAM_RESCHEDULE_STATUSES


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



def split_bookings_by_period(
    items: list[MyBookingItem],
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    now: datetime | None = None,
) -> MyBookingsSplit:
    """Split bookings by appointment datetime using branch timezone only."""

    zone = _zoneinfo(timezone_name)
    current = now or datetime.now(zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    current = current.astimezone(zone)

    upcoming: list[MyBookingItem] = []
    past: list[MyBookingItem] = []
    for item in items:
        booking_datetime = parse_booking_datetime(item, timezone_name=timezone_name)
        if booking_datetime is None:
            continue
        if is_future_booking(item, timezone_name=timezone_name, now=current):
            upcoming.append(item)
        elif is_completed_visit(item, timezone_name=timezone_name, now=current):
            past.append(item)

    upcoming = sort_bookings_by_datetime(upcoming, timezone_name=timezone_name)
    past = list(reversed(sort_bookings_by_datetime(past, timezone_name=timezone_name)))
    return MyBookingsSplit(upcoming=upcoming, past=past)

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


def format_booking_list_item(item: MyBookingItem, *, index: int, timezone_name: str) -> str:
    """Format one compact booking row for a fast My bookings list."""

    booking_datetime = item.booking_datetime.astimezone(_zoneinfo(timezone_name))
    master = item.master_name or "Любой мастер"
    return f"{index}. 📅 {booking_datetime.strftime('%d.%m.%Y')} в {booking_datetime.strftime('%H:%M')} — {item.service_name}, {master}"


def format_bookings_list_screen(bookings: list[MyBookingItem], *, timezone_name: str) -> str:
    """Format a compact bookings list split into upcoming and past sections."""

    if not bookings:
        return f"{MY_BOOKINGS_TITLE_TEXT}\n\nПока у вас нет активных записей 🙏"

    split = split_bookings_by_period(bookings, timezone_name=timezone_name)
    parts = [MY_BOOKINGS_TITLE_TEXT, "Выберите запись, чтобы открыть детали 👇"]
    index = 1
    if split.upcoming:
        parts.extend(["", "🔜 Предстоящие записи"])
        for item in split.upcoming:
            parts.append(format_booking_list_item(item, index=index, timezone_name=timezone_name))
            index += 1
    if split.past:
        parts.extend(["", "🕓 История визитов"])
        for item in split.past:
            parts.append(format_booking_list_item(item, index=index, timezone_name=timezone_name))
            index += 1
    return "\n".join(parts)


def format_visit_history_screen(bookings: list[MyBookingItem | dict[str, Any]], *, timezone_name: str, page: int = 0, page_size: int = 5) -> str:
    """Format past bookings like the Telegram visit history screen."""

    if not bookings:
        return "🕘 История визитов пока пуста."

    start = max(page, 0) * max(page_size, 1)
    shown = bookings[start : start + max(page_size, 1)]
    lines = ["🕘 История визитов", ""]
    for idx, booking in enumerate(shown, start=start + 1):
        display = booking_display_data(booking, timezone_name=timezone_name)
        lines.append(f"{idx}. ✂️ {display['service_name']}")
        lines.append(f"   👤 {display['master_name'] or 'Любой мастер'}")
        lines.append(f"   📅 {display['date']} {display['time']}")
        lines.append(f"   💰 {display['price'] or '—'}")
    return "\n".join(lines)


def format_bookings_screen(bookings: list[MyBookingItem], *, timezone_name: str) -> str:
    """Format the full future bookings screen."""

    if not bookings:
        return MY_BOOKINGS_EMPTY_TEXT
    cards = [format_booking_item(item, index=index, timezone_name=timezone_name) for index, item in enumerate(bookings, start=1)]
    return f"{MY_BOOKINGS_TITLE_TEXT}\n\n" + "\n\n".join(cards)


def format_booking_details_text(
    booking: MyBookingItem | dict[str, Any],
    *,
    timezone_name: str = DEFAULT_BRANCH_TIMEZONE,
    title: str = "📋 Активная запись",
    include_status: bool = True,
) -> str:
    """Format selected booking details in the Telegram reference style."""

    display = booking_display_data(booking, timezone_name=timezone_name)
    lines = [
        title,
        "",
        f"✂️ Услуга: {display['service_name']}",
        f"👤 Мастер: {display['master_name'] or 'Любой мастер'}",
        f"📅 Дата: {display['date']}",
        f"🕒 Время: {display['time']}",
        f"⏳ Длительность: {display['duration_minutes'] + ' мин' if display['duration_minutes'] else '—'}",
        f"💰 Цена: {display['price'] or '—'}",
        f"📍 Адрес: {display['address'] or '—'}",
        f"📞 Контакты: {display['phone'] or '—'}",
    ]
    if include_status and display.get("status"):
        lines.append(f"🧾 Статус: {display['status']}")
    return "\n".join(lines)


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
        status = format_booking_status(booking.raw_status) if booking.raw_status else None
        if status is None and booking.status and booking.status != "Неизвестен":
            status = booking.status
        return {
            "yclients_record_id": booking.yclients_record_id,
            "service_name": booking.service_name,
            "master_name": booking.master_name,
            "yclients_staff_id": booking.yclients_staff_id,
            "datetime": booking_datetime.isoformat(),
            "date": booking_datetime.strftime("%d.%m.%Y"),
            "time": booking_datetime.strftime("%H:%M"),
            "raw_status": booking.raw_status,
            "status": status,
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
        "yclients_staff_id": _clean_text(booking.get("yclients_staff_id") or booking.get("staff_id") or booking.get("master_id")) or _extract_staff_id(booking),
        "datetime": _clean_text(booking.get("datetime") or booking.get("booking_datetime")) or None,
        "date": booking_date or "—",
        "time": booking_time or "—",
        "raw_status": _clean_text(booking.get("raw_status")) or None,
        "status": format_booking_status(booking.get("raw_status") or booking.get("status")) if _clean_text(booking.get("raw_status") or booking.get("status")) else None,
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
            f"✂️ Услуга: {_clean_text(data.get('service_name')) or 'Услуга'}",
            f"👤 Мастер: {_clean_text(data.get('staff_name')) or 'Любой мастер'}",
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
        yclients_staff_id=_extract_staff_id(item),
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



async def _fetch_all_relevant_records(
    yclients: YClientsServiceLayer,
    *,
    company_id: str | int,
    yclients_client_id: str | None,
    phones: set[str],
    attributed_record_ids: list[str],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Fetch fresh YClients records by scoped ownership candidates only."""

    rows: list[dict[str, Any]] = []
    attributed_ids = {_clean_text(record_id) for record_id in attributed_record_ids if _clean_text(record_id)}
    if yclients_client_id:
        rows.extend(
            _mark_rows_request_context(
                await _fetch_all_client_records_page_set(
                    yclients,
                    company_id=company_id,
                    yclients_client_id=yclients_client_id,
                    phone=None,
                    start_date=start_date,
                    end_date=end_date,
                ),
                request_client_id=yclients_client_id,
                request_phone=None,
            )
        )
    # Prefer the scoped client_id lookup. Phone fallback is used only when it is needed,
    # so a user with both identifiers does not cause two immediate equivalent /records calls.
    phones_to_try = sorted(phones) if (not yclients_client_id or not rows) else []
    for known_phone in phones_to_try:
        rows.extend(
            _mark_rows_request_context(
                await _fetch_all_client_records_page_set(
                    yclients,
                    company_id=company_id,
                    yclients_client_id=None,
                    phone=known_phone,
                    start_date=start_date,
                    end_date=end_date,
                ),
                request_client_id=None,
                request_phone=known_phone,
            )
        )

    fetched_ids = {
        _clean_text(row.get("record_id") or row.get("id") or row.get("booking_id") or row.get("visit_id"))
        for row in rows
    }
    for record_id in sorted(attributed_ids - fetched_ids):
        try:
            payload = await yclients.get_booking_details(company_id=company_id, yclients_record_id=record_id)
        except YClientsNotFoundError:
            continue
        fresh_row = _extract_record_detail_row(payload)
        if fresh_row:
            rows.append(fresh_row)
    return _deduplicate_record_rows(rows)


async def _fetch_all_client_records_page_set(
    yclients: YClientsServiceLayer,
    *,
    company_id: str | int,
    yclients_client_id: str | None,
    phone: str | None,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Fetch all pages for one YClients records filter set."""

    rows: list[dict[str, Any]] = []
    for page in range(1, _MY_BOOKINGS_MAX_PAGES + 1):
        payload = await yclients.get_client_records(
            company_id=company_id,
            yclients_client_id=yclients_client_id,
            phone=phone,
            start_date=start_date,
            end_date=end_date,
            page=page,
            count=_MY_BOOKINGS_PAGE_SIZE,
        )
        page_rows = _extract_record_rows(payload)
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < _MY_BOOKINGS_PAGE_SIZE:
            break
    return rows


def _platform_user_attributions(database_path: str, platform_user_id: str | None):
    """Return locally attributed rows for this MAX user only."""

    if not platform_user_id:
        return []
    try:
        return PlatformAttributionRepository(database_path).list_by_platform_user_id(platform_user_id, platform=PLATFORM_MAX)
    except Exception as exc:  # noqa: BLE001 - attribution is an optional lookup source.
        logger.info(
            "MAX my bookings ownership diagnostic: platform_user_id_present=%s attributed_record_ids_count=%s hidden_missing_fresh_count=%s branch_timezone=%s",
            True,
            0,
            0,
            type(exc).__name__,
        )
        return []


def _attributed_record_ids(attribution_rows: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for record in attribution_rows:
        record_id = _clean_text(getattr(record, "yclients_record_id", None))
        if record_id and record_id not in seen:
            seen.add(record_id)
            result.append(record_id)
    return result


def collect_known_user_phones(user: User | None, attribution_rows: list[Any]) -> set[str]:
    """Collect verified/locally attributed phones normalized for ownership checks."""

    phones: set[str] = set()
    registered = _normalize_phone_digits(user.phone if user else None)
    if registered:
        phones.add(registered)
    for record in attribution_rows:
        booking_phone = _normalize_phone_digits(getattr(record, "booking_phone", None))
        if booking_phone:
            phones.add(booking_phone)
    return phones


def _deduplicate_record_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep all unique records without collapsing different visits for one client."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        record_id = _clean_text(row.get("id") or row.get("record_id") or row.get("booking_id") or row.get("visit_id"))
        key = record_id or f"row:{index}"
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _mark_rows_request_context(
    rows: list[dict[str, Any]],
    *,
    request_client_id: str | None,
    request_phone: str | None,
) -> list[dict[str, Any]]:
    """Attach safe internal ownership context for scoped YClients list responses.

    Some real /records responses omit nested ``client`` or phone fields even when
    the request itself was scoped by client_id/phone.  Telegram treats those rows
    as user rows because they came from a scoped request; MAX previously rejected
    them as "not owned".
    """

    marked: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        if request_client_id:
            copy["_max_request_client_id"] = _clean_text(request_client_id)
        if request_phone:
            copy["_max_request_phone"] = _normalize_phone_digits(request_phone)
        marked.append(copy)
    return marked


def _mask_phone(phone: str | None) -> str:
    normalized = normalize_phone(phone or "") if phone else ""
    if not normalized:
        return "False"
    digits = "".join(ch for ch in normalized if ch.isdigit())
    suffix = digits[-2:] if len(digits) >= 2 else "**"
    return f"***{suffix}"


def _status_diagnostics(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Return compact non-sensitive status samples for parity diagnostics."""

    raw_values: list[str] = []
    mapped_values: list[str] = []
    for row in rows[:10]:
        raw = _clean_text(row.get("status") or row.get("record_status") or row.get("state")) or "empty"
        mapped = format_booking_status(raw)
        if raw not in raw_values:
            raw_values.append(raw)
        if mapped not in mapped_values:
            mapped_values.append(mapped)
    return {
        "raw": ",".join(raw_values)[:120] or "none",
        "mapped": ",".join(mapped_values)[:120] or "none",
    }


def _extract_service_name(item: dict[str, Any]) -> str:
    services = item.get("services")
    if isinstance(services, list) and services:
        for service in services:
            if isinstance(service, dict):
                name = _clean_text(service.get("title") or service.get("name"))
                if name:
                    return name
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


def _normalize_duration_minutes(value: int | None) -> int | None:
    if not value:
        return None
    if value >= 300 and value % 60 == 0:
        return value // 60
    return value


def _extract_seance_length(row: dict[str, Any]) -> int | None:
    value = _normalize_duration_minutes(_to_int(row.get("seance_length") or row.get("length") or row.get("duration")))
    if value:
        return value
    services = row.get("services")
    if isinstance(services, list):
        total = 0
        for item in services:
            if isinstance(item, dict):
                total += _normalize_duration_minutes(_to_int(item.get("seance_length") or item.get("duration"))) or 0
        if total:
            return total
    return None



def _filter_owned_active_rows(
    rows: list[dict[str, Any]],
    *,
    yclients_client_id: str | None,
    attributed_record_ids: list[str],
    known_phones: set[str],
    timezone_name: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = {
        "owned_records_count": 0,
        "hidden_not_owned_count": 0,
        "hidden_cancelled_count": 0,
        "hidden_deleted_count": 0,
        "hidden_past_count": 0,
        "hidden_parse_error_count": 0,
    }
    visible: list[dict[str, Any]] = []
    for row in rows:
        ownership_source = ownership_source_for_record(
            row,
            yclients_client_id=yclients_client_id,
            attributed_record_ids=set(attributed_record_ids),
            known_phones=known_phones,
        )
        if ownership_source == "none":
            counts["hidden_not_owned_count"] += 1
            continue
        counts["owned_records_count"] += 1
        status = _normalize_status(row.get("status") or row.get("record_status") or row.get("state"))
        if _has_deleted_flag(row) or status in {"deleted", "delete", "removed", "archived", "archive"}:
            counts["hidden_deleted_count"] += 1
            continue
        if not _is_yclients_record_active(row):
            counts["hidden_cancelled_count"] += 1
            continue
        booking = _booking_from_payload(row, timezone_name=timezone_name)
        if booking is None:
            counts["hidden_parse_error_count"] += 1
            continue
        if not is_future_booking(booking, timezone_name=timezone_name, now=now):
            counts["hidden_past_count"] += 1
            continue
        visible.append(row)
    return visible, counts


def ownership_source_for_record(
    row: dict[str, Any],
    *,
    yclients_client_id: str | None,
    attributed_record_ids: set[str],
    known_phones: set[str],
) -> str:
    record_id = _clean_text(row.get("record_id") or row.get("id") or row.get("booking_id") or row.get("visit_id"))
    if record_id and record_id in attributed_record_ids:
        return "attribution"
    expected_client_id = _clean_text(yclients_client_id)
    actual_client_id = _extract_record_client_id(row)
    actual_phone = _extract_record_client_phone(row)
    if expected_client_id and actual_client_id and actual_client_id == expected_client_id:
        return "client_id"
    if actual_phone and actual_phone in known_phones:
        return "phone"
    request_client_id = _clean_text(row.get("_max_request_client_id"))
    if expected_client_id and request_client_id and request_client_id == expected_client_id:
        return "request_client_id"
    request_phone = _normalize_phone_digits(row.get("_max_request_phone"))
    if request_phone and request_phone in known_phones:
        return "request_phone"
    return "none"


def is_record_owned_by_user(
    row: dict[str, Any],
    user: User | None,
    attributed_record_ids: set[str],
    known_phones: set[str],
) -> bool:
    return ownership_source_for_record(
        row,
        yclients_client_id=_clean_text(user.yclients_client_id if user else None),
        attributed_record_ids=attributed_record_ids,
        known_phones=known_phones,
    ) != "none"


def is_record_visible_active(row: dict[str, Any], branch_now: datetime, *, timezone_name: str = DEFAULT_BRANCH_TIMEZONE) -> bool:
    if not _is_yclients_record_active(row):
        return False
    booking = _booking_from_payload(row, timezone_name=timezone_name)
    return bool(booking and is_future_booking(booking, timezone_name=timezone_name, now=branch_now))


def _extract_record_client_id(row: dict[str, Any]) -> str:
    client = row.get("client") if isinstance(row.get("client"), dict) else {}
    return _clean_text(client.get("id") or client.get("client_id") or row.get("client_id") or row.get("yclients_client_id"))


def _extract_record_client_phone(row: dict[str, Any]) -> str:
    client = row.get("client") if isinstance(row.get("client"), dict) else {}
    for value in (client.get("phone"), client.get("tel"), row.get("phone"), row.get("client_phone"), row.get("tel")):
        normalized = _normalize_phone_digits(value)
        if normalized:
            return normalized
    for container in (client.get("phones"), row.get("phones")):
        if not isinstance(container, list):
            continue
        for item in container:
            value = item
            if isinstance(item, dict):
                value = item.get("phone") or item.get("number") or item.get("tel")
            normalized = _normalize_phone_digits(value)
            if normalized:
                return normalized
    return ""


def _normalize_phone_digits(value: Any) -> str:
    normalized = normalize_phone(str(value or "")) if value else ""
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) == 10:
        return f"7{digits}"
    if len(digits) == 11 and digits.startswith("8"):
        return f"7{digits[1:]}"
    return digits



def _is_yclients_record_active(item: dict[str, Any] | MyBookingItem) -> bool:
    raw = item.raw if isinstance(item, MyBookingItem) else item
    raw_status = item.raw_status if isinstance(item, MyBookingItem) else _clean_text(raw.get("raw_status") or raw.get("status") or raw.get("record_status") or raw.get("state"))
    if _has_deleted_flag(raw) or _is_explicitly_inactive_status(raw_status):
        return False
    attendance = _extract_attendance(raw)
    if attendance in {"-1", "1"}:
        return False
    if attendance in {"0", "2"}:
        return True
    normalized_status = _normalize_status(raw_status)
    if not normalized_status:
        return True
    return normalized_status in _ACTIVE_BOOKING_STATUSES


def _has_deleted_flag(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    for key in ("deleted", "is_deleted", "removed", "is_removed"):
        value = row.get(key)
        if value is True:
            return True
        if _clean_text(value).lower() in {"1", "true", "yes", "y", "да"}:
            return True
    return False


def _extract_attendance(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    value = row.get("attendance")
    if value is None:
        value = row.get("visit_attendance")
    return _clean_text(value)

def _is_explicitly_inactive_status(status: Any) -> bool:
    """Return True only for statuses Telegram reference hides from active records."""

    normalized = _normalize_status(status)
    return normalized in _CANCELLED_OR_PAST_STATUSES or normalized in {"deleted", "delete", "removed", "archived", "archive"}

def _to_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _normalize_status(status: Any) -> str:
    return _clean_text(status).lower().replace("-", "_").replace(" ", "_")


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


def _request_mode(yclients_client_id: str | None, phones: set[str]) -> str:
    has_client = bool(_clean_text(yclients_client_id))
    has_phone = bool(phones)
    if has_client and has_phone:
        return "by_both"
    if has_client:
        return "by_client_id"
    if has_phone:
        return "by_phone"
    return "no_identity"


def _runtime_error_diagnostic(
    *,
    function: str,
    max_user_id: str | None,
    platform_user_id: str | None,
    user: User | None,
    company_id_present: bool,
    endpoint_name: str,
    request_mode: str,
    records_count: int,
    parsed_records_count: int,
    active_records_count: int,
    skipped_malformed_count: int,
    exc: Exception,
    duration_ms: int,
) -> dict[str, Any]:
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return {
        "function": function,
        "max_user_id": max_user_id or "n/a",
        "platform_user_id": platform_user_id or "n/a",
        "has_yclients_client_id": bool(user and user.yclients_client_id),
        "has_phone": bool(user and user.phone),
        "user_role": _clean_text(user.role if user else None) or "n/a",
        "yclients_company_id_present": company_id_present,
        "endpoint_name": endpoint_name,
        "request_mode": request_mode,
        "yclients_records_count": records_count,
        "parsed_records_count": parsed_records_count,
        "active_records_count": active_records_count,
        "skipped_malformed_count": skipped_malformed_count,
        "error_type": type(exc).__name__,
        "error_message_short": str(exc)[:180],
        "traceback_last_5_lines": "".join(tb_lines[-5:])[:900],
        "duration_ms": duration_ms,
    }


def _rate_limit_cooldown_active(key: tuple[str, str, str, str]) -> bool:
    expires_at = _RATE_LIMIT_COOLDOWNS.get(key)
    if not expires_at:
        return False
    if time.monotonic() >= expires_at:
        _RATE_LIMIT_COOLDOWNS.pop(key, None)
        return False
    return True


def _set_rate_limit_cooldown(key: tuple[str, str, str, str], retry_after_seconds: int | None) -> None:
    delay = max(5, min(int(retry_after_seconds or 5), 60))
    _RATE_LIMIT_COOLDOWNS[key] = time.monotonic() + delay


def _rate_limit_diagnostic(*, function: str, max_user_id: str | None, user: User | None, endpoint_name: str, request_mode: str, retry_after_seconds: int, duration_ms: int) -> dict[str, Any]:
    return {
        "function": function,
        "endpoint_name": endpoint_name,
        "request_mode": request_mode,
        "retry_after_seconds": retry_after_seconds,
        "max_user_id": max_user_id or "n/a",
        "has_yclients_client_id": bool(user and user.yclients_client_id),
        "has_phone": bool(user and user.phone),
        "action": "my_bookings_open",
        "duration_ms": duration_ms,
        "error_type": "YClientsRateLimitError",
        "error_category": "rate_limit",
    }
