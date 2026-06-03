"""Exception hierarchy for the transport-neutral YClients integration."""

from __future__ import annotations

from typing import Any


class YClientsError(RuntimeError):
    """Base exception for all YClients integration errors."""

    def __init__(
        self,
        message: str,
        *,
        trace_id: str | None = None,
        status_code: int | None = None,
        method: str | None = None,
        endpoint: str | None = None,
        response_snippet: str | None = None,
        partner_token_present: bool | None = None,
        user_token_present: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.trace_id = trace_id
        self.status_code = status_code
        self.method = method
        self.endpoint = endpoint
        self.response_snippet = response_snippet
        self.partner_token_present = partner_token_present
        self.user_token_present = user_token_present
        self.context = context or {}


class YClientsConfigError(YClientsError):
    """Raised when required YClients settings are missing or invalid."""


class YClientsAuthError(YClientsError):
    """401/403 authentication and authorization failures."""


class YClientsValidationError(YClientsError):
    """400/422 request validation error."""


class YClientsNotFoundError(YClientsError):
    """404 not found error."""


class YClientsRateLimitError(YClientsError):
    """429 request throttling error."""


class YClientsServerError(YClientsError):
    """5xx service-side failure or unexpected HTTP status."""


class YClientsTransportError(YClientsError):
    """Network-level transport failure (timeouts, DNS, connection)."""
