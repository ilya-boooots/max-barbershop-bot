"""Platform-agnostic YClients-sourced one-time broadcast delivery."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import aiohttp
from dataclasses import dataclass, field
from typing import Any, Protocol

from max_barbershop_bot.integrations.yclients.dto import YClientsNormalizedClient
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.omnichannel_broadcasts import OmnichannelBroadcastRepository
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, PLATFORM_TELEGRAM, User, UsersRepository
from max_barbershop_bot.repositories.telegram_users import TelegramUsersRepository, TelegramUserRecord
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
    telegram_selected: int = 0
    max_selected: int = 0
    last_telegram_error_code: str | None = None
    last_telegram_error_short: str | None = None
    telegram_unavailable_reason: str | None = None

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
            "telegram_selected": self.telegram_selected,
            "max_selected": self.max_selected,
            "last_telegram_error_code": self.last_telegram_error_code,
            "last_telegram_error_short": self.last_telegram_error_short,
            "telegram_unavailable_reason": self.telegram_unavailable_reason,
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



class TelegramBotApiBroadcastAdapter:
    """Telegram Bot API adapter using plain HTTP, without aiogram imports."""

    platform = PLATFORM_TELEGRAM

    def __init__(self, *, bot_token: str, timeout_seconds: float = 30.0) -> None:
        self._bot_token = bot_token.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.sent_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.last_error_code: str | None = None
        self.last_error_short: str | None = None

    def can_send(self, target: DeliveryTarget) -> bool:
        return bool(self._bot_token and target.platform == PLATFORM_TELEGRAM and (target.chat_id or target.platform_user_id))

    async def send_text(self, target: DeliveryTarget, text: str) -> tuple[bool, str | None]:
        return await self._request("sendMessage", {"chat_id": target.chat_id or target.platform_user_id, "text": text})

    async def send_media(self, target: DeliveryTarget, text: str, attachment: BroadcastAttachmentPayload) -> tuple[bool, str | None]:
        media = _telegram_media_reference(attachment)
        if not media:
            self.skipped_count += 1
            return False, "skipped_media_unsupported"
        method_by_type = {"photo": "sendPhoto", "video": "sendVideo", "gif": "sendAnimation"}
        media_field_by_type = {"photo": "photo", "video": "video", "gif": "animation"}
        method = method_by_type.get(attachment.type or "")
        field = media_field_by_type.get(attachment.type or "")
        if not method or not field:
            self.skipped_count += 1
            return False, "skipped_media_unsupported"
        payload = {"chat_id": target.chat_id or target.platform_user_id, field: media}
        if text:
            payload["caption"] = text[:1024]
        return await self._request(method, payload)


    async def smoke_check(self, *, test_chat_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"token_configured": bool(self._bot_token), "get_me_ok": False, "test_send_ok": None, "test_send_skipped": False, "error": None}
        if not self._bot_token:
            result["error"] = "telegram_token_missing"
            return result
        ok, error = await self._request("getMe", {}, use_get=True)
        result["get_me_ok"] = ok
        if error:
            result["error"] = error
        if not test_chat_id:
            result["test_send_skipped"] = True
            return result
        ok, error = await self._request("sendMessage", {"chat_id": str(test_chat_id), "text": "✅ Telegram adapter test from MAX bot"})
        result["test_send_ok"] = ok
        if error:
            result["error"] = error
        return result

    async def _request(self, method: str, payload: dict[str, Any], *, retry_once: bool = True, use_get: bool = False) -> tuple[bool, str | None]:
        url = f"https://api.telegram.org/bot{self._bot_token}/{method}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                request_ctx = session.get(url, params=payload) if use_get else session.post(url, json=payload)
                async with request_ctx as response:
                    data = await _safe_json(response)
                    if response.status == 429:
                        retry_after = _retry_after(data)
                        logger.warning(
                            "MAX Telegram broadcast diagnostic: telegram_method=%s http_status=%s retry_after_present=%s",
                            method, response.status, retry_after is not None,
                        )
                        if retry_once and retry_after is not None and retry_after <= 5:
                            await asyncio.sleep(float(retry_after))
                            return await self._request(method, payload, retry_once=False, use_get=use_get)
                    ok = bool(isinstance(data, dict) and data.get("ok"))
                    if ok:
                        self.sent_count += 1
                        logger.info("MAX Telegram broadcast diagnostic: telegram_method=%s http_status=%s sent_count=%s", method, response.status, self.sent_count)
                        return True, None
                    reason = _telegram_error_reason(response.status, data)
                    self.last_error_code = str(response.status)
                    self.last_error_short = reason[:120]
                    if reason.startswith("skipped"):
                        self.skipped_count += 1
                    else:
                        self.failed_count += 1
                    logger.warning(
                        "MAX Telegram broadcast diagnostic: telegram_method=%s http_status=%s error_code=%s retry_after_present=%s",
                        method, response.status, reason, _retry_after(data) is not None,
                    )
                    return False, reason
        except Exception as exc:  # noqa: BLE001 - isolate one recipient.
            self.failed_count += 1
            self.last_error_code = type(exc).__name__
            self.last_error_short = type(exc).__name__[:120]
            logger.warning(
                "MAX Telegram broadcast diagnostic: telegram_method=%s error_code=%s",
                method, type(exc).__name__,
            )
            return False, type(exc).__name__

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



def _telegram_media_reference(attachment: BroadcastAttachmentPayload) -> str | None:
    roots = []
    if attachment.telegram_payload:
        roots.append(attachment.telegram_payload)
    if attachment.max_payload:
        roots.append(attachment.max_payload)
        payload = attachment.max_payload.get("payload") if isinstance(attachment.max_payload.get("payload"), dict) else None
        if payload:
            roots.append(payload)
    for root in roots:
        for key in ("file_id", "telegram_file_id", "url", "download_url"):
            value = root.get(key)
            if value:
                return str(value).strip()
    return None


async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, Any] | None:
    try:
        data = await response.json(content_type=None)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _retry_after(data: dict[str, Any] | None) -> int | None:
    params = data.get("parameters") if isinstance(data, dict) else None
    if not isinstance(params, dict):
        return None
    value = params.get("retry_after")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _telegram_error_reason(http_status: int, data: dict[str, Any] | None) -> str:
    description = str(data.get("description") if isinstance(data, dict) else "").lower()
    if http_status == 403 or "blocked" in description or "forbidden" in description:
        return "skipped_blocked"
    if http_status == 400 and ("chat not found" in description or "user not found" in description or "invalid" in description):
        return "skipped_unreachable"
    if http_status == 429:
        return "failed_rate_limited"
    code = data.get("error_code") if isinstance(data, dict) else http_status
    return f"failed_telegram_{code}"

class OmnichannelBroadcastService:
    """Resolve YClients clients to one best platform target and send once."""

    def __init__(self, *, users_repository: UsersRepository, attribution_repository: PlatformAttributionRepository, history_repository: OmnichannelBroadcastRepository, adapters: dict[str, BroadcastDeliveryAdapter], telegram_users_repository: TelegramUsersRepository | None = None, telegram_unavailable_reason: str | None = None) -> None:
        self.users = users_repository
        self.telegram_users = telegram_users_repository
        self.attribution = attribution_repository
        self.history = history_repository
        self.adapters = adapters
        self.telegram_unavailable_reason = telegram_unavailable_reason

    def estimate(self, clients: list[YClientsNormalizedClient], *, attachment: BroadcastAttachmentPayload | None = None) -> AudienceEstimate:
        targets = [self.resolve_delivery_target_for_yclients_client(client) for client in clients]
        telegram_candidates = sum(1 for client in clients if self._candidate(client, PLATFORM_TELEGRAM) is not None)
        max_candidates = sum(1 for client in clients if self._candidate(client, PLATFORM_MAX) is not None)
        both = sum(1 for client in clients if self._has_exact_yclients_link(client, PLATFORM_TELEGRAM) and self._has_exact_yclients_link(client, PLATFORM_MAX))
        logger.info(
            "MAX Telegram broadcast diagnostic: yclients_clients_count=%s telegram_candidates_count=%s max_candidates_count=%s both_platforms_count=%s telegram_selected_count=%s max_selected_count=%s",
            len(clients), telegram_candidates, max_candidates, both, sum(1 for t in targets if t.platform == PLATFORM_TELEGRAM), sum(1 for t in targets if t.platform == PLATFORM_MAX),
        )
        media_supported = not attachment or not attachment.type or bool(_telegram_media_reference(attachment))
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
        report.telegram_unavailable_reason = self.telegram_unavailable_reason
        targets = [self.resolve_delivery_target_for_yclients_client(client) for client in clients]
        report.telegram_selected = sum(1 for t in targets if t.platform == PLATFORM_TELEGRAM)
        report.max_selected = sum(1 for t in targets if t.platform == PLATFORM_MAX)
        logger.info("MAX omnichannel broadcast diagnostic: send_started broadcast_id=%s origin_platform=%s yclients_clients_count=%s attachment_type=%s", bid, origin_platform, len(clients), attachment.type if attachment else None)
        for client, target in zip(clients, targets):
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
                if target.platform == PLATFORM_TELEGRAM and error:
                    report.last_telegram_error_code = str(error).split(":", 1)[0][:120]
                    report.last_telegram_error_short = str(error)[:120]
            self.history.add_delivery(broadcast_id=bid, yclients_client_id=client.id, selected_platform=target.platform, platform_user_id=target.platform_user_id, delivery_status=status, reason=reason, origin_platform=origin_platform, priority_decision=target.priority_decision, error_short=reason, sent=ok)
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)
        report.finished_at = time.time()
        self.history.mark_status(bid, "completed", report=report.as_dict(), finished=True)
        telegram_failed = max(0, report.telegram_selected - report.telegram_sent - report.skipped_blocked - report.skipped_sender_unavailable - report.skipped_media_unsupported)
        logger.info("MAX Telegram broadcast diagnostic: telegram_selected_count=%s max_selected_count=%s skipped_sender_unavailable_count=%s skipped_unreachable_count=%s skipped_blocked_count=%s telegram_sent_count=%s telegram_failed_count=%s last_telegram_error_code=%s last_telegram_error_short=%s", report.telegram_selected, report.max_selected, report.skipped_sender_unavailable, report.skipped_unreachable, report.skipped_blocked, report.telegram_sent, telegram_failed, report.last_telegram_error_code, report.last_telegram_error_short)
        logger.info("MAX omnichannel broadcast diagnostic: send_finished broadcast_id=%s sent_telegram_count=%s sent_max_count=%s failed_count=%s skipped_count=%s duration_ms=%s", bid, report.telegram_sent, report.max_sent, report.failed, report.not_delivered, report.duration_ms)
        return report


    def _has_exact_yclients_link(self, client: YClientsNormalizedClient, platform: str) -> bool:
        if not client.id:
            return False
        return any(self._is_deliverable(user, platform) for user in self._list_by_yclients_client_id(client.id, platform=platform))

    def _candidate(self, client: YClientsNormalizedClient, platform: str) -> User | TelegramUserRecord | None:
        if client.id:
            users = self._list_by_yclients_client_id(client.id, platform=platform)
            deliverable = [u for u in users if self._is_deliverable(u, platform)]
            if deliverable:
                return deliverable[0]
        keys: set[str] = set()
        for phone in client.phones:
            keys.update(build_phone_match_keys(phone))
        users = self._list_by_phone_keys(keys, platform=platform)
        deliverable = [u for u in users if self._is_deliverable(u, platform)]
        if deliverable:
            return deliverable[0]
        for record in self.attribution.list_by_booking_phone_keys(keys, platform=platform):
            user = self._find_by_platform_user_id(record.platform_user_id, platform=platform)
            if user and self._is_deliverable(user, platform):
                return user
        if client.id:
            for record in self.attribution.list_by_yclients_client_id(client.id):
                if record.platform != platform:
                    continue
                user = self._find_by_platform_user_id(record.platform_user_id, platform=platform)
                if user and self._is_deliverable(user, platform):
                    return user
        return None

    def _list_by_yclients_client_id(self, yclients_client_id: str, *, platform: str):
        if platform == PLATFORM_TELEGRAM and self.telegram_users is not None:
            return self.telegram_users.list_by_yclients_client_id(yclients_client_id, platform=platform)
        return self.users.list_by_yclients_client_id(yclients_client_id, platform=platform)

    def _list_by_phone_keys(self, keys: set[str], *, platform: str):
        if platform == PLATFORM_TELEGRAM and self.telegram_users is not None:
            return self.telegram_users.list_by_phone_keys(keys, platform=platform)
        return self.users.list_by_phone_keys(keys, platform=platform)

    def _find_by_platform_user_id(self, platform_user_id: str, *, platform: str):
        if platform == PLATFORM_TELEGRAM and self.telegram_users is not None:
            return self.telegram_users.find_by_platform_user_id(platform_user_id, platform=platform)
        return self.users.find_by_platform_user_id(platform_user_id, platform=platform)

    def _is_deliverable(self, user: User | TelegramUserRecord, platform: str) -> bool:
        if not user.notifications_enabled or bool(getattr(user, "blocked", False)) or bool(getattr(user, "stopped", False)):
            return False
        if platform == PLATFORM_MAX:
            return bool(user.platform_user_id and (user.max_user_id or user.chat_id))
        if platform == PLATFORM_TELEGRAM:
            return bool(user.platform_user_id)
        return False
