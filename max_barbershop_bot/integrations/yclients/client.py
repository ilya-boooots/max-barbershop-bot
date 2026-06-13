"""Centralized asynchronous HTTP client for YClients."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import aiohttp

from .auth import build_auth_headers
from .dto import YClientsCredentials
from .exceptions import (
    YClientsAuthError,
    YClientsConfigError,
    YClientsError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsValidationError,
    make_safe_response_snippet,
    sanitize_yclients_endpoint,
)

logger = logging.getLogger(__name__)

DEFAULT_YCLIENTS_BASE_URL = "https://api.yclients.com"
DEFAULT_YCLIENTS_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class RetryPolicy:
    """Small retry policy for transient YClients errors."""

    attempts: int = 3
    base_delay_seconds: float = 0.4
    max_delay_seconds: float = 3.0


@dataclass(frozen=True)
class YClientsResponse:
    """Parsed HTTP response plus safe diagnostics."""

    status: int
    body: dict[str, Any] | list[Any] | str
    trace_id: str
    method: str
    path_with_query: str
    response_snippet: str
    partner_token_present: bool
    user_token_present: bool
    context: dict[str, Any]


class YClientsClient:
    """Transport-neutral YClients API client.

    The client owns or reuses an ``aiohttp.ClientSession``, centralizes
    authorization headers and maps HTTP/network failures to YClients exceptions.
    It is transport-neutral and never logs tokens.
    """

    _failures: deque[float] = deque()
    _cooldown_until: float = 0.0
    _failure_window_seconds = 60.0
    _failure_threshold = 5
    _cooldown_seconds = 30.0

    def __init__(
        self,
        *,
        partner_token: str,
        user_token: str | None = None,
        company_id: str | int | None = None,
        base_url: str = DEFAULT_YCLIENTS_BASE_URL,
        timeout_seconds: float = DEFAULT_YCLIENTS_TIMEOUT_SECONDS,
        session: aiohttp.ClientSession | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._credentials = YClientsCredentials(
            partner_token=partner_token,
            user_token=user_token,
            company_id=str(company_id).strip() if company_id is not None else None,
        )
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session = session
        self._owns_session = session is None
        self._retry = retry_policy or RetryPolicy()

    @property
    def company_id(self) -> str | None:
        """Configured YClients company id, if one was provided."""

        return self._credentials.company_id

    async def start(self) -> None:
        """Create an underlying session when the client owns it."""

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._owns_session = True

    async def close(self) -> None:
        """Close the owned HTTP session."""

        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "YClientsClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> YClientsResponse:
        """Perform an HTTP request and return a parsed response wrapper."""

        self._ensure_available()
        method_upper = method.upper()
        path_with_query = self._build_path_with_query(path, params)
        partner_token_present = bool(self._credentials.partner_token)
        user_token_present = bool(self._credentials.user_token)
        if not self._credentials.company_id or not partner_token_present or not user_token_present:
            raise YClientsConfigError(
                "YClients settings are incomplete",
                trace_id=uuid4().hex[:12],
                method=method_upper,
                endpoint=path_with_query,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
            )
        await self.start()
        if self._session is None:
            raise YClientsTransportError("YClients HTTP session is not initialized")

        trace_id = uuid4().hex[:12]
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = build_auth_headers(self._credentials)
        context = self._safe_context(params=params, json_data=json_data, form_data=form_data)

        last_exc: Exception | None = None
        for attempt in range(1, max(self._retry.attempts, 1) + 1):
            started_at = time.perf_counter()
            try:
                async with self._session.request(
                    method_upper,
                    url,
                    params=params,
                    json=json_data,
                    data=form_data,
                    headers=headers,
                    timeout=self._timeout,
                ) as response:
                    body_text = await response.text()
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    request_id = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
                    logger.info(
                        "YClients request method=%s path=%s status_code=%s request_id=%s duration_ms=%s params=%s payload_fields=%s",
                        method_upper,
                        sanitize_yclients_endpoint(path_with_query),
                        response.status,
                        self._mask_identifier(request_id),
                        duration_ms,
                        sorted((params or {}).keys()),
                        context.get("payload_fields", []),
                    )

                    if response.status in {429} or 500 <= response.status < 600:
                        self._record_failure(f"status_{response.status}")
                        if attempt < self._retry.attempts:
                            await asyncio.sleep(self._backoff(attempt))
                            continue

                    payload = self._safe_json_parse(body_text)
                    yclients_response = YClientsResponse(
                        status=response.status,
                        body=payload,
                        trace_id=trace_id,
                        method=method_upper,
                        path_with_query=path_with_query,
                        response_snippet=self._response_snippet(body_text),
                        partner_token_present=partner_token_present,
                        user_token_present=user_token_present,
                        context=context,
                    )
                    if 200 <= response.status < 300:
                        self._record_success()
                    else:
                        logger.warning(
                            "YClients non-2xx response trace_id=%s method=%s path=%s status_code=%s snippet=%s",
                            trace_id,
                            method_upper,
                            sanitize_yclients_endpoint(path_with_query),
                            response.status,
                            yclients_response.response_snippet or "<empty>",
                        )
                    return yclients_response
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                self._record_failure(type(exc).__name__)
                logger.warning(
                    "YClients transport error trace_id=%s method=%s path=%s attempt=%s/%s duration_ms=%s error=%s",
                    trace_id,
                    method_upper,
                    path_with_query,
                    attempt,
                    self._retry.attempts,
                    duration_ms,
                    type(exc).__name__,
                )
                if attempt < self._retry.attempts:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise YClientsTransportError(
                    "YClients API transport error",
                    trace_id=trace_id,
                    method=method_upper,
                    endpoint=path_with_query,
                    response_snippet=make_safe_response_snippet(f"{type(exc).__name__}: {exc}", max_chars=300),
                    partner_token_present=partner_token_present,
                    user_token_present=user_token_present,
                    context=context,
                ) from exc

        raise YClientsTransportError(
            "YClients API transport error",
            trace_id=trace_id,
            method=method_upper,
            endpoint=path_with_query,
            response_snippet=(make_safe_response_snippet(f"{type(last_exc).__name__}: {last_exc}", max_chars=300) if last_exc else None),
            partner_token_present=partner_token_present,
            user_token_present=user_token_present,
            context=context,
        ) from last_exc

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        response = await self.request("GET", path, params=params)
        self.raise_for_status(response)
        return self._body_as_mapping_or_list(response.body)

    async def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> dict[str, Any] | list[Any]:
        response = await self.request("POST", path, params=params, json_data=json_data, form_data=form_data)
        self.raise_for_status(response)
        return self._body_as_mapping_or_list(response.body)

    async def put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> dict[str, Any] | list[Any]:
        response = await self.request("PUT", path, params=params, json_data=json_data, form_data=form_data)
        self.raise_for_status(response)
        return self._body_as_mapping_or_list(response.body)

    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        response = await self.request("DELETE", path, params=params)
        self.raise_for_status(response)
        return self._body_as_mapping_or_list(response.body)

    def raise_for_status(self, response: YClientsResponse) -> None:
        """Raise a custom exception for non-success YClients responses."""

        status_code = response.status
        if 200 <= status_code < 300:
            return

        exception_kwargs = {
            "trace_id": response.trace_id,
            "status_code": status_code,
            "method": response.method,
            "endpoint": sanitize_yclients_endpoint(response.path_with_query),
            "response_snippet": response.response_snippet,
            "partner_token_present": response.partner_token_present,
            "user_token_present": response.user_token_present,
            "context": response.context,
        }
        message = self._extract_message(response.body)
        if status_code in {400, 422}:
            raise YClientsValidationError(message, **exception_kwargs)
        if status_code in {401, 403}:
            raise YClientsAuthError(message, **exception_kwargs)
        if status_code == 404:
            raise YClientsNotFoundError(message, **exception_kwargs)
        if status_code == 429:
            raise YClientsRateLimitError(message, **exception_kwargs)
        if 500 <= status_code < 600:
            raise YClientsServerError(message, **exception_kwargs)
        raise YClientsServerError(f"Unexpected YClients status: {status_code}", **exception_kwargs)

    def _backoff(self, attempt: int) -> float:
        base = min(self._retry.base_delay_seconds * (2 ** (attempt - 1)), self._retry.max_delay_seconds)
        return max(0.1, base + random.uniform(0.0, 0.25))

    @classmethod
    def _ensure_available(cls) -> None:
        now = time.monotonic()
        if now < cls._cooldown_until:
            raise YClientsServerError("YClients cooldown is active")
        if cls._cooldown_until and now >= cls._cooldown_until:
            cls._cooldown_until = 0.0
            logger.info("YClients cooldown recovered")

    @classmethod
    def _record_failure(cls, reason: str) -> None:
        now = time.monotonic()
        cls._failures.append(now)
        while cls._failures and now - cls._failures[0] > cls._failure_window_seconds:
            cls._failures.popleft()
        if len(cls._failures) >= cls._failure_threshold and now >= cls._cooldown_until:
            cls._cooldown_until = now + cls._cooldown_seconds
            logger.error(
                "YClients cooldown activated failures=%s window_s=%.0f cooldown_s=%.0f reason=%s",
                len(cls._failures),
                cls._failure_window_seconds,
                cls._cooldown_seconds,
                reason,
            )

    @classmethod
    def _record_success(cls) -> None:
        if cls._failures:
            cls._failures.clear()

    @staticmethod
    def _safe_json_parse(body_text: str) -> dict[str, Any] | list[Any] | str:
        if not body_text:
            return {}
        try:
            return json.loads(body_text)
        except json.JSONDecodeError:
            return body_text[:250]

    @staticmethod
    def _body_as_mapping_or_list(body: dict[str, Any] | list[Any] | str) -> dict[str, Any] | list[Any]:
        if isinstance(body, str):
            return {"raw": body}
        return body

    @staticmethod
    def _response_snippet(body_text: str) -> str:
        return make_safe_response_snippet(body_text, max_chars=300)

    @staticmethod
    def _build_path_with_query(path: str, params: dict[str, Any] | None) -> str:
        normalized = f"/{path.lstrip('/')}"
        if not params:
            return normalized
        query = urlencode(params, doseq=True)
        return sanitize_yclients_endpoint(f"{normalized}?{query}" if query else normalized) or normalized

    @staticmethod
    def _extract_message(payload: dict[str, Any] | list[Any] | str) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error", "errors", "meta"):
                value = payload.get(key)
                if value:
                    return str(value)[:240]
        if isinstance(payload, str) and payload:
            return payload[:240]
        return "YClients API error"

    @staticmethod
    def _safe_context(
        *,
        params: dict[str, Any] | None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None,
    ) -> dict[str, Any]:
        payload_fields: list[str] = []
        if isinstance(json_data, dict):
            payload_fields.extend(json_data.keys())
        elif isinstance(json_data, list):
            payload_fields.extend({key for item in json_data for key in item.keys()})
        if isinstance(form_data, dict):
            payload_fields.extend(form_data.keys())
        elif isinstance(form_data, list):
            payload_fields.extend(str(key) for key, _ in form_data)
        return {
            "params": sorted((params or {}).keys()),
            "payload_fields": sorted(set(payload_fields)),
            "body_transport": "json" if json_data is not None else ("form" if form_data is not None else "none"),
        }

    @staticmethod
    def _mask_identifier(value: str | None) -> str:
        if not value:
            return "-"
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}***{value[-3:]}"
