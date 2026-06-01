from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.core.safe_telegram import safe_send
from app.repositories import broadcasts as broadcasts_repo
from app.services.anti_spam import can_send_notification, record_delivery_decision

logger = logging.getLogger(__name__)

SEND_RATE_PER_SEC = 12
SEND_DELAY = 1 / SEND_RATE_PER_SEC
MAX_RETRIES = 2


class BroadcastSender:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="broadcast-sender")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _run_loop(self) -> None:
        logger.info("Broadcast sender started")
        while not self._stop_event.is_set():
            try:
                has_work = await self._process_scheduled_campaigns()
                if not has_work:
                    has_work = await self._process_next_broadcast()
                if not has_work:
                    await asyncio.sleep(1.0)
            except Exception:
                logger.exception("Broadcast sender loop error")
                await asyncio.sleep(2.0)
        logger.info("Broadcast sender stopped")

    async def _process_next_broadcast(self) -> bool:
        broadcast = await broadcasts_repo.get_next_broadcast_to_send()
        if not broadcast:
            return False

        broadcast_id = int(broadcast["id"])
        if broadcast["status"] == "draft":
            await broadcasts_repo.mark_broadcast_sending(broadcast_id)

        while not self._stop_event.is_set():
            current = await broadcasts_repo.get_broadcast(broadcast_id)
            if not current or current["status"] == "cancelled":
                return True
            pending = await broadcasts_repo.get_pending_recipients(broadcast_id, limit=1)
            if not pending:
                await broadcasts_repo.mark_broadcast_done(broadcast_id, status="done")
                return True
            await self._send_to_recipient(current, pending[0])
            await asyncio.sleep(SEND_DELAY)
        return True


    async def _process_scheduled_campaigns(self) -> bool:
        rows = await broadcasts_repo.fetchall("SELECT * FROM broadcast_campaigns WHERE status='scheduled' AND sent_at_utc IS NOT NULL AND sent_at_utc<=? ORDER BY id LIMIT 1", (broadcasts_repo.now_iso(),))
        if not rows:
            return False
        campaign = dict(rows[0])
        cid = int(campaign['id'])
        await broadcasts_repo.execute("UPDATE broadcast_campaigns SET status='sending' WHERE id=? AND status='scheduled'", (cid,))
        recs = await broadcasts_repo.fetchall("SELECT * FROM broadcast_recipient_logs WHERE campaign_id=? AND status='pending'", (cid,))
        sent=failed=blocked=skipped=0
        for r in recs:
            uid = r.get('recipient_tg_id')
            if not uid:
                skipped += 1
                await broadcasts_repo.execute("UPDATE broadcast_recipient_logs SET status='skipped_no_tg_id', error_summary=?, updated_at_utc=? WHERE id=?", ('нет Telegram ID', broadcasts_repo.now_iso(), r['id']))
                continue
            allowed, decision = await can_send_notification(client_tg_id=int(uid), notification_type='manual_broadcast', category='marketing', funnel_type='manual_broadcast', source_event_id=f'scheduled_campaign:{cid}:{uid}')
            if not allowed:
                skipped += 1
                await broadcasts_repo.execute("UPDATE broadcast_recipient_logs SET status='skipped', error_summary=?, updated_at_utc=? WHERE id=?", (decision[:120], broadcasts_repo.now_iso(), r['id']))
                continue
            result = await safe_send(self.bot, 'send_photo' if campaign.get('photo_file_id') else 'send_message', chat_id=int(uid), retries=MAX_RETRIES + 1, **({'photo': campaign.get('photo_file_id'), 'caption': campaign.get('text') or None} if campaign.get('photo_file_id') else {'text': campaign.get('text') or ''}))
            if result.ok:
                sent += 1
                await broadcasts_repo.execute("UPDATE broadcast_recipient_logs SET status='sent', sent_at_utc=?, updated_at_utc=? WHERE id=?", (broadcasts_repo.now_iso(), broadcasts_repo.now_iso(), r['id']))
            elif result.skipped:
                skipped += 1
                await broadcasts_repo.execute("UPDATE broadcast_recipient_logs SET status='skipped', error_summary=?, updated_at_utc=? WHERE id=?", ((result.error or 'skipped')[:120], broadcasts_repo.now_iso(), r['id']))
            else:
                failed += 1
                await broadcasts_repo.execute("UPDATE broadcast_recipient_logs SET status='failed', error_summary=?, updated_at_utc=? WHERE id=?", ((result.error or 'send_failed')[:120], broadcasts_repo.now_iso(), r['id']))
        await broadcasts_repo.execute("UPDATE broadcast_campaigns SET status=?, sent_count=?, failed_count=?, blocked_count=?, skipped_count=?, branch_local_sent_at=?, sent_at_utc=? WHERE id=?", ('sent' if failed==0 else 'failed', sent, failed, blocked, skipped, broadcasts_repo.now_iso(), broadcasts_repo.now_iso(), cid))
        return True

    async def _send_to_recipient(self, broadcast: dict, recipient: dict) -> None:
        recipient_id = int(recipient["id"])
        tg_user_id = int(recipient["tg_user_id"])
        message_type = broadcast["message_type"]
        text = (broadcast.get("text") or "").strip()
        file_id = broadcast.get("file_id")

        method = "send_message"
        kwargs: dict[str, object] = {"text": text}
        if message_type == "photo" and file_id:
            method = "send_photo"
            kwargs = {"photo": file_id, "caption": text or None}
        elif message_type == "video" and file_id:
            method = "send_video"
            kwargs = {"video": file_id, "caption": text or None}
        elif message_type == "animation" and file_id:
            method = "send_animation"
            kwargs = {"animation": file_id, "caption": text or None}

        allowed, decision = await can_send_notification(client_tg_id=tg_user_id, notification_type='manual_broadcast', category='marketing', funnel_type='manual_broadcast', source_event_id=f'broadcast:{broadcast.get("id")}')
        if not allowed:
            await broadcasts_repo.mark_recipient_status(recipient_id, status='skipped', error_short=decision[:120])
            await record_delivery_decision(client_tg_id=tg_user_id, notification_type='manual_broadcast', category='marketing', funnel_type='manual_broadcast', source_event_id=f'broadcast:{broadcast.get("id")}:{tg_user_id}', decision=decision)
            return
        result = await safe_send(self.bot, method, chat_id=tg_user_id, retries=MAX_RETRIES + 1, **kwargs)
        if result.ok:
            await broadcasts_repo.mark_recipient_status(recipient_id, status="sent")
            await record_delivery_decision(client_tg_id=tg_user_id, notification_type='manual_broadcast', category='marketing', funnel_type='manual_broadcast', source_event_id=f'broadcast:{broadcast.get("id")}:{tg_user_id}', decision='allowed')
            return
        if result.skipped:
            await broadcasts_repo.mark_recipient_status(recipient_id, status="skipped", error_short=(result.error or "skipped")[:120])
            return
        await broadcasts_repo.mark_recipient_status(recipient_id, status="failed", error_short=(result.error or "send_failed")[:120])


sender_instance: BroadcastSender | None = None


def start_broadcast_sender(bot: Bot) -> BroadcastSender:
    global sender_instance
    sender_instance = BroadcastSender(bot)
    sender_instance.start()
    return sender_instance


async def stop_broadcast_sender() -> None:
    if sender_instance:
        await sender_instance.stop()
