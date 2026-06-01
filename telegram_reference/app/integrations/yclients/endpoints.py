from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import YClientsClient


@dataclass(frozen=True)
class CreatedBookingResult:
    record_id: str
    datetime: str | None
    staff_name: str | None
    service_name: str | None
    raw_payload: dict[str, Any] | list[Any]


@dataclass(frozen=True)
class CancelBookingResult:
    record_id: str
    status: str | None
    raw_payload: dict[str, Any] | list[Any]


@dataclass(frozen=True)
class AdminBookingCapabilities:
    can_cancel: bool = False
    can_comment: bool = False
    can_reschedule: bool = False
    can_status_update: bool = False


ADMIN_BOOKING_CAPABILITIES = AdminBookingCapabilities(
    can_cancel=True,
    can_comment=False,
    can_reschedule=False,
    can_status_update=False,
)

LOYALTY_CAPABILITIES = {
    "enabled": True,
    "accrue": True,
    "redeem": True,
    "discount": True,
}


async def get_company(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    # Safe, read-only endpoint used by health-check.
    return await client.get(f"/api/v1/company/{company_id}")


async def list_service_categories(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    return await client.get(f"/api/v1/service_categories/{company_id}")


async def get_services(
    client: YClientsClient,
    *,
    company_id: str,
    category_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    params = {"category_id": category_id} if category_id else None
    return await client.get(f"/api/v1/company/{company_id}/services", params=params)


async def get_staff(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    return await client.get(f"/api/v1/company/{company_id}/staff")


async def list_staff(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    params = {"service_id": service_id} if service_id else None
    return await client.get(f"/api/v1/company/{company_id}/staff", params=params)


async def get_available_slots(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
    date: str,
    staff_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Return free booking times for service/date/staff.

    API path based on YClients REST API docs: /api/v1/book_times/{company_id}/{staff_id}/{date}
    """
    normalized_staff_id = staff_id or "0"
    params = {"service_ids[]": service_id}
    return await client.get(f"/api/v1/book_times/{company_id}/{normalized_staff_id}/{date}", params=params)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_record(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


async def create_booking_or_visit(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
    datetime_iso: str,
    phone: str,
    fullname: str,
    staff_id: str | None = None,
    email: str = "",
    comment: str = "",
) -> CreatedBookingResult:
    def _normalize_id(raw: str) -> int | str:
        return int(raw) if raw.isdigit() else raw

    normalized_datetime = datetime_iso.replace("T", " ") if "T" in datetime_iso else datetime_iso
    appointment: dict[str, Any] = {
        "services": [_normalize_id(service_id)],
        "datetime": normalized_datetime,
    }
    if staff_id:
        normalized_staff_id = _normalize_id(staff_id)
        appointment["id"] = normalized_staff_id
        appointment["staff_id"] = normalized_staff_id

    payload = {
        "phone": phone,
        "fullname": fullname,
        "email": email,
        "comment": comment,
        "appointments": [appointment],
    }
    response = await client.post(f"/api/v1/book_record/{company_id}", json_data=payload)

    record = _extract_record(response) or {}
    record_id = _safe_str(
        record.get("record_id")
        or record.get("id")
        or record.get("booking_id")
        or record.get("visit_id")
    )
    if not record_id:
        raise ValueError("YClients create booking response does not contain record id")

    return CreatedBookingResult(
        record_id=record_id,
        datetime=_safe_str(record.get("datetime") or datetime_iso) or None,
        staff_name=_safe_str(record.get("staff_name") or record.get("staff")) or None,
        service_name=_safe_str(record.get("service_name") or record.get("service")) or None,
        raw_payload=response,
    )


async def list_user_bookings(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str | None = None,
    phone: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int | None = None,
    count: int | None = None,
) -> dict[str, Any] | list[Any]:
    params: dict[str, Any] = {}
    if client_id:
        params["client_id"] = client_id
    if phone:
        params["phone"] = phone
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if page is not None:
        params["page"] = page
    if count is not None:
        params["count"] = count
    return await client.get(f"/api/v1/records/{company_id}", params=params or None)


async def list_clients(
    client: YClientsClient,
    *,
    company_id: str,
    page: int = 1,
    count: int = 200,
) -> dict[str, Any] | list[Any]:
    params: dict[str, Any] = {
        "page": page,
        "count": count,
    }
    return await client.get(f"/api/v1/clients/{company_id}", params=params)


async def search_clients(
    client: YClientsClient,
    *,
    company_id: str,
    query: str,
    page: int = 1,
    count: int = 10,
    by_phone: bool = False,
    by_name: bool = False,
) -> dict[str, Any] | list[Any]:
    params: dict[str, Any] = {
        "page": page,
        "count": count,
    }
    if by_phone:
        params["phone"] = query
    elif by_name:
        params["name"] = query
    else:
        params["query"] = query
    return await client.get(f"/api/v1/clients/{company_id}", params=params)


async def get_client_details(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str,
) -> dict[str, Any] | list[Any]:
    return await client.get(f"/api/v1/client/{company_id}/{client_id}")


async def list_client_visits(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str,
    page: int = 1,
    count: int = 5,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any] | list[Any]:
    return await list_user_bookings(
        client,
        company_id=company_id,
        client_id=client_id,
        page=page,
        count=count,
        start_date=start_date,
        end_date=end_date,
    )


async def get_booking_details(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> dict[str, Any] | list[Any]:
    return await client.get(f"/api/v1/record/{company_id}/{record_id}")


async def update_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | list[Any]:
    return await client.put(f"/api/v1/record/{company_id}/{record_id}", json_data=payload)


async def update_booking_comment(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    comment: str,
) -> dict[str, Any] | list[Any]:
    payload = {"comment": comment}
    return await client.put(f"/api/v1/record/{company_id}/{record_id}", json_data=payload)


def build_reschedule_payload(
    *,
    record_id: str,
    services: list[str],
    client_data: dict[str, Any],
    seance_length: int,
    datetime_iso: str,
    staff_id: str,
) -> dict[str, Any]:
    def _normalize_id(raw: str) -> int | str:
        return int(raw) if raw.isdigit() else raw

    normalized_record_id = _normalize_id(record_id)
    normalized_client = dict(client_data) if isinstance(client_data, dict) else {}
    normalized_client_id = normalized_client.get("id") or normalized_client.get("client_id")
    payload: dict[str, Any] = {
        "id": normalized_record_id,
        "staff_id": _normalize_id(staff_id),
        "services": [_normalize_id(service_id) for service_id in services],
        "client": normalized_client,
        "client_id": _normalize_id(str(normalized_client_id)) if normalized_client_id is not None else None,
        "seance_length": seance_length,
        "datetime": datetime_iso.replace("T", " ") if "T" in datetime_iso else datetime_iso,
    }
    if payload.get("client_id") is None:
        payload.pop("client_id", None)
    return payload


def build_reschedule_form_data(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    form_data: list[tuple[str, Any]] = []
    for key in ("id", "staff_id", "client_id", "seance_length", "datetime"):
        value = payload.get(key)
        if value is not None:
            form_data.append((key, value))

    services = payload.get("services")
    if isinstance(services, list):
        for service_id in services:
            form_data.append(("services[]", service_id))

    client = payload.get("client")
    if isinstance(client, dict):
        for field in ("id", "name", "phone", "email", "sex"):
            value = client.get(field)
            if value not in (None, ""):
                form_data.append((f"client[{field}]", value))

    return form_data


def get_reschedule_payload_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not payload.get("id"):
        missing.append("id")
    if not payload.get("staff_id"):
        missing.append("staff_id")
    services = payload.get("services")
    if not isinstance(services, list) or not services:
        missing.append("services")
    client = payload.get("client")
    if not isinstance(client, dict) or not client:
        missing.append("client")
    client_id = payload.get("client_id")
    if not client_id and (not isinstance(client, dict) or not (client.get("id") or client.get("client_id"))):
        missing.append("client_id")
    if not payload.get("seance_length"):
        missing.append("seance_length")
    if not payload.get("datetime"):
        missing.append("datetime")
    return missing


async def reschedule_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    services: list[str],
    client_data: dict[str, Any],
    seance_length: int,
    datetime_iso: str,
    staff_id: str,
) -> dict[str, Any] | list[Any]:
    payload = build_reschedule_payload(
        record_id=record_id,
        services=services,
        client_data=client_data,
        seance_length=seance_length,
        datetime_iso=datetime_iso,
        staff_id=staff_id,
    )
    form_data = build_reschedule_form_data(payload)
    return await client.put(f"/api/v1/record/{company_id}/{record_id}", form_data=form_data)


async def list_bookings_by_date_range(
    client: YClientsClient,
    *,
    company_id: str,
    date_from: str,
    date_to: str,
    staff_id: str | None = None,
    status: str | None = None,
    page: int | None = None,
    count: int | None = None,
) -> dict[str, Any] | list[Any]:
    params: dict[str, Any] = {
        "start_date": date_from,
        "end_date": date_to,
    }
    if staff_id:
        params["staff_id"] = staff_id
    if status:
        params["status"] = status
    if page is not None:
        params["page"] = page
    if count is not None:
        params["count"] = count
    return await client.get(f"/api/v1/records/{company_id}", params=params)


async def get_booking_details_admin(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> dict[str, Any] | list[Any]:
    return await get_booking_details(client, company_id=company_id, record_id=record_id)


async def cancel_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> CancelBookingResult:
    """Cancel booking via YClients record endpoint."""
    response = await client.delete(f"/api/v1/record/{company_id}/{record_id}")
    record = _extract_record(response) or {}
    normalized_record_id = _safe_str(
        record.get("record_id")
        or record.get("id")
        or record.get("booking_id")
        or record.get("visit_id")
        or record_id
    )
    status = _safe_str(record.get("status") or record.get("record_status") or record.get("state")) or None
    return CancelBookingResult(record_id=normalized_record_id, status=status, raw_payload=response)


async def admin_cancel_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> CancelBookingResult:
    return await cancel_booking(client, company_id=company_id, record_id=record_id)


async def confirm_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> dict[str, Any] | list[Any]:
    return await client.put(f"/api/v1/record/{company_id}/{record_id}", json_data={"attendance": 2})


async def get_loyalty_info(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str,
) -> dict[str, Any] | list[Any]:
    return await client.get(f"/api/v1/loyalty/{company_id}/client/{client_id}")


async def apply_loyalty_to_visit(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    action_type: str,
    value: str,
    comment: str | None = None,
) -> dict[str, Any] | list[Any]:
    payload: dict[str, Any] = {
        "action": action_type,
        "value": value,
    }
    if comment:
        payload["comment"] = comment
    return await client.post(f"/api/v1/loyalty/{company_id}/record/{record_id}", json_data=payload)
