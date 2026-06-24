"""Platform-agnostic YClients-sourced one-time broadcast delivery."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from max_barbershop_bot.integrations.yclients.dto import YClientsNormalizedClient
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.omnichannel_broadcasts import OmnichannelBroadcastRepository
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, PLATFORM_TELEGRAM, User, UsersRepository
from max_barbershop_bot.services.broadcasts import BroadcastRecipient, _send_to_recipient
from max_barbershop_bot.services.phone_normalization import build_phone_match_keys

logger = logging.getLogger(__name__)
AUDIENCE_SOURCE_YCLIENTS_ALL = "yclients_all_clients"


@dataclass(frozen=True)
class BroadcastAttachmentPayload:
    type: str | None = None
    original_platform: str | None = None
    max_payload: dict[str, Any] | None = None
    telegram_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeliveryTarget:
    yclients_client_id: str | None
    platform: str | None
    platform_user_id: str | None
    max_user_id: str | None = None
    chat_id: str | None = None
    priority_decision: str = "unreachable"
    reason: str | None = None


@dataclass(frozen=True)
class AudienceEstimate:
    total_yclients_clients: int
    telegram_candidates: int
    max_candidates: int
    both_platforms: int
    telegram_selected: int
    max_selected: int
    unreachable: int
    duplicates_excluded: int
    media_cross_platform_supported: bool = True
    media_warning: str | None = None

    @property
    def total_deliveries(self) -> int:
        return self.telegram_selected + self.max_selected


@dataclass
class OmnichannelBroadcastReport:
    broadcast_id: str
    total_yclients_clients: int = 0
    telegram_sent: int = 0
    max_sent: int = 0
    failed: int = 0
    skipped_unreachable: int = 0
    skipped_duplicate: int = 0
    skipped_blocked: int = 0
    skipped_opted_out: int = 0
    skipped_media_unsupported: int = 0
    skipped_sender_unavailable: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return int(((self.finished_at or time.time()) - self.started_at) * 1000)

    @property
    def not_delivered(self) -> int:
        return self.skipped_unreachable + self.failed + self.skipped_blocked + self.skipped_opted_out + self.skipped_media_unsupported + self.skipped_sender_unavailable

    def as_dict(self) -> dict[str, Any]:
        return {
            "broadcast_id": self.broadcast_id,
            "total_yclients_clients": self.total_yclients_clients,
            "telegram_sent": self.telegram_sent,
            "max_sent": self.max_sent,
            "failed": self.failed,
            "skipped_unreachable": self.skipped_unreachable,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_blocked": self.skipped_blocked,
            "skipped_opted_out": self.skipped_opted_out,
            "skipped_media_unsupported": self.skipped_media_unsupported,
            "skipped_sender_unavailable": self.skipped_sender_unavailable,
            "duration_ms": self.duration_ms,
        }


class BroadcastDeliveryAdapter(Protocol):
    platform: str
    def can_send(self, target: DeliveryTarget) -> bool: ...
    async def send_text(self, target: DeliveryTarget, text: str) -> tuple[bool, str | None]: ...
    async def send_media(self, target: DeliveryTarget, text: str, attachment: BroadcastAttachmentPayload) -> tuple[bool, str | None]: ...
    def format_error(self, error: Exception) -> str: ...


class MaxBroadcastDeliveryAdapter:
    platform = PLATFORM_MAX

    def __init__(self, sender: MaxMessageSender) -> None:
        self._sender = sender

    def can_send(self, target: DeliveryTarget) -> bool:
        return bool(target.platform == PLATFORM_MAX and (target.max_user_id or target.chat_id or target.platform_user_id))

    async def send_text(self, target: DeliveryTarget, text: str) -> tuple[bool, str | None]:
        return await self._send(target, text, None)

    async def send_media(self, target: DeliveryTarget, text: str, attachment: BroadcastAttachmentPayload) -> tuple[bool, str | None]:
        if not attachment.max_payload:
            return False, "skipped_media_unsupported"
        return await self._send(target, text, attachment.max_payload)

    async def _send(self, target: DeliveryTarget, text: str, attachment: dict[str, Any] | None) -> tuple[bool, str | None]:
        recipient = BroadcastRecipient(platform_user_id=target.platform_user_id or "", max_user_id=target.max_user_id, chat_id=target.chat_id)
        result = await _send_to_recipient(self._sender, recipient, text, attachment=attachment, metadata={"broadcast_id": target.yclients_client_id, "audience_source": AUDIENCE_SOURCE_YCLIENTS_ALL})
        if result.ok:
            return True, None
        if result.is_blocked:
            return False, "skipped_blocked"
        if result.is_stopped:
            return False, "skipped_blocked"
        return False, (result.error_code or result.error_message or "failed")[:120]

    def format_error(self, error: Exception) -> str:
        return type(error).__name__


class TelegramUnavailableBroadcastAdapter:
    platform = PLATFORM_TELEGRAM

    def can_send(self, target: DeliveryTarget) -> bool:
        return False

    async def send_text(self, target: DeliveryTarget, text: str) -> tuple[bool, str | None]:
        return False, "telegram_sender_unavailable"

    async def send_media(self, target: DeliveryTarget, text: str, attachment: BroadcastAttachmentPayload) -> tuple[bool, str | None]:
        return False, "telegram_sender_unavailable"

    def format_error(self, error: Exception) -> str:
        return "telegram_sender_unavailable"


class OmnichannelBroadcastService:
    """Resolve YClients clients to one best platform target and send once."""

    def __init__(self, *, users_repository: UsersRepository, attribution_repository: PlatformAttributionRepository, history_repository: OmnichannelBroadcastRepository, adapters: dict[str, BroadcastDeliveryAdapter]) -> None:
        self.users = users_repository
        self.attribution = attribution_repository
        self.history = history_repository
        self.adapters = adapters

    def estimate(self, clients: list[YClientsNormalizedClient], *, attachment: BroadcastAttachmentPayload | None = None) -> AudienceEstimate:
        targets = [self.resolve_delivery_target_for_yclients_client(client) for client in clients]
        telegram_candidates = sum(1 for client in clients if self._candidate(client, PLATFORM_TELEGRAM) is not None)
        max_candidates = sum(1 for client in clients if self._candidate(client, PLATFORM_MAX) is not None)
        both = sum(1 for client in clients if self._has_exact_yclients_link(client, PLATFORM_TELEGRAM) and self._has_exact_yclients_link(client, PLATFORM_MAX))
        media_supported = not attachment or not attachment.type or bool(attachment.telegram_payload or self.adapters.get(PLATFORM_TELEGRAM, TelegramUnavailableBroadcastAdapter()).can_send(DeliveryTarget(None, PLATFORM_TELEGRAM, None)))
        return AudienceEstimate(
            total_yclients_clients=len(clients), telegram_candidates=telegram_candidates, max_candidates=max_candidates,
            both_platforms=both, telegram_selected=sum(1 for t in targets if t.platform == PLATFORM_TELEGRAM),
            max_selected=sum(1 for t in targets if t.platform == PLATFORM_MAX), unreachable=sum(1 for t in targets if t.platform is None),
            duplicates_excluded=both, media_cross_platform_supported=media_supported,
            media_warning=("⚠️ Telegram-отправитель или медиа для Telegram недоступны: такие получатели будут отмечены как недоставленные." if attachment and attachment.type and not media_supported else None),
        )

    def resolve_delivery_target_for_yclients_client(self, client: YClientsNormalizedClient) -> DeliveryTarget:
        telegram = self._candidate(client, PLATFORM_TELEGRAM)
        max_user = self._candidate(client, PLATFORM_MAX)
        if telegram and self._is_deliverable(telegram, PLATFORM_TELEGRAM):
            return DeliveryTarget(client.id, PLATFORM_TELEGRAM, telegram.platform_user_id, telegram.max_user_id, telegram.chat_id, "telegram_priority", "telegram_selected")
        if max_user and self._is_deliverable(max_user, PLATFORM_MAX):
            reason = "max_selected_after_telegram_absent" if not telegram else "max_selected_telegram_not_deliverable"
            return DeliveryTarget(client.id, PLATFORM_MAX, max_user.platform_user_id, max_user.max_user_id, max_user.chat_id, reason, reason)
        return DeliveryTarget(client.id, None, None, priority_decision="unreachable", reason="skipped_unreachable")

    async def send(self, *, clients: list[YClientsNormalizedClient], text: str, origin_platform: str, created_by_user_id: str | None, attachment: BroadcastAttachmentPayload | None = None, broadcast_id: str | None = None, sleep_seconds: float = 0.1) -> OmnichannelBroadcastReport:
        bid = broadcast_id or uuid.uuid4().hex
        self.history.upsert_broadcast(broadcast_id=bid, origin_platform=origin_platform, text=text, attachment_type=attachment.type if attachment else None, attachment=attachment.max_payload if attachment else None, created_by_user_id=created_by_user_id, status="sending")
        self.history.mark_status(bid, "sending", started=True)
        report = OmnichannelBroadcastReport(broadcast_id=bid, total_yclients_clients=len(clients))
        logger.info("MAX omnichannel broadcast diagnostic: send_started broadcast_id=%s origin_platform=%s yclients_clients_count=%s attachment_type=%s", bid, origin_platform, len(clients), attachment.type if attachment else None)
        for client in clients:
            target = self.resolve_delivery_target_for_yclients_client(client)
            if target.platform is None:
                report.skipped_unreachable += 1
                self.history.add_delivery(broadcast_id=bid, yclients_client_id=client.id, selected_platform=None, platform_user_id=None, delivery_status="skipped_unreachable", reason="skipped_unreachable", origin_platform=origin_platform, priority_decision=target.priority_decision)
                continue
            if target.priority_decision == "telegram_priority" and self._has_exact_yclients_link(client, PLATFORM_MAX):
                report.skipped_duplicate += 1
            adapter = self.adapters.get(target.platform)
            if adapter is None or not adapter.can_send(target):
                report.skipped_sender_unavailable += 1
                self.history.add_delivery(broadcast_id=bid, yclients_client_id=client.id, selected_platform=target.platform, platform_user_id=target.platform_user_id, delivery_status="skipped_sender_unavailable", reason="sender_unavailable", origin_platform=origin_platform, priority_decision=target.priority_decision)
                continue
            if attachment and attachment.type:
                ok, error = await adapter.send_media(target, text, attachment)
            else:
                ok, error = await adapter.send_text(target, text)
            if ok:
                if target.platform == PLATFORM_TELEGRAM:
                    report.telegram_sent += 1
                else:
                    report.max_sent += 1
                status = "sent"; reason = None
            else:
                if error in {"skipped_blocked", "blocked", "stopped"}:
                    report.skipped_blocked += 1; status = "skipped_blocked"; reason = error
                elif error == "skipped_media_unsupported":
                    report.skipped_media_unsupported += 1; status = error; reason = error
                elif error == "telegram_sender_unavailable":
                    report.skipped_sender_unavailable += 1; status = "skipped_sender_unavailable"; reason = error
                else:
                    report.failed += 1; status = "failed"; reason = error or "failed"; report.errors.append(str(reason)[:120])
            self.history.add_delivery(broadcast_id=bid, yclients_client_id=client.id, selected_platform=target.platform, platform_user_id=target.platform_user_id, delivery_status=status, reason=reason, origin_platform=origin_platform, priority_decision=target.priority_decision, error_short=reason, sent=ok)
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
        report.finished_at = time.time()
        self.history.mark_status(bid, "completed", report=report.as_dict(), finished=True)
        logger.info("MAX omnichannel broadcast diagnostic: send_finished broadcast_id=%s sent_telegram_count=%s sent_max_count=%s failed_count=%s skipped_count=%s duration_ms=%s", bid, report.telegram_sent, report.max_sent, report.failed, report.not_delivered, report.duration_ms)
        return report


    def _has_exact_yclients_link(self, client: YClientsNormalizedClient, platform: str) -> bool:
        if not client.id:
            return False
        return any(self._is_deliverable(user, platform) for user in self.users.list_by_yclients_client_id(client.id, platform=platform))

    def _candidate(self, client: YClientsNormalizedClient, platform: str) -> User | None:
        if client.id:
            users = self.users.list_by_yclients_client_id(client.id, platform=platform)
            deliverable = [u for u in users if self._is_deliverable(u, platform)]
            if deliverable:
                return deliverable[0]
        keys: set[str] = set()
        for phone in client.phones:
            keys.update(build_phone_match_keys(phone))
        users = self.users.list_by_phone_keys(keys, platform=platform)
        deliverable = [u for u in users if self._is_deliverable(u, platform)]
        if deliverable:
            return deliverable[0]
        for record in self.attribution.list_by_booking_phone_keys(keys, platform=platform):
            user = self.users.find_by_platform_user_id(record.platform_user_id, platform=platform)
            if user and self._is_deliverable(user, platform):
                return user
        if client.id:
            for record in self.attribution.list_by_yclients_client_id(client.id):
                if record.platform != platform:
                    continue
                user = self.users.find_by_platform_user_id(record.platform_user_id, platform=platform)
                if user and self._is_deliverable(user, platform):
                    return user
        return None

    def _is_deliverable(self, user: User, platform: str) -> bool:
        if not user.notifications_enabled:
            return False
        if platform == PLATFORM_MAX:
            return bool(user.platform_user_id and (user.max_user_id or user.chat_id))
        if platform == PLATFORM_TELEGRAM:
            return bool(user.platform_user_id)
        return False
