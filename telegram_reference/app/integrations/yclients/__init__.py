from .client import RetryPolicy, YClientsClient
from .dto import YClientsCredentials, YClientsCredentialsDiagnostics, YClientsHealthCheckResult
from .errors import (
    YClientsAuthError,
    YClientsBadRequestError,
    YClientsCredentialsError,
    YClientsError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsUnavailableError,
)
from .service import (
    build_yclients_client,
    get_staff_for_service,
    get_yclients_credentials,
    notify_yclients_exception,
    set_shared_http_session,
    yclients_health_check,
    yclients_integration_self_test,
)

__all__ = [
    "RetryPolicy",
    "YClientsClient",
    "YClientsCredentials",
    "YClientsCredentialsDiagnostics",
    "YClientsHealthCheckResult",
    "YClientsError",
    "YClientsCredentialsError",
    "YClientsAuthError",
    "YClientsRateLimitError",
    "YClientsBadRequestError",
    "YClientsNotFoundError",
    "YClientsServerError",
    "YClientsTransportError",
    "YClientsUnavailableError",
    "build_yclients_client",
    "get_staff_for_service",
    "get_yclients_credentials",
    "notify_yclients_exception",
    "set_shared_http_session",
    "yclients_health_check",
    "yclients_integration_self_test",
]
