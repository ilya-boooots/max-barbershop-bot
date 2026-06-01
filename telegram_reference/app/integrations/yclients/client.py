from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from uuid import uuid4
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from app.core.config import get_settings

from .auth import build_auth_headers
from .dto import YClientsCredentials
from .errors import (
    YClientsAuthError,
    YClientsBadRequestError,
    YClientsNotFoundError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsUnavailableError,
)

logger = logging.getLogger(__name__)
CredentialsProvider = Callable[[], Awaitable[YClientsCredentials]]


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_s: float = 0.4
    max_delay_s: float = 3.0


@dataclass(frozen=True)
class YClientsResponse:
    status: int
    body: dict[str, Any] | list[Any] | str
    trace_id: str
    method: str
    path_with_query: str
    response_snippet: str
    partner_token_present: bool
    user_token_present: bool
    transport_debug: dict[str, Any] | None = None


class YClientsClient:
    _failures: deque[float] = deque()
    _cooldown_until: float = 0.0
    _failure_window_s = 60.0
    _failure_threshold = 5
    _cooldown_s = 30.0

    def __init__(
        self,
        *,
        base_url: str,
        credentials_provider: CredentialsProvider,
        session: aiohttp.ClientSession,
        timeout_s: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        settings = get_settings()
        retries = settings.yclients_retry_max
        self._base_url = base_url.rstrip("/")
        self._credentials_provider = credentials_provider
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_s or settings.yclients_timeout_seconds)
        self._retry = retry_policy or RetryPolicy(attempts=retries)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None = None,
    ) -> YClientsResponse:
        self._ensure_available()
        trace_id = uuid4().hex[:12]
        url = f"{self._base_url}/{path.lstrip('/')}"
        credentials = await self._credentials_provider()
        headers = build_auth_headers(credentials)
        path_with_query = self._build_path_with_query(path, params)
        partner_token_present = bool(credentials.partner_token)
        user_token_present = bool(credentials.user_token)

        last_exc: Exception | None = None
        transport_debug = self._summarize_transport(json_data=json_data, form_data=form_data)
        logger.info(
            "yclients_transport_prepare trace_id=%s method=%s url=%s params=%s uses_json_arg=%s uses_data_arg=%s content_type=%s payload_keys=%s body_preview=%s",
            trace_id,
            method.upper(),
            path_with_query,
            sorted((params or {}).keys()),
            transport_debug.get("uses_json_arg"),
            transport_debug.get("uses_data_arg"),
            transport_debug.get("content_type"),
            transport_debug.get("payload_keys"),
            transport_debug.get("body_preview"),
        )
        for attempt in range(1, self._retry.attempts + 1):
            started_at = time.perf_counter()
            try:
                async with self._session.request(
                    method.upper(),
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
                    self._log_request(
                        method=method,
                        path=path,
                        status_code=response.status,
                        duration_ms=duration_ms,
                        request_id=request_id,
                        params=params,
                        json_data=json_data,
                        form_data=form_data,
                    )

                    if response.status in {429} or 500 <= response.status < 600:
                        self._log_failed_response(
                            trace_id=trace_id,
                            method=method,
                            url=url,
                            status_code=response.status,
                            response_text=body_text,
                        )
                        self._record_failure(f"status_{response.status}")
                        if attempt < self._retry.attempts:
                            backoff_s = self._backoff(attempt)
                            logger.warning(
                                "yclients_retry status=%s method=%s path=%s attempt=%s/%s backoff_s=%.2f",
                                response.status,
                                method.upper(),
                                path,
                                attempt,
                                self._retry.attempts,
                                backoff_s,
                            )
                            await asyncio.sleep(backoff_s)
                            continue
                    payload = self._safe_json_parse(body_text)
                    snippet = self._response_snippet(body_text)
                    yclients_response = YClientsResponse(
                        status=response.status,
                        body=payload,
                        trace_id=trace_id,
                        method=method.upper(),
                        path_with_query=path_with_query,
                        response_snippet=snippet,
                        partner_token_present=partner_token_present,
                        user_token_present=user_token_present,
                        transport_debug=transport_debug,
                    )
                    if 200 <= response.status < 300:
                        self._record_success()
                    else:
                        self._log_failed_response(trace_id=trace_id, method=method, url=url, status_code=response.status, response_text=snippet)
                    return yclients_response
            except (aiohttp.ClientConnectionError, aiohttp.ClientResponseError, asyncio.TimeoutError) as exc:
                last_exc = exc
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                logger.warning(
                    "yclients_transport_error trace_id=%s method=%s path=%s attempt=%s/%s duration_ms=%s error=%s",
                    trace_id,
                    method.upper(),
                    path,
                    attempt,
                    self._retry.attempts,
                    duration_ms,
                    type(exc).__name__,
                )
                self._record_failure(type(exc).__name__)
                if attempt < self._retry.attempts:
                    await asyncio.sleep(self._backoff(attempt))
                    continue
                raise YClientsTransportError(
                    "YClients API transport error",
                    trace_id=trace_id,
                    method=method.upper(),
                    endpoint=path_with_query,
                    response_snippet=f"{type(exc).__name__}: {str(exc)[:300]}",
                    partner_token_present=partner_token_present,
                    user_token_present=user_token_present,
                    transport_debug=transport_debug,
                ) from exc

        raise YClientsTransportError(
            "YClients API transport error",
            trace_id=trace_id,
            method=method.upper(),
            endpoint=path_with_query,
            response_snippet=(f"{type(last_exc).__name__}: {str(last_exc)[:300]}" if last_exc else None),
            partner_token_present=partner_token_present,
            user_token_present=user_token_present,
            transport_debug=transport_debug,
        ) from last_exc

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        response = await self.request("GET", path, params=params)
        self.raise_for_status(response)
        if isinstance(response.body, str):
            return {"raw": response.body}
        return response.body

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
        if isinstance(response.body, str):
            return {"raw": response.body}
        return response.body

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
        if isinstance(response.body, str):
            return {"raw": response.body}
        return response.body

    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        response = await self.request("DELETE", path, params=params)
        self.raise_for_status(response)
        if isinstance(response.body, str):
            return {"raw": response.body}
        return response.body

    def raise_for_status(self, response: YClientsResponse) -> None:
        self._raise_for_status(response=response)

    async def close(self) -> None:
        return None

    def _backoff(self, attempt: int) -> float:
        base = min(self._retry.base_delay_s * (2 ** (attempt - 1)), self._retry.max_delay_s)
        return max(0.1, base + random.uniform(0.0, 0.25))

    @classmethod
    def _ensure_available(cls) -> None:
        now = time.monotonic()
        if now < cls._cooldown_until:
            raise YClientsUnavailableError("YClients cooldown is active")
        if cls._cooldown_until and now >= cls._cooldown_until:
            cls._cooldown_until = 0.0
            logger.info("yclients_cooldown_recovered")

    @classmethod
    def _record_failure(cls, reason: str) -> None:
        now = time.monotonic()
        cls._failures.append(now)
        while cls._failures and now - cls._failures[0] > cls._failure_window_s:
            cls._failures.popleft()
        if len(cls._failures) >= cls._failure_threshold and now >= cls._cooldown_until:
            cls._cooldown_until = now + cls._cooldown_s
            logger.error(
                "yclients_cooldown_activated failures=%s window_s=%.0f cooldown_s=%.0f reason=%s",
                len(cls._failures),
                cls._failure_window_s,
                cls._cooldown_s,
                reason,
            )

    @classmethod
    def _record_success(cls) -> None:
        if cls._failures:
            cls._failures.clear()

    def _raise_for_status(self, *, response: YClientsResponse) -> None:
        status_code = response.status
        payload = response.body
        trace_id = response.trace_id
        method = response.method
        path_with_query = response.path_with_query
        response_snippet = response.response_snippet
        partner_token_present = response.partner_token_present
        user_token_present = response.user_token_present
        transport_debug = response.transport_debug
        message = self._extract_message(payload)
        if status_code in {200, 201, 202, 204}:
            return
        if status_code in {400, 422}:
            raise YClientsBadRequestError(
                message,
                trace_id=trace_id,
                status_code=status_code,
                method=method.upper(),
                endpoint=path_with_query,
                response_snippet=response_snippet,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
                transport_debug=transport_debug,
            )
        if status_code in {401, 403}:
            raise YClientsAuthError(
                message,
                trace_id=trace_id,
                status_code=status_code,
                method=method.upper(),
                endpoint=path_with_query,
                response_snippet=response_snippet,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
                transport_debug=transport_debug,
            )
        if status_code == 404:
            raise YClientsNotFoundError(
                message,
                trace_id=trace_id,
                status_code=status_code,
                method=method.upper(),
                endpoint=path_with_query,
                response_snippet=response_snippet,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
                transport_debug=transport_debug,
            )
        if status_code == 429:
            raise YClientsRateLimitError(
                message,
                trace_id=trace_id,
                status_code=status_code,
                method=method.upper(),
                endpoint=path_with_query,
                response_snippet=response_snippet,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
                transport_debug=transport_debug,
            )
        if 500 <= status_code < 600:
            raise YClientsServerError(
                message,
                trace_id=trace_id,
                status_code=status_code,
                method=method.upper(),
                endpoint=path_with_query,
                response_snippet=response_snippet,
                partner_token_present=partner_token_present,
                user_token_present=user_token_present,
                transport_debug=transport_debug,
            )
        raise YClientsServerError(
            f"Unexpected YClients status: {status_code}",
            trace_id=trace_id,
            status_code=status_code,
            method=method.upper(),
            endpoint=path_with_query,
            response_snippet=response_snippet,
            partner_token_present=partner_token_present,
            user_token_present=user_token_present,
            transport_debug=transport_debug,
        )

    def _log_failed_response(
        self,
        *,
        trace_id: str,
        method: str,
        url: str,
        status_code: int,
        response_text: str,
    ) -> None:
        trimmed = response_text.strip().replace("\n", " ")[:500]
        logger.error(
            "yclients_request_failed trace_id=%s method=%s endpoint=%s status=%s response=%s",
            trace_id,
            method.upper(),
            url,
            status_code,
            trimmed or "<empty>",
        )

    @staticmethod
    def _safe_json_parse(body_text: str) -> dict[str, Any] | list[Any]:
        if not body_text:
            return {}
        try:
            return json.loads(body_text)
        except json.JSONDecodeError:
            return {"raw": body_text[:250]}

    @staticmethod
    def _response_snippet(body_text: str) -> str:
        return body_text.strip().replace("\n", " ")[:1000]

    @staticmethod
    def _build_path_with_query(path: str, params: dict[str, Any] | None) -> str:
        normalized = f"/{path.lstrip('/')}"
        if not params:
            return normalized
        query = urlencode(params, doseq=True)
        return f"{normalized}?{query}" if query else normalized

    @staticmethod
    def _extract_message(payload: dict[str, Any] | list[Any]) -> str:
        if isinstance(payload, dict):
            for key in ("message", "error", "errors", "meta"):
                value = payload.get(key)
                if value:
                    return str(value)[:240]
        return "YClients API error"

    @staticmethod
    def _summarize_transport(*, json_data: dict[str, Any] | list[dict[str, Any]] | None, form_data: dict[str, Any] | list[tuple[str, Any]] | None) -> dict[str, Any]:
        body_transport = "json" if json_data is not None else ("form" if form_data is not None else "none")
        content_type = (
            "application/json"
            if json_data is not None
            else ("application/x-www-form-urlencoded" if form_data is not None else "none")
        )
        return {
            "body_transport": body_transport,
            "content_type": content_type,
            "uses_json_arg": json_data is not None,
            "uses_data_arg": form_data is not None,
            "payload_keys": YClientsClient._payload_fields(json_data=json_data, form_data=form_data),
            "body_preview": YClientsClient._build_body_preview_static(json_data=json_data, form_data=form_data),
        }

    @staticmethod
    def _payload_fields(
        *,
        json_data: dict[str, Any] | list[dict[str, Any]] | None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None,
    ) -> list[str]:
        if isinstance(json_data, dict):
            payload_fields = sorted(json_data.keys())
        elif isinstance(json_data, list):
            payload_fields = sorted({k for item in json_data for k in item.keys()})
        else:
            payload_fields = []
        if isinstance(form_data, dict):
            payload_fields = sorted(set(payload_fields + list(form_data.keys())))
        elif isinstance(form_data, list):
            payload_fields = sorted(set(payload_fields + [str(key) for key, _ in form_data]))
        return payload_fields

    @staticmethod
    def _build_body_preview_static(
        *,
        json_data: dict[str, Any] | list[dict[str, Any]] | None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None,
    ) -> str:
        if json_data is not None:
            if isinstance(json_data, dict):
                payload = {k: YClientsClient._preview_value(v) for k, v in json_data.items()}
            else:
                payload = [{k: YClientsClient._preview_value(v) for k, v in item.items()} for item in json_data]
            return json.dumps(payload, ensure_ascii=False)[:500]
        if form_data is not None:
            if isinstance(form_data, dict):
                serializable = [(key, form_data[key]) for key in sorted(form_data.keys())]
            else:
                serializable = form_data
            preview = {k: YClientsClient._preview_value(v) for k, v in serializable}
            return json.dumps(preview, ensure_ascii=False)[:500]
        return "{}"

    def _log_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        request_id: str | None,
        params: dict[str, Any] | None,
        json_data: dict[str, Any] | list[dict[str, Any]] | None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None,
    ) -> None:
        safe_params = sorted((params or {}).keys())
        payload_fields = self._payload_fields(json_data=json_data, form_data=form_data)
        transport_debug = self._summarize_transport(json_data=json_data, form_data=form_data)
        body_transport = str(transport_debug.get("body_transport") or "none")
        content_type = str(transport_debug.get("content_type") or "none")
        body_preview = str(transport_debug.get("body_preview") or "{}")

        logger.info(
            "YClients request method=%s path=%s status_code=%s request_id=%s duration_ms=%s params=%s payload_fields=%s body_transport=%s content_type=%s body_preview=%s",
            method.upper(),
            path,
            status_code,
            self._mask_identifier(request_id),
            duration_ms,
            safe_params,
            payload_fields,
            body_transport,
            content_type,
            body_preview,
        )

    def _build_body_preview(
        self,
        *,
        json_data: dict[str, Any] | list[dict[str, Any]] | None,
        form_data: dict[str, Any] | list[tuple[str, Any]] | None,
    ) -> str:
        return self._build_body_preview_static(json_data=json_data, form_data=form_data)

    @staticmethod
    def _preview_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: YClientsClient._preview_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [YClientsClient._preview_value(item) for item in value]
        if isinstance(value, str):
            return value[:80]
        return value

    @staticmethod
    def _mask_identifier(value: str | None) -> str:
        if not value:
            return "-"
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}***{value[-3:]}"
