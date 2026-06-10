"""One-time broadcast helpers for the MAX bot."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from max_barbershop_bot.max_api.sender import MaxMessageSender, MaxSendResult
from max_barbershop_bot.repositories.users import PLATFORM_MAX, UserProfileUpdate, UsersRepository
from max_barbershop_bot.services.notifications import (
    NotificationDeliveryResult,
    save_delivery_result,
)

logger = logging.getLogger(__name__)

BROADCAST_NOTIFICATION_TYPE = "broadcast_one_time"
BROADCAST_ALL_USERS_AUDIENCE = "all_users"
BROADCAST_ALL_USERS_LABEL = "👥 Все пользователи"
MAX_BROADCAST_TEXT_LENGTH = 4000
DEFAULT_SEND_DELAY_SECONDS = 0.1
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_PAUSE_SECONDS = 1.0


@dataclass(frozen=True)
class BroadcastAudience:
    """One supported one-time broadcast audience."""

    key: str
    label: str


@dataclass(frozen=True)
class BroadcastRecipient:
    """Recipient address for one MAX broadcast delivery."""

    platform_user_id: str
    max_user_id: str | None = None
    chat_id: str | None = None
    display_name: str | None = None

    @property
    def recipient_type(self) -> str:
        return "chat" if self.chat_id else "user"

    @property
    def recipient_id(self) -> str:
        return self.chat_id or self.max_user_id or self.platform_user_id


@dataclass(frozen=True)
class BroadcastSendReport:
    """Aggregated one-time broadcast send result."""

    total: int
    sent: int
    failed: int
    blocked: int
    broadcast_id: str | None = None


@dataclass(frozen=True)
class BroadcastTextValidation:
    """Result of broadcast text validation."""

    ok: bool
    text: str = ""
    error: str | None = None


ALL_USERS_AUDIENCE = BroadcastAudience(
    key=BROADCAST_ALL_USERS_AUDIENCE,
    label=BROADCAST_ALL_USERS_LABEL,
)


def validate_broadcast_text(text: str | None) -> BroadcastTextValidation:
    """Trim and validate one-time broadcast text."""

    clean = (text or "").strip()
    if not clean:
        return BroadcastTextValidation(ok=False, error="Текст рассылки не может быть пустым 🙏")
    if len(clean) > MAX_BROADCAST_TEXT_LENGTH:
        return BroadcastTextValidation(
            ok=False,
            error=f"Текст слишком длинный 🙏 Максимум {MAX_BROADCAST_TEXT_LENGTH} символов.",
        )
    return BroadcastTextValidation(ok=True, text=clean)


def build_broadcast_preview(text: str) -> str:
    """Build the preview screen text."""

    return f"Предпросмотр рассылки 👀\n\n{text}"


def build_broadcast_confirm_text(*, audience_label: str, recipient_count: int, text: str) -> str:
    """Build the final confirmation screen text."""

    return (
        "Проверьте рассылку 📣\n\n"
        f"Аудитория: {audience_label}\n"
        f"Получателей: {recipient_count}\n\n"
        f"Текст:\n{text}"
    )


def format_broadcast_report(report: BroadcastSendReport) -> str:
    """Build final one-time broadcast report text."""

    return (
        "Рассылка завершена ✅\n\n"
        f"Получателей: {report.total}\n"
        f"Отправлено: {report.sent}\n"
        f"Ошибок: {report.failed}\n"
        f"Заблокировали/остановили бота: {report.blocked}"
    )


def get_all_registered_recipients(users_repository: UsersRepository) -> list[BroadcastRecipient]:
    """Return local MAX users who can receive a broadcast."""

    return [
        BroadcastRecipient(
            platform_user_id=user.platform_user_id,
            max_user_id=user.max_user_id,
            chat_id=user.chat_id,
            display_name=user.display_name or user.first_name,
        )
        for user in users_repository.list_broadcast_recipients(
            platform=PLATFORM_MAX,
            notifications_enabled=True,
        )
    ]


async def send_one_time_broadcast(
    *,
    sender: MaxMessageSender,
    users_repository: UsersRepository,
    database_path: str,
    text: str,
    recipients: Sequence[BroadcastRecipient],
    audience: BroadcastAudience = ALL_USERS_AUDIENCE,
    actor_platform_user_id: str | None = None,
    broadcast_id: str | None = None,
    send_delay_seconds: float = DEFAULT_SEND_DELAY_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> BroadcastSendReport:
    """Send a one-time broadcast and persist every recipient delivery result."""

    session_id = broadcast_id or uuid.uuid4().hex
    sent = failed = blocked = 0
    safe_batch_size = max(1, int(batch_size))
    safe_send_delay = max(0.0, float(send_delay_seconds))
    safe_batch_pause = max(0.0, float(batch_pause_seconds))

    logger.info(
        "broadcast_one_time_started broadcast_id=%s audience=%s recipients=%s actor_platform_user_id=%s",
        session_id,
        audience.key,
        len(recipients),
        actor_platform_user_id,
    )

    for index, recipient in enumerate(recipients, start=1):
        metadata = {
            "broadcast_id": session_id,
            "audience_key": audience.key,
            "audience_label": audience.label,
            "actor_platform_user_id": actor_platform_user_id,
        }
        try:
            result = await _send_to_recipient(sender, recipient, text, metadata=metadata)
        except Exception as exc:  # defensive isolation per recipient
            logger.warning(
                "broadcast_one_time_recipient_exception broadcast_id=%s recipient_type=%s error_class=%s",
                session_id,
                recipient.recipient_type,
                type(exc).__name__,
                exc_info=True,
            )
            result = MaxSendResult(
                ok=False,
                status_code=None,
                message_id=None,
                recipient_type=recipient.recipient_type,
                recipient_id=recipient.recipient_id,
                error_code=type(exc).__name__,
                error_message=str(exc)[:240],
                is_retryable=False,
                attempts=1,
            )

        status = "sent" if result.ok else "blocked" if result.is_blocked else "stopped" if result.is_stopped else "failed"
        if result.ok:
            sent += 1
        else:
            failed += 1
        if result.is_blocked or result.is_stopped:
            blocked += 1
            _disable_recipient_notifications(users_repository, recipient)

        _save_broadcast_delivery(
            database_path,
            recipient=recipient,
            result=result,
            status=status,
            metadata=metadata,
        )

        if index < len(recipients):
            if safe_batch_pause and index % safe_batch_size == 0:
                await sleep(safe_batch_pause)
            elif safe_send_delay:
                await sleep(safe_send_delay)

    report = BroadcastSendReport(
        total=len(recipients),
        sent=sent,
        failed=failed,
        blocked=blocked,
        broadcast_id=session_id,
    )
    logger.info(
        "broadcast_one_time_finished broadcast_id=%s recipients=%s sent=%s failed=%s blocked=%s",
        session_id,
        report.total,
        report.sent,
        report.failed,
        report.blocked,
    )
    return report


async def _send_to_recipient(
    sender: MaxMessageSender,
    recipient: BroadcastRecipient,
    text: str,
    *,
    metadata: dict[str, object],
) -> MaxSendResult:
    if recipient.chat_id:
        return await sender.send_to_chat(recipient.chat_id, text, metadata=metadata)
    return await sender.send_to_user(recipient.max_user_id or recipient.platform_user_id, text, metadata=metadata)


def _save_broadcast_delivery(
    database_path: str,
    *,
    recipient: BroadcastRecipient,
    result: MaxSendResult,
    status: str,
    metadata: dict[str, object],
) -> None:
    try:
        delivery = NotificationDeliveryResult.from_max_result(
            result,
            platform_user_id=recipient.platform_user_id,
            max_user_id=recipient.max_user_id,
            chat_id=recipient.chat_id,
            message_type=BROADCAST_NOTIFICATION_TYPE,
            metadata=metadata,
        )
        save_delivery_result(
            database_path,
            NotificationDeliveryResult(
                platform=delivery.platform,
                platform_user_id=delivery.platform_user_id,
                max_user_id=delivery.max_user_id,
                chat_id=delivery.chat_id,
                message_type=delivery.message_type,
                recipient_type=delivery.recipient_type,
                recipient_id=delivery.recipient_id,
                status=status,
                status_code=delivery.status_code,
                error_code=delivery.error_code,
                error_message=delivery.error_message,
                attempts=delivery.attempts,
                message_id=delivery.message_id,
                is_blocked=delivery.is_blocked,
                is_stopped=delivery.is_stopped,
                metadata=delivery.metadata,
            ),
        )
    except Exception:
        logger.warning(
            "broadcast_delivery_save_failed recipient_type=%s status=%s",
            result.recipient_type,
            status,
            exc_info=True,
        )


def _disable_recipient_notifications(
    users_repository: UsersRepository,
    recipient: BroadcastRecipient,
) -> None:
    try:
        users_repository.update_profile(
            recipient.platform_user_id,
            UserProfileUpdate(notifications_enabled=False),
            platform=PLATFORM_MAX,
        )
    except Exception:
        logger.warning(
            "broadcast_disable_user_notifications_failed platform_user_id=%s",
            recipient.platform_user_id,
            exc_info=True,
        )
