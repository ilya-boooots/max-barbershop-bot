from __future__ import annotations


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
        transport_debug: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.trace_id = trace_id
        self.status_code = status_code
        self.method = method
        self.endpoint = endpoint
        self.response_snippet = response_snippet
        self.partner_token_present = partner_token_present
        self.user_token_present = user_token_present
        self.transport_debug = transport_debug


class YClientsCredentialsError(YClientsError):
    """Raised when required YClients credentials are missing."""


class YClientsAuthError(YClientsError):
    """401/403 authentication and authorization failures."""


class YClientsRateLimitError(YClientsError):
    """429 request throttling error."""


class YClientsBadRequestError(YClientsError):
    """400/422 request validation error."""


class YClientsNotFoundError(YClientsError):
    """404 not found error."""


class YClientsServerError(YClientsError):
    """5xx service-side failure."""


class YClientsTransportError(YClientsError):
    """Network-level transport failure (timeouts, DNS, connection)."""


class YClientsUnavailableError(YClientsError):
    """Temporary cooldown after repeated YClients failures."""
