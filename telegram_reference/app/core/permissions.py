from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from functools import wraps
from typing import Any

from aiogram.types import CallbackQuery, Message

from app.repositories.staff_roles import get_role

DEVELOPER_TG_ID = 378881880
ROLE_DEVELOPER = "developer"
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ALL_STAFF_ROLES = {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}


async def resolve_role(tg_id: int) -> str | None:
    if tg_id == DEVELOPER_TG_ID:
        return ROLE_DEVELOPER
    return await get_role(tg_id)


async def is_developer(tg_id: int) -> bool:
    return await resolve_role(tg_id) == ROLE_DEVELOPER


async def is_admin(tg_id: int) -> bool:
    role = await resolve_role(tg_id)
    return role in {ROLE_DEVELOPER, ROLE_ADMIN}


async def is_manager(tg_id: int) -> bool:
    role = await resolve_role(tg_id)
    return role in {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}


async def has_any_role(tg_id: int, roles: Iterable[str]) -> bool:
    role = await resolve_role(tg_id)
    return role in set(roles)


def require_roles(*roles: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    allowed = set(roles)

    def decorator(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(handler)
        async def wrapped(event: Message | CallbackQuery, *args: Any, **kwargs: Any) -> Any:
            user_id = event.from_user.id
            if not await has_any_role(user_id, allowed):
                text = "⛔️ Недостаточно прав."
                if isinstance(event, CallbackQuery):
                    if event.message:
                        await event.message.answer(text)
                    await event.answer()
                    return None
                await event.answer(text)
                return None
            return await handler(event, *args, **kwargs)

        return wrapped

    return decorator
