"""One-time broadcast helpers for the MAX bot."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
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
BROADCAST_ALL_USERS_LABEL = "👥 Все клиенты"
BROADCAST_SELF_AUDIENCE = "send_to_self"
BROADCAST_SELF_LABEL = "🧪 Отправить себе"
MAX_BROADCAST_TEXT_LENGTH = 4000
DEFAULT_SEND_DELAY_SECONDS = 0.1
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_PAUSE_SECONDS = 1.0




@dataclass(frozen=True)
class BroadcastAttachment:
    """Reusable MAX broadcast media attachment."""

    attachment_type: str
    attachment: dict[str, Any]


@dataclass(frozen=True)
class BroadcastDraft:
    """One-time broadcast draft stored in navigation state."""

    text: str | None = None
    attachment_type: str | None = None
    attachment: dict[str, Any] | None = None
    audience_key: str | None = None
    audience_label: str | None = None

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
    stopped: int = 0
    skipped_notifications_disabled: int = 0
    skipped_missing_recipient_id: int = 0
    rate_limited: int = 0
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
SELF_AUDIENCE = BroadcastAudience(
    key=BROADCAST_SELF_AUDIENCE,
    label=BROADCAST_SELF_LABEL,
)


def validate_broadcast_text(text: str | None) -> BroadcastTextValidation:
    """Trim and validate one-time broadcast text."""

    clean = (text or "").strip()
    if not clean:
        return BroadcastTextValidation(ok=False, error="⚠️ Текст рассылки не может быть пустым. Введите сообщение.")
    if clean.startswith("/"):
        return BroadcastTextValidation(ok=False, error="⚠️ Команды нельзя отправлять как текст рассылки. Введите сообщение.")
    if len(clean) > MAX_BROADCAST_TEXT_LENGTH:
        return BroadcastTextValidation(
            ok=False,
            error=f"⚠️ Слишком длинный текст. Максимум {MAX_BROADCAST_TEXT_LENGTH} символов.",
        )
    return BroadcastTextValidation(ok=True, text=clean)


def build_broadcast_preview(text: str, attachment_type: str | None = None) -> str:
    """Build the preview screen text."""

    media_line = f"\n\nВложение: {_attachment_label(attachment_type)}" if attachment_type else ""
    return f"👀 Предпросмотр рассылки\n\n{text}{media_line}"


def build_broadcast_confirm_text(*, audience_label: str, recipient_count: int, text: str, attachment_type: str | None = None) -> str:
    """Build the final confirmation screen text."""

    media_line = f"\nВложение: {_attachment_label(attachment_type)}" if attachment_type else ""
    return (
        f"👀 Предпросмотр рассылки\n\n"
        f"Аудитория: {audience_label}\n"
        f"Получателей: {recipient_count}{media_line}\n\n"
        f"{text}\n\n"
        "Отправить рассылку?"
    )


def format_broadcast_report(report: BroadcastSendReport) -> str:
    """Build final one-time broadcast report text."""

    return (
        "✅ Рассылка завершена\n\n"
        f"Всего клиентов: {report.total}\n"
        f"Отправлено: {report.sent}\n"
        f"Ошибок: {report.failed}\n"
        f"Заблокировали бота: {report.blocked}\n"
        f"Остановили бота: {report.stopped}\n"
        f"Пропущено: {report.skipped_notifications_disabled + report.skipped_missing_recipient_id}\n"
        f"— отключены уведомления: {report.skipped_notifications_disabled}\n"
        f"— нет MAX ID/чата: {report.skipped_missing_recipient_id}\n"
        f"Rate limit/повторы: {report.rate_limited}"
    )


def get_all_registered_recipients(users_repository: UsersRepository) -> list[BroadcastRecipient]:
    """Return local MAX users who can receive a broadcast."""

    return build_recipients_from_users(
        users_repository.list_broadcast_recipients(
            platform=PLATFORM_MAX,
            notifications_enabled=True,
        )
    )


def build_recipients_from_users(users: Sequence[object]) -> list[BroadcastRecipient]:
    """Build sendable broadcast recipients from user-like objects."""

    recipients: list[BroadcastRecipient] = []
    for user in users:
        platform_user_id = str(getattr(user, "platform_user_id", "") or "").strip()
        max_user_id = str(getattr(user, "max_user_id", "") or "").strip() or None
        chat_id = str(getattr(user, "chat_id", "") or "").strip() or None
        if not platform_user_id or not (max_user_id or chat_id):
            continue
        recipients.append(
            BroadcastRecipient(
                platform_user_id=platform_user_id,
                max_user_id=max_user_id,
                chat_id=chat_id,
                display_name=getattr(user, "display_name", None) or getattr(user, "first_name", None),
            )
        )
    return recipients


async def send_one_time_broadcast(
    *,
    sender: MaxMessageSender,
    users_repository: UsersRepository,
    database_path: str,
    text: str,
    recipients: Sequence[BroadcastRecipient],
    attachment: Mapping[str, Any] | None = None,
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
    sent = failed = blocked = stopped = rate_limited = 0
    safe_batch_size = max(1, int(batch_size))
    safe_send_delay = max(0.0, float(send_delay_seconds))
    safe_batch_pause = max(0.0, float(batch_pause_seconds))

    logger.info(
        "MAX broadcast diagnostic: broadcast_one_time_started broadcast_id=%s audience=%s recipients_count=%s actor_platform_user_id_present=%s",
        session_id,
        audience.key,
        len(recipients),
        bool(actor_platform_user_id),
    )

    for index, recipient in enumerate(recipients, start=1):
        metadata = {
            "broadcast_id": session_id,
            "audience_key": audience.key,
            "audience_label": audience.label,
            "actor_platform_user_id": actor_platform_user_id,
        }
        try:
            result = await _send_to_recipient(sender, recipient, text, attachment=attachment, metadata=metadata)
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
        if result.is_blocked:
            blocked += 1
        if result.is_stopped:
            stopped += 1
        if result.status_code == 429 or result.error_code == "rate_limited":
            rate_limited += 1
        if result.is_blocked or result.is_stopped:
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
        stopped=stopped,
        rate_limited=rate_limited,
        broadcast_id=session_id,
    )
    logger.info(
        "MAX broadcast diagnostic: broadcast_one_time_finished broadcast_id=%s recipients_count=%s sent_count=%s failed_count=%s blocked_count=%s",
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
    attachment: Mapping[str, Any] | None = None,
    metadata: dict[str, object],
) -> MaxSendResult:
    attachments = [dict(attachment)] if attachment else None
    if recipient.chat_id:
        return await sender.send_to_chat(recipient.chat_id, text, attachments=attachments, metadata=metadata)
    return await sender.send_to_user(recipient.max_user_id or recipient.platform_user_id, text, attachments=attachments, metadata=metadata)


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


def extract_broadcast_attachment(attachments: Sequence[Any]) -> BroadcastAttachment | None:
    """Extract a reusable photo/GIF/video attachment from a MAX message."""

    for attachment in attachments:
        normalized = _normalize_broadcast_attachment(attachment)
        if normalized is not None:
            return normalized
    return None


def _normalize_broadcast_attachment(attachment: Any) -> BroadcastAttachment | None:
    if not isinstance(attachment, Mapping):
        return None
    raw_type = str(attachment.get("type") or "").strip().lower()
    normalized_type = {"photo": "photo", "image": "photo", "video": "video", "gif": "gif", "animation": "gif"}.get(raw_type)
    if normalized_type is None:
        return None
    max_type = "image" if normalized_type in {"photo", "gif"} else "video"
    payload = attachment.get("payload") if isinstance(attachment.get("payload"), Mapping) else {}
    token = _first_text_value((payload, attachment), keys=("token", "file_id", "id"))
    url = _first_text_value((payload, attachment), keys=("url", "download_url"))
    compact_payload: dict[str, Any] = {}
    if token:
        compact_payload["token"] = token
    if url:
        compact_payload["url"] = url
    if not compact_payload:
        return None
    result = {"type": max_type, "payload": compact_payload}
    if normalized_type == "gif":
        result["payload"]["content_type"] = "image/gif"
    return BroadcastAttachment(attachment_type=normalized_type, attachment=result)


def _first_text_value(roots: Sequence[Mapping[str, Any]], *, keys: tuple[str, ...]) -> str:
    for root in roots:
        for key in keys:
            value = root.get(key)
            if value:
                return str(value).strip()
        nested = _first_nested_text_value(root, keys=keys)
        if nested:
            return nested
    return ""


def _first_nested_text_value(value: Any, *, keys: tuple[str, ...]) -> str:
    if isinstance(value, Mapping):
        for key in keys:
            found = value.get(key)
            if found:
                return str(found).strip()
        for child_key, child in value.items():
            if child_key in {"bytes", "data", "content", "file", "raw", "thumbnail"}:
                continue
            found = _first_nested_text_value(child, keys=keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_nested_text_value(child, keys=keys)
            if found:
                return found
    return ""


def _attachment_label(attachment_type: str | None) -> str:
    return {"photo": "🖼 фото", "gif": "🎞 GIF", "video": "🎬 видео"}.get(attachment_type or "", "медиа")
