from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from aiogram import Bot

from app.core.config import get_settings, mask_secret
from app.core.error_monitor import send_dev_alert
from app.repositories.yclients_settings import get_yclients_settings

from .client import YClientsClient
from .dto import YClientsCredentials, YClientsCredentialsDiagnostics, YClientsHealthCheckResult
from .endpoints import get_company
from .errors import (
    YClientsAuthError,
    YClientsCredentialsError,
    YClientsError,
    YClientsRateLimitError,
    YClientsServerError,
    YClientsTransportError,
    YClientsUnavailableError,
)

logger = logging.getLogger(__name__)

_staff_for_service_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_STAFF_FOR_SERVICE_TTL_S = 600


def _extract_data_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return None
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return None


def _staff_cache_get(company_id: str, service_id: str) -> list[dict[str, Any]] | None:
    entry = _staff_for_service_cache.get((company_id, service_id))
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _staff_for_service_cache.pop((company_id, service_id), None)
        return None
    return payload


def _staff_cache_set(company_id: str, service_id: str, payload: list[dict[str, Any]]) -> None:
    _staff_for_service_cache[(company_id, service_id)] = (time.monotonic() + _STAFF_FOR_SERVICE_TTL_S, payload)


async def get_staff_for_service(company_id: int, service_id: int) -> list[dict[str, Any]]:
    normalized_company_id = str(company_id).strip()
    normalized_service_id = str(service_id).strip()
    if not normalized_company_id or not normalized_service_id:
        raise YClientsError(
            "service_id is required for staff-by-service filtering",
            endpoint="/api/v1/company/{company_id}/staff?service_ids[]={service_id}",
        )

    cached = _staff_cache_get(normalized_company_id, normalized_service_id)
    if cached is not None:
        return cached

    client, _ = await build_yclients_client()
    try:
        response = await client.request(
            "GET",
            f"/api/v1/company/{normalized_company_id}/staff",
            params={"service_ids[]": normalized_service_id},
        )
        client.raise_for_status(response)
    except YClientsAuthError:
        raise
    except YClientsError:
        raise
    finally:
        await client.close()

    if isinstance(response.body, str):
        raise YClientsError(
            "Invalid payload for staff-by-service endpoint",
            trace_id=response.trace_id,
            status_code=response.status,
            method=response.method,
            endpoint=response.path_with_query,
            response_snippet=response.response_snippet,
        )

    rows = _extract_data_rows(response.body)
    if rows is None:
        raise YClientsError(
            "Invalid payload for staff-by-service endpoint",
            trace_id=response.trace_id,
            status_code=response.status,
            method=response.method,
            endpoint=response.path_with_query,
            response_snippet=response.response_snippet,
        )

    _staff_cache_set(normalized_company_id, normalized_service_id, rows)
    return rows


_shared_http_session: aiohttp.ClientSession | None = None


def set_shared_http_session(session: aiohttp.ClientSession) -> None:
    global _shared_http_session
    _shared_http_session = session


def get_shared_http_session() -> aiohttp.ClientSession | None:
    return _shared_http_session


def require_shared_http_session() -> aiohttp.ClientSession:
    if _shared_http_session is None:
        raise RuntimeError("Shared HTTP session is not initialized")
    return _shared_http_session


async def build_yclients_client() -> tuple[YClientsClient, str]:
    credentials, _ = await get_yclients_credentials()
    settings = get_settings()
    db_settings = await get_yclients_settings()
    base_url = (db_settings.base_url if db_settings else None) or settings.yclients_base_url or "https://api.yclients.com"

    async def _provider() -> YClientsCredentials:
        return credentials

    return (
        YClientsClient(
            base_url=base_url,
            credentials_provider=_provider,
            session=require_shared_http_session(),
        ),
        credentials.company_id,
    )


async def notify_yclients_exception(bot: Bot, *, exc: Exception, action: str) -> None:
    trace_id = getattr(exc, "trace_id", None) or "n/a"
    text = (
        "🚨 YClients: техническая ошибка\n"
        f"🧩 trace_id: {trace_id}\n"
        f"🎬 Действие: {action}\n"
        f"🧠 Тип: {type(exc).__name__}\n"
        f"💬 Кратко: {str(exc)[:200] or '—'}"
    )
    await send_dev_alert(bot, text)


async def get_yclients_credentials() -> tuple[YClientsCredentials, YClientsCredentialsDiagnostics]:
    settings = get_settings()
    db_settings = await get_yclients_settings()

    if db_settings and db_settings.company_id and db_settings.partner_token:
        partner_token = db_settings.partner_token
        user_token = db_settings.user_token
        company_id = db_settings.company_id
        source = "db"
    else:
        partner_token = settings.yclients_partner_token
        user_token = settings.yclients_user_token
        company_id = settings.yclients_company_id
        source = "env"

    missing = []
    if not partner_token:
        missing.append("YCLIENTS_PARTNER_TOKEN")
    if not company_id:
        missing.append("YCLIENTS_COMPANY_ID")

    diagnostics = YClientsCredentialsDiagnostics(
        partner_token_masked=mask_secret(partner_token),
        user_token_masked=mask_secret(user_token),
        company_id_masked=mask_secret(company_id),
        source=source,
    )

    if missing:
        raise YClientsCredentialsError(
            f"Missing YClients credentials: {', '.join(missing)} | diagnostics={diagnostics}"
        )

    return (
        YClientsCredentials(partner_token=partner_token, user_token=user_token, company_id=company_id),
        diagnostics,
    )


async def yclients_health_check(
    *,
    credentials_override: YClientsCredentials | None = None,
    base_url_override: str | None = None,
) -> YClientsHealthCheckResult:
    if credentials_override is None:
        try:
            credentials, _ = await get_yclients_credentials()
        except YClientsCredentialsError:
            return YClientsHealthCheckResult(
                ok=False,
                status_code=None,
                short_message="Не заполнены ключи YClients в настройках",
            )
    else:
        credentials = credentials_override

    settings = get_settings()
    db_settings = await get_yclients_settings()
    base_url = base_url_override or (db_settings.base_url if db_settings else None) or settings.yclients_base_url or "https://api.yclients.com"

    client = YClientsClient(
        base_url=base_url,
        credentials_provider=_build_static_credentials_provider(credentials),
        session=require_shared_http_session(),
    )

    try:
        await get_company(client, company_id=credentials.company_id)
        return YClientsHealthCheckResult(ok=True, status_code=200, short_message="Подключение к YClients работает")
    except YClientsError as exc:
        return _health_result_from_error(exc)


def _build_static_credentials_provider(credentials: YClientsCredentials):
    async def _provider() -> YClientsCredentials:
        return credentials

    return _provider


async def yclients_integration_self_test() -> dict[str, Any]:
    results: dict[str, Any] = {
        "client_constructed": True,
        "missing_credentials_error": False,
        "healthcheck_401": False,
        "healthcheck_429": False,
        "healthcheck_5xx": False,
    }

    settings = get_settings()
    provider = _build_static_credentials_provider(
        YClientsCredentials(partner_token="***", user_token="***", company_id="1")
    )
    client = YClientsClient(
        base_url=settings.yclients_base_url or "https://api.yclients.com",
        credentials_provider=provider,
        session=require_shared_http_session(),
    )
    await client.close()

    original = get_settings()
    if not original.yclients_partner_token or not original.yclients_company_id:
        try:
            await get_yclients_credentials()
        except YClientsCredentialsError:
            results["missing_credentials_error"] = True

    results["healthcheck_401"] = _health_result_from_error(YClientsAuthError("x")).status_code == 401
    results["healthcheck_429"] = _health_result_from_error(YClientsRateLimitError("x")).status_code == 429
    results["healthcheck_5xx"] = _health_result_from_error(YClientsServerError("x")).status_code == 503

    return results


def _health_result_from_error(exc: YClientsError) -> YClientsHealthCheckResult:
    if isinstance(exc, YClientsAuthError):
        return YClientsHealthCheckResult(ok=False, status_code=401, short_message="Ошибка токенов доступа")
    if isinstance(exc, YClientsRateLimitError):
        return YClientsHealthCheckResult(ok=False, status_code=429, short_message="Слишком много запросов к API")
    if isinstance(exc, YClientsServerError):
        return YClientsHealthCheckResult(ok=False, status_code=503, short_message="Сервис YClients временно недоступен")
    if isinstance(exc, YClientsTransportError):
        return YClientsHealthCheckResult(ok=False, status_code=None, short_message="Нет соединения с API YClients")
    if isinstance(exc, YClientsUnavailableError):
        return YClientsHealthCheckResult(ok=False, status_code=503, short_message="YClients временно недоступен, повторите позже")
    logger.warning("YClients health-check error: %s", type(exc).__name__)
    return YClientsHealthCheckResult(ok=False, status_code=None, short_message="Ошибка проверки YClients")
