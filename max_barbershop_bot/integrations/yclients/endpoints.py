"""Transport-neutral YClients endpoint wrappers."""

from __future__ import annotations

from typing import Any

from .client import YClientsClient
from .dto import YClientsBookingRecord, YClientsCancelBookingResult
from .exceptions import YClientsValidationError
from .utils import (
    MAX_BOOKING_COMMENT_MARKER,
    append_booking_marker,
    extract_first_record,
    normalize_id,
    normalize_phone,
    safe_str,
)


async def get_company(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    """Return a company/location card. Useful for safe health checks."""

    return await client.get(f"/api/v1/company/{company_id}")


async def get_service_categories(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    """Return service categories for a company/location."""

    return await client.get(f"/api/v1/service_categories/{company_id}")


async def list_service_categories(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    """Alias kept for parity with the reference integration layer."""

    return await get_service_categories(client, company_id=company_id)


async def get_services(
    client: YClientsClient,
    *,
    company_id: str,
    category_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Return company services, optionally filtered by category."""

    params = {"category_id": category_id} if category_id else None
    return await client.get(f"/api/v1/company/{company_id}/services", params=params)


async def get_service_details(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
) -> dict[str, Any] | list[Any]:
    """Return a service by id when it is present in the company service list."""

    payload = await get_services(client, company_id=company_id)
    for item in _extract_rows(payload):
        if safe_str(item.get("id") or item.get("service_id")) == safe_str(service_id):
            return item
    return {}


async def get_staff(client: YClientsClient, *, company_id: str) -> dict[str, Any] | list[Any]:
    """Return company staff/masters."""

    return await client.get(f"/api/v1/company/{company_id}/staff")


async def list_staff(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Return staff, optionally filtered by a service id."""

    params = {"service_id": service_id} if service_id else None
    return await client.get(f"/api/v1/company/{company_id}/staff", params=params)


async def get_staff_by_service(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
) -> dict[str, Any] | list[Any]:
    """Return staff available for a service using the reference query parameter."""

    return await client.get(
        f"/api/v1/company/{company_id}/staff",
        params={"service_ids[]": service_id},
    )


async def get_staff_details(
    client: YClientsClient,
    *,
    company_id: str,
    staff_id: str,
) -> dict[str, Any] | list[Any]:
    """Return a staff member by id when present in the company staff list."""

    payload = await get_staff(client, company_id=company_id)
    for item in _extract_rows(payload):
        if safe_str(item.get("id") or item.get("staff_id")) == safe_str(staff_id):
            return item
    return {}


async def get_available_slots(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
    date: str,
    staff_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Return free booking times for a service/date/staff combination."""

    normalized_staff_id = staff_id or "0"
    params = {"service_ids[]": service_id}
    return await client.get(f"/api/v1/book_times/{company_id}/{normalized_staff_id}/{date}", params=params)


async def get_available_dates(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
    date_from: str,
    date_to: str,
    staff_id: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Return candidate booking dates for a service/staff/date range."""

    params = {
        "staff_id": staff_id or "0",
        "service_ids[]": service_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    return await client.get(f"/api/v1/book_dates/{company_id}", params=params)


async def get_slots_by_service_staff_date(
    client: YClientsClient,
    *,
    company_id: str,
    service_id: str,
    staff_id: str,
    date: str,
) -> dict[str, Any] | list[Any]:
    """Explicit alias for slot lookup by service, staff and date."""

    return await get_available_slots(
        client,
        company_id=company_id,
        service_id=service_id,
        staff_id=staff_id,
        date=date,
    )


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
    marker: str = MAX_BOOKING_COMMENT_MARKER,
) -> YClientsBookingRecord:
    """Create a YClients booking/record and return the created record data."""

    normalized_datetime = datetime_iso.replace("T", " ") if "T" in datetime_iso else datetime_iso
    appointment: dict[str, Any] = {
        "services": [normalize_id(service_id)],
        "datetime": normalized_datetime,
    }
    if staff_id:
        normalized_staff_id = normalize_id(staff_id)
        appointment["id"] = normalized_staff_id
        appointment["staff_id"] = normalized_staff_id

    payload = {
        "phone": normalize_phone(phone),
        "fullname": fullname,
        "email": email,
        "comment": append_booking_marker(comment, marker=marker),
        "appointments": [appointment],
    }
    response = await client.post(f"/api/v1/book_record/{company_id}", json_data=payload)
    record = extract_first_record(response) or {}
    record_id = safe_str(
        record.get("record_id")
        or record.get("id")
        or record.get("booking_id")
        or record.get("visit_id")
    )
    if not record_id:
        raise YClientsValidationError("YClients create booking response does not contain record id")

    return YClientsBookingRecord(
        record_id=record_id,
        datetime=safe_str(record.get("datetime") or datetime_iso) or None,
        staff_name=safe_str(record.get("staff_name") or record.get("staff")) or None,
        service_name=safe_str(record.get("service_name") or record.get("service")) or None,
        raw_payload=response,
    )


async def create_booking(
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
) -> YClientsBookingRecord:
    """Alias for creating a YClients record with the MAX origin marker."""

    return await create_booking_or_visit(
        client,
        company_id=company_id,
        service_id=service_id,
        datetime_iso=datetime_iso,
        phone=phone,
        fullname=fullname,
        staff_id=staff_id,
        email=email,
        comment=comment,
    )


async def cancel_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> YClientsCancelBookingResult:
    """Cancel a YClients record via DELETE /api/v1/record/{company_id}/{record_id}."""

    response = await client.delete(f"/api/v1/record/{company_id}/{record_id}")
    record = extract_first_record(response) or {}
    normalized_record_id = safe_str(
        record.get("record_id")
        or record.get("id")
        or record.get("booking_id")
        or record.get("visit_id")
        or record_id
    )
    status = safe_str(record.get("status") or record.get("record_status") or record.get("state")) or None
    return YClientsCancelBookingResult(record_id=normalized_record_id, status=status, raw_payload=response)


async def list_clients(
    client: YClientsClient,
    *,
    company_id: str,
    page: int = 1,
    count: int = 200,
) -> dict[str, Any] | list[Any]:
    """Return clients from YClients."""

    return await client.get(f"/api/v1/clients/{company_id}", params={"page": page, "count": count})


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
    """Search YClients clients by generic query, phone or name."""

    params: dict[str, Any] = {"page": page, "count": count}
    if by_phone:
        params["phone"] = normalize_phone(query)
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
    """Return a YClients client card/profile."""

    return await client.get(f"/api/v1/client/{company_id}/{client_id}")


async def create_client(
    client: YClientsClient,
    *,
    company_id: str,
    name: str,
    phone: str,
    email: str | None = None,
    comment: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Create a YClients client using fields present in the reference implementation."""

    payload: dict[str, Any] = {
        "name": name,
        "phone": normalize_phone(phone),
    }
    if email:
        payload["email"] = email
    if comment:
        payload["comment"] = comment
    if extra_fields:
        payload.update(extra_fields)
    return await client.post(f"/api/v1/clients/{company_id}", json_data=payload)


async def update_client(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | list[Any]:
    """Update a YClients client card/profile."""

    normalized_payload = dict(payload)
    if "phone" in normalized_payload and normalized_payload["phone"]:
        normalized_payload["phone"] = normalize_phone(str(normalized_payload["phone"]))
    return await client.put(f"/api/v1/client/{company_id}/{client_id}", json_data=normalized_payload)


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
    """Return records/bookings filtered by client, phone and/or date range."""

    params: dict[str, Any] = {}
    if client_id:
        params["client_id"] = client_id
    if phone:
        params["phone"] = normalize_phone(phone)
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if page is not None:
        params["page"] = page
    if count is not None:
        params["count"] = count
    return await client.get(f"/api/v1/records/{company_id}", params=params or None)


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
    """Return visit/record history for a YClients client from YClients itself."""

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
    """Return a YClients record/booking by id."""

    return await client.get(f"/api/v1/record/{company_id}/{record_id}")


async def update_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    payload: dict[str, Any],
) -> dict[str, Any] | list[Any]:
    """Update a YClients record/booking."""

    return await client.put(f"/api/v1/record/{company_id}/{record_id}", json_data=payload)


async def update_booking_comment(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
    comment: str,
) -> dict[str, Any] | list[Any]:
    """Update only a record comment."""

    return await update_booking(client, company_id=company_id, record_id=record_id, payload={"comment": comment})


def build_reschedule_payload(
    *,
    record_id: str,
    services: list[str],
    client_data: dict[str, Any],
    seance_length: int,
    datetime_iso: str,
    staff_id: str,
) -> dict[str, Any]:
    """Build the form-compatible YClients record reschedule payload."""

    normalized_client = dict(client_data) if isinstance(client_data, dict) else {}
    normalized_client_id = normalized_client.get("id") or normalized_client.get("client_id")
    payload: dict[str, Any] = {
        "id": normalize_id(record_id),
        "staff_id": normalize_id(staff_id),
        "services": [normalize_id(service_id) for service_id in services],
        "client": normalized_client,
        "client_id": normalize_id(str(normalized_client_id)) if normalized_client_id is not None else None,
        "seance_length": seance_length,
        "datetime": datetime_iso.replace("T", " ") if "T" in datetime_iso else datetime_iso,
    }
    if payload.get("client_id") is None:
        payload.pop("client_id", None)
    return payload


def build_reschedule_form_data(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """Convert a reschedule payload to YClients form field tuples."""

    form_data: list[tuple[str, Any]] = []
    for key in ("id", "staff_id", "client_id", "seance_length", "datetime"):
        value = payload.get(key)
        if value is not None:
            form_data.append((key, value))

    services = payload.get("services")
    if isinstance(services, list):
        for service_id in services:
            form_data.append(("services[]", service_id))

    client_data = payload.get("client")
    if isinstance(client_data, dict):
        for field in ("id", "name", "phone", "email", "sex"):
            value = client_data.get(field)
            if value not in (None, ""):
                form_data.append((f"client[{field}]", value))
    return form_data


def get_reschedule_payload_missing_fields(payload: dict[str, Any]) -> list[str]:
    """Return required reschedule fields missing from a payload."""

    missing: list[str] = []
    if not payload.get("id"):
        missing.append("id")
    if not payload.get("staff_id"):
        missing.append("staff_id")
    services = payload.get("services")
    if not isinstance(services, list) or not services:
        missing.append("services")
    client_data = payload.get("client")
    if not isinstance(client_data, dict) or not client_data:
        missing.append("client")
    client_id = payload.get("client_id")
    if not client_id and (not isinstance(client_data, dict) or not (client_data.get("id") or client_data.get("client_id"))):
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
    """Reschedule a YClients record/booking using form data."""

    payload = build_reschedule_payload(
        record_id=record_id,
        services=services,
        client_data=client_data,
        seance_length=seance_length,
        datetime_iso=datetime_iso,
        staff_id=staff_id,
    )
    return await client.put(f"/api/v1/record/{company_id}/{record_id}", form_data=build_reschedule_form_data(payload))


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
    """Return company bookings/records in a date range."""

    params: dict[str, Any] = {"start_date": date_from, "end_date": date_to}
    if staff_id:
        params["staff_id"] = staff_id
    if status:
        params["status"] = status
    if page is not None:
        params["page"] = page
    if count is not None:
        params["count"] = count
    return await client.get(f"/api/v1/records/{company_id}", params=params)


async def get_future_bookings(
    client: YClientsClient,
    *,
    company_id: str,
    start_date: str,
    end_date: str | None = None,
    client_id: str | None = None,
    phone: str | None = None,
    page: int | None = None,
    count: int | None = None,
) -> dict[str, Any] | list[Any]:
    """Return future records using YClients records endpoint filters."""

    return await list_user_bookings(
        client,
        company_id=company_id,
        client_id=client_id,
        phone=phone,
        start_date=start_date,
        end_date=end_date,
        page=page,
        count=count,
    )


async def cancel_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> YClientsBookingRecord:
    """Cancel/delete a YClients booking record."""

    response = await client.delete(f"/api/v1/record/{company_id}/{record_id}")
    record = extract_first_record(response) or {}
    return YClientsBookingRecord(
        record_id=safe_str(record.get("record_id") or record.get("id") or record_id),
        datetime=safe_str(record.get("datetime")) or None,
        staff_name=safe_str(record.get("staff_name") or record.get("staff")) or None,
        service_name=safe_str(record.get("service_name") or record.get("service")) or None,
        raw_payload=response,
    )


async def confirm_booking(
    client: YClientsClient,
    *,
    company_id: str,
    record_id: str,
) -> dict[str, Any] | list[Any]:
    """Mark a booking as confirmed/arrived using the reference attendance value."""

    return await client.put(f"/api/v1/record/{company_id}/{record_id}", json_data={"attendance": 2})


async def get_loyalty_info(
    client: YClientsClient,
    *,
    company_id: str,
    client_id: str,
) -> dict[str, Any] | list[Any]:
    """Return loyalty information for a YClients client."""

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
    """Apply loyalty accrual/redemption to a YClients record."""

    payload: dict[str, Any] = {"action": action_type, "value": value}
    if comment:
        payload["comment"] = comment
    return await client.post(f"/api/v1/loyalty/{company_id}/record/{record_id}", json_data=payload)


def _extract_rows(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    if isinstance(payload, dict):
        return [payload]
    return []
