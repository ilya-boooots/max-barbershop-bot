"""Transport-neutral YClients integration layer."""

from __future__ import annotations

from .auth import build_auth_headers, build_authorization_header
from .client import DEFAULT_YCLIENTS_BASE_URL, YClientsClient, YClientsResponse
from .dto import (
    YClientsBookingRecord,
    YClientsClientCard,
    YClientsCredentials,
    YClientsHealthCheckResult,
    YClientsService,
    YClientsServiceCategory,
    YClientsSlot,
    YClientsStaff,
    YClientsVisit,
)
from .exceptions import (
    YClientsAuthError,
    YClientsConfigError,
    YClientsError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsValidationError,
)
from .service import YClientsNotifier, YClientsServiceLayer, build_yclients_client_from_env
from .utils import MAX_BOOKING_COMMENT_MARKER

__all__ = [
    "DEFAULT_YCLIENTS_BASE_URL",
    "MAX_BOOKING_COMMENT_MARKER",
    "YClientsAuthError",
    "YClientsBookingRecord",
    "YClientsClient",
    "YClientsClientCard",
    "YClientsConfigError",
    "YClientsCredentials",
    "YClientsError",
    "YClientsHealthCheckResult",
    "YClientsNotFoundError",
    "YClientsNotifier",
    "YClientsRateLimitError",
    "YClientsResponse",
    "YClientsServerError",
    "YClientsService",
    "YClientsServiceCategory",
    "YClientsServiceLayer",
    "YClientsSlot",
    "YClientsStaff",
    "YClientsTransportError",
    "YClientsValidationError",
    "YClientsVisit",
    "build_auth_headers",
    "build_authorization_header",
    "build_yclients_client_from_env",
]
