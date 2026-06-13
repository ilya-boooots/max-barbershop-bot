"""Exception hierarchy for the transport-neutral YClients integration."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4


YCLIENTS_ERROR_CREDENTIALS = "credentials"
YCLIENTS_ERROR_AUTH = "auth"
YCLIENTS_ERROR_RATE_LIMIT = "rate_limit"
YCLIENTS_ERROR_SERVER = "server"
YCLIENTS_ERROR_TRANSPORT = "transport"
YCLIENTS_ERROR_UNAVAILABLE = "unavailable"
YCLIENTS_ERROR_VALIDATION = "validation"
YCLIENTS_ERROR_BUSINESS = "business"

_AUTHORIZATION_RE = re.compile(r'(?i)(authorization\s*[:=]\s*)([^,;\s}]+(?:\s+[^,;\s}]+)?)')
_TOKEN_FIELD_RE = re.compile(r'(?i)(partner_token|user_token|token|access_token|bearer)(["\']?\s*[:=]\s*["\']?)([^"\'\s,;&}]+)')
_BEARER_RE = re.compile(r'(?i)bearer\s+[a-z0-9._~+/=-]{12,}')
_PHONE_RE = re.compile(r'(?<!\d)(?:\+?7|8)?[\s()\-]*\d{3}[\s()\-]*\d{3}[\s()\-]*\d{2}[\s()\-]*\d{2}(?!\d)')
_LONG_SECRET_RE = re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9._~+/=-]{24,}(?![A-Za-z0-9])')
_SECRET_QUERY_KEYS = {"token", "partner_token", "user_token", "access_token", "authorization", "auth"}


def sanitize_yclients_diagnostic(value: str) -> str:
    """Return a short diagnostic string without tokens, auth headers, or full phones."""

    text = str(value or "")
    text = _AUTHORIZATION_RE.sub(r"\1***", text)
    text = _TOKEN_FIELD_RE.sub(r"\1\2***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _PHONE_RE.sub("***phone***", text)
    return _LONG_SECRET_RE.sub("***", text)


def make_safe_response_snippet(payload_or_text: Any, max_chars: int = 300) -> str:
    """Serialize, sanitize, and truncate a YClients response snippet for diagnostics."""

    if payload_or_text is None:
        return ""
    if isinstance(payload_or_text, (dict, list)):
        text = json.dumps(payload_or_text, ensure_ascii=False, default=str)
    else:
        text = str(payload_or_text)
    text = sanitize_yclients_diagnostic(text.strip().replace("\n", " "))
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}…"


def sanitize_yclients_endpoint(endpoint: str | None) -> str | None:
    """Remove secret query values from a diagnostic endpoint path."""

    if not endpoint:
        return endpoint
    parts = urlsplit(str(endpoint))
    query = urlencode(
        [(key, "***" if key.lower() in _SECRET_QUERY_KEYS else value) for key, value in parse_qsl(parts.query, keep_blank_values=True)],
        doseq=True,
    )
    sanitized = urlunsplit(("", "", parts.path or str(endpoint).split("?", 1)[0], query, ""))
    return sanitize_yclients_diagnostic(sanitized)


def classify_yclients_error(exc: BaseException | None = None, *, status_code: int | None = None) -> str:
    """Classify YClients failures using the Telegram reference taxonomy."""

    status = status_code if status_code is not None else getattr(exc, "status_code", None)
    if status in {401, 403}:
        return YCLIENTS_ERROR_AUTH
    if status == 429:
        return YCLIENTS_ERROR_RATE_LIMIT
    if status in {500, 502, 503, 504}:
        return YCLIENTS_ERROR_SERVER
    if status in {400, 422}:
        return YCLIENTS_ERROR_VALIDATION
    if isinstance(exc, YClientsConfigError):
        return YCLIENTS_ERROR_CREDENTIALS
    if isinstance(exc, YClientsAuthError):
        return YCLIENTS_ERROR_AUTH
    if isinstance(exc, YClientsRateLimitError):
        return YCLIENTS_ERROR_RATE_LIMIT
    if isinstance(exc, YClientsServerError):
        return YCLIENTS_ERROR_SERVER
    if isinstance(exc, YClientsTransportError):
        return YCLIENTS_ERROR_TRANSPORT
    if isinstance(exc, (YClientsValidationError, YClientsNotFoundError)):
        return YCLIENTS_ERROR_VALIDATION
    if isinstance(exc, YClientsError):
        return YCLIENTS_ERROR_UNAVAILABLE
    return YCLIENTS_ERROR_UNAVAILABLE


def yclients_trace_id(exc: BaseException | None = None) -> str:
    """Return existing trace id or generate a safe one."""

    trace_id = getattr(exc, "trace_id", None)
    return str(trace_id) if trace_id else uuid4().hex[:12]


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
        self.error_category = classify_yclients_error(self, status_code=status_code)


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
