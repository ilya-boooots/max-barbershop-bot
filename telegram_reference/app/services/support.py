from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.integrations.yclients import YClientsCredentialsError, get_yclients_credentials
from app.repositories.support_settings import (
    DEFAULT_SUPPORT_DESCRIPTION,
    DEFAULT_SUPPORT_USERNAME,
    SupportSettings,
    get_support_settings,
)
from app.ui.buttons import BACK, HOME
from app.ui.callbacks import NAV_BACK, NAV_HOME


@dataclass(frozen=True)
class EffectiveSupportSettings:
    description: str
    username: str


async def resolve_support_company_id() -> str:
    try:
        credentials, _ = await get_yclients_credentials()
    except YClientsCredentialsError:
        return "default"
    return str(credentials.company_id).strip() or "default"


async def resolve_support_settings(company_id: str | None = None) -> tuple[str, EffectiveSupportSettings, SupportSettings | None]:
    effective_company_id = company_id or await resolve_support_company_id()
    stored = await get_support_settings(effective_company_id)
    if stored:
        return (
            effective_company_id,
            EffectiveSupportSettings(description=stored.support_description, username=stored.support_username),
            stored,
        )

    return (
        effective_company_id,
        EffectiveSupportSettings(description=DEFAULT_SUPPORT_DESCRIPTION, username=DEFAULT_SUPPORT_USERNAME),
        None,
    )


def build_support_url(username: str) -> str:
    cleaned = (username or "").strip().lstrip("@")
    return f"https://t.me/{cleaned}"


def render_support_message(description: str) -> str:
    normalized = (description or "").strip() or DEFAULT_SUPPORT_DESCRIPTION
    return f"🆘 Поддержка\n\n{normalized}"


def support_screen_kb(*, username: str, include_home: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🆘 Написать в поддержку", url=build_support_url(username))],
        [InlineKeyboardButton(text=BACK, callback_data=NAV_BACK)],
    ]
    if include_home:
        rows.append([InlineKeyboardButton(text=HOME, callback_data=NAV_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
