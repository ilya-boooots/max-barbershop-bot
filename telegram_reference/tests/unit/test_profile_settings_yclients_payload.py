from __future__ import annotations

import pytest

from app.handlers.master_photos_settings import build_yclients_client_update_payload, _format_yclients_profile_update_error
from app.integrations.yclients.errors import YClientsBadRequestError


pytestmark = pytest.mark.unit


def test_build_payload_for_name_update_keeps_existing_phone_and_optional_fields():
    payload = build_yclients_client_update_payload(
        local_user={"name": "Локальное имя", "phone": "+79990001122"},
        yclients_client_row={
            "name": "Старое имя",
            "phone": "+79991234567",
            "birth_date": "1990-01-01",
            "email": "client@example.com",
            "comment": "VIP",
        },
        new_name="Новое имя",
    )
    assert payload["name"] == "Новое имя"
    assert payload["phone"] == "+79991234567"
    assert payload["birth_date"] == "1990-01-01"
    assert payload["bdate"] == "1990-01-01"
    assert payload["email"] == "client@example.com"
    assert payload["comment"] == "VIP"


def test_build_payload_for_phone_update_uses_local_name_fallback():
    payload = build_yclients_client_update_payload(
        local_user={"name": "Имя из БД", "phone": "+79990001122", "birth_date": "1987-05-10"},
        yclients_client_row={"email": "a@b.c"},
        new_phone="+79997776655",
    )
    assert payload["name"] == "Имя из БД"
    assert payload["phone"] == "+79997776655"
    assert payload["birth_date"] == "1987-05-10"
    assert payload["bdate"] == "1987-05-10"


def test_build_payload_requires_phone():
    with pytest.raises(ValueError, match="missing_required_phone"):
        build_yclients_client_update_payload(
            local_user={"name": "Только имя"},
            yclients_client_row={"name": "Клиент без телефона"},
            new_name="Новое имя",
        )


def test_yclients_error_formatter_returns_human_message_without_raw_json():
    error = YClientsBadRequestError(
        "Validation failed",
        response_snippet='{"success":false,"meta":{"message":"Validation failed"},"errors":{"phone":"Не передан обязательный параметр phone"}}',
        status_code=422,
    )
    user_message, safe_summary = _format_yclients_profile_update_error(action="name_update", exc=error)
    assert "не передан телефон" in user_message
    assert "{" not in user_message
    assert safe_summary == "status=422 validation=missing_phone"


def test_phone_update_error_formatter_handles_duplicate_cleanly():
    error = YClientsBadRequestError(
        "Validation failed",
        response_snippet='{"success":false,"meta":{"message":"client with this phone already exists"}}',
        status_code=422,
    )
    user_message, safe_summary = _format_yclients_profile_update_error(action="phone_update", exc=error)
    assert "уже используется" in user_message
    assert "{" not in user_message
    assert safe_summary == "status=422 duplicate_phone_conflict"
