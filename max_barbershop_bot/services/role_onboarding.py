"""Role notification helpers for MAX staff management."""

from __future__ import annotations

import logging
from typing import Protocol

from max_barbershop_bot.repositories.users import User

logger = logging.getLogger(__name__)


class _RoleNotificationSender(Protocol):
    async def send_to_user(self, user_id: int, text: str, **kwargs: object) -> object | None: ...

    async def send_to_chat(self, chat_id: int, text: str, **kwargs: object) -> object | None: ...


def build_role_assigned_text(role: str) -> str:
    """Build role-assigned notification text."""

    return f"Поздравляем, вам выдали роль {role} 🎉"


def build_role_removed_text(role: str | None = None) -> str:
    """Build role-removed notification text."""

    return "У вас изменились права доступа в боте ℹ️"


async def notify_role_assigned(sender: _RoleNotificationSender, user: User, role: str) -> bool:
    """Notify a user that a staff role was assigned without failing the caller."""

    return await _safe_notify(sender, user, build_role_assigned_text(role), "назначении роли")


async def notify_role_removed(
    sender: _RoleNotificationSender,
    user: User,
    role: str | None = None,
) -> bool:
    """Notify a user that staff access rights were changed without failing the caller."""

    return await _safe_notify(sender, user, build_role_removed_text(role), "изменении роли")


async def _safe_notify(
    sender: _RoleNotificationSender,
    user: User,
    text: str,
    action_label: str,
) -> bool:
    user_id = _int_from_string(user.max_user_id or user.platform_user_id)
    chat_id = _int_from_string(user.chat_id)
    try:
        if user_id is not None:
            await sender.send_to_user(user_id, text)
            return True
        if chat_id is not None:
            await sender.send_to_chat(chat_id, text)
            return True
        logger.warning(
            "Cannot send role notification about %s: platform_user_id=%s has no numeric destination",
            action_label,
            user.platform_user_id,
        )
        return False
    except Exception as error:
        logger.warning(
            "Role notification about %s failed safely for platform_user_id=%s: %s: %s",
            action_label,
            user.platform_user_id,
            type(error).__name__,
            error,
        )
        return False


def _int_from_string(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
