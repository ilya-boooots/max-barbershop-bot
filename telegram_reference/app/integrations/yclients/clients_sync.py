from __future__ import annotations

import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.utils.phone import build_phone_match_keys, normalize_phone
from app.services.company_time import resolve_company_timezone

from .client import YClientsClient
from .service import build_yclients_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YClientsClientUpsertResult:
    client_id: int
    action: str


@dataclass(frozen=True)
class YClientsExistingClient:
    client_id: int
    note: str
    match_keys: set[str]


def normalize_phone_for_yclients(raw_phone: str) -> str | None:
    bundle = normalize_phone(raw_phone, default_region="RU")
    return bundle.canonical_e164 if bundle.is_valid else None


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _extract_client_id(payload: dict[str, Any] | list[Any]) -> int | None:
    for item in _extract_rows(payload):
        for key in ("id", "client_id"):
            value = item.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str.isdigit():
                return int(value_str)
    return None


def _extract_client_note(item: dict[str, Any]) -> str:
    for key in ("comment", "notes"):
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _merge_registration_note(existing_note: str | None, tag_line: str) -> str:
    note = (existing_note or "").strip()
    if not note:
        return tag_line
    if tag_line in note:
        return note
    return f"{note}\n{tag_line}"


async def _build_registration_tag_line(
    *,
    company_id: str,
    registration_completed_at: datetime | None = None,
) -> str:
    tz_context = await resolve_company_timezone(company_id)
    completed_at = registration_completed_at or datetime.now().astimezone()
    completed_at = completed_at.astimezone(ZoneInfo(tz_context.timezone_name))
    return f"Зарегистрирован из Telegram бота {completed_at.strftime('%d.%m.%Y в %H:%M')}"


def _extract_phone_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("phone", "phones", "tel"):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.append(raw)
            continue
        if isinstance(raw, list):
            for part in raw:
                if isinstance(part, dict):
                    phone = part.get("phone") or part.get("number")
                    if phone:
                        values.append(str(phone))
                elif isinstance(part, str):
                    values.append(part)
            continue
        if isinstance(raw, dict):
            phone = raw.get("phone") or raw.get("number")
            if phone:
                values.append(str(phone))
    return values


def _normalize_yclients_client_phones(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for raw_phone in _extract_phone_values(item):
        bundle = normalize_phone(raw_phone, default_region="RU")
        keys.update(build_phone_match_keys(bundle))
    return keys


def _extract_client_candidates(payload: dict[str, Any] | list[Any]) -> list[YClientsExistingClient]:
    candidates: list[YClientsExistingClient] = []
    for item in _extract_rows(payload):
        client_id = None
        for key in ("id", "client_id"):
            value = item.get(key)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str.isdigit():
                client_id = int(value_str)
                break
        if client_id is None:
            continue
        candidates.append(
            YClientsExistingClient(
                client_id=client_id,
                note=_extract_client_note(item),
                match_keys=_normalize_yclients_client_phones(item),
            )
        )
    return candidates


def _resolve_match(candidates: list[YClientsExistingClient], phone_keys: set[str]) -> tuple[YClientsExistingClient | None, str]:
    matched: list[YClientsExistingClient] = []
    for candidate in candidates:
        if candidate.match_keys & phone_keys:
            matched.append(candidate)
    if not matched:
        return None, "no_match"
    if len(matched) > 1:
        return None, "ambiguous_match"
    return matched[0], "exact_normalized_match"


async def _find_client_by_phone_with_client(
    *,
    client: YClientsClient,
    company_id: str,
    phone_keys: list[str],
    trace_id: str,
) -> tuple[YClientsExistingClient | None, str, list[str], int]:
    searched_variants: list[str] = []
    all_candidates: dict[int, YClientsExistingClient] = {}

    for variant in phone_keys:
        response = await client.request(
            "GET",
            f"/api/v1/clients/{company_id}",
            params={"phone": variant, "count": 50, "page": 1},
        )
        client.raise_for_status(response)
        searched_variants.append(variant)
        for candidate in _extract_client_candidates(response.body):
            all_candidates[candidate.client_id] = candidate

    resolved, reason = _resolve_match(list(all_candidates.values()), set(phone_keys))
    logger.info(
        "yclients_phone_match trace_id=%s reason=%s variants=%s candidates=%s resolved_client_id=%s",
        trace_id,
        reason,
        searched_variants,
        len(all_candidates),
        resolved.client_id if resolved else None,
    )
    return resolved, reason, searched_variants, len(all_candidates)


async def _create_client_with_client(*, client: YClientsClient, company_id: str, payload: dict[str, Any]) -> int:
    response = await client.request(
        "POST",
        f"/api/v1/clients/{company_id}",
        json_data=payload,
    )
    client.raise_for_status(response)
    created_client_id = _extract_client_id(response.body)
    if created_client_id is None:
        raise ValueError("YClients client create response does not contain client id")
    return int(created_client_id)


async def _update_client_with_client(*, client: YClientsClient, company_id: str, client_id: int, payload: dict[str, Any]) -> int:
    response = await client.request(
        "PUT",
        f"/api/v1/client/{company_id}/{client_id}",
        json_data=payload,
    )
    client.raise_for_status(response)
    return int(_extract_client_id(response.body) or client_id)


async def yclients_find_client_by_phone(*, company_id: str, phone: str) -> int | None:
    client, _ = await build_yclients_client()
    try:
        bundle = normalize_phone(phone)
        existing_client, _, _, _ = await _find_client_by_phone_with_client(
            client=client,
            company_id=company_id,
            phone_keys=sorted(build_phone_match_keys(bundle)),
            trace_id="phone_lookup",
        )
        return existing_client.client_id if existing_client else None
    finally:
        await client.close()


async def yclients_create_client(*, company_id: str, payload: dict[str, Any]) -> int:
    client, _ = await build_yclients_client()
    try:
        return await _create_client_with_client(client=client, company_id=company_id, payload=payload)
    finally:
        await client.close()


async def yclients_update_client(*, company_id: str, client_id: int, payload: dict[str, Any]) -> int:
    client, _ = await build_yclients_client()
    try:
        return await _update_client_with_client(client=client, company_id=company_id, client_id=client_id, payload=payload)
    finally:
        await client.close()


async def upsert_client_profile(*, name: str, phone: str, birthdate_iso: str) -> YClientsClientUpsertResult:
    client, company_id = await build_yclients_client()
    try:
        normalized_bundle = normalize_phone(phone, default_region="RU")
        normalized_phone = normalize_phone_for_yclients(phone)
        if not normalized_phone:
            raise ValueError("Cannot normalize phone for YClients sync")
        trace_id = datetime.now().strftime("%H%M%S%f")
        user_phone_keys = sorted(build_phone_match_keys(normalized_bundle))

        existing_client, match_reason, search_variants, candidates_found = await _find_client_by_phone_with_client(
            client=client,
            company_id=company_id,
            phone_keys=user_phone_keys,
            trace_id=trace_id,
        )
        if match_reason == "ambiguous_match":
            raise ValueError(f"Ambiguous YClients phone match trace_id={trace_id}")
        tag_line = await _build_registration_tag_line(company_id=company_id)
        merged_note = _merge_registration_note(existing_client.note if existing_client else "", tag_line)
        payload = {
            "name": name,
            "phone": normalized_phone,
            "birth_date": birthdate_iso,
            "bdate": birthdate_iso,
            "comment": merged_note,
        }
        if existing_client is not None:
            resolved_client_id = await _update_client_with_client(
                client=client,
                company_id=company_id,
                client_id=existing_client.client_id,
                payload=payload,
            )
            logger.info(
                "yclients_client_sync_success action=updated company_id=%s client_id=%s trace_id=%s match_reason=%s canonical_e164=%s match_keys=%s search_variants=%s candidates_found=%s",
                company_id,
                resolved_client_id,
                trace_id,
                match_reason,
                normalized_bundle.canonical_e164,
                user_phone_keys,
                search_variants,
                candidates_found,
            )
            return YClientsClientUpsertResult(client_id=resolved_client_id, action="updated")

        created_client_id = await _create_client_with_client(client=client, company_id=company_id, payload=payload)
        logger.info(
            "yclients_client_sync_success action=created company_id=%s client_id=%s trace_id=%s match_reason=created_new_client canonical_e164=%s match_keys=%s search_variants=%s candidates_found=%s",
            company_id,
            created_client_id,
            trace_id,
            normalized_bundle.canonical_e164,
            user_phone_keys,
            search_variants,
            candidates_found,
        )
        return YClientsClientUpsertResult(client_id=created_client_id, action="created")
    finally:
        await client.close()
