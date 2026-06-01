from __future__ import annotations

from app.repositories.users import upsert_registration_profile, upsert_telegram_user


async def create_user(
    *,
    tg_id: int,
    name: str = "Пользователь",
    phone: str = "+79990000000",
    birthdate_iso: str = "1990-01-01",
    yclients_client_id: int | None = None,
) -> int:
    await upsert_telegram_user(tg_id=tg_id, username=f"u{tg_id}", name=name)
    await upsert_registration_profile(
        tg_user_id=tg_id,
        name=name,
        birthdate_iso=birthdate_iso,
        phone=phone,
        username=f"u{tg_id}",
        yclients_client_id=yclients_client_id,
        phone_e164=phone,
    )
    return tg_id
