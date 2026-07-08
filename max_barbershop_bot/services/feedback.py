"""Post-visit feedback flow ported from Telegram business rules."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.max_api.models import MaxButton, MaxInlineKeyboard
from max_barbershop_bot.max_api.sender import MaxMessageSender
from max_barbershop_bot.repositories.feedback import FeedbackAdminReply, FeedbackRepository, FeedbackRequest, FeedbackResponse
from max_barbershop_bot.repositories.platform_attribution import PlatformAttributionRepository
from max_barbershop_bot.repositories.staff_roles import StaffRolesRepository
from max_barbershop_bot.repositories.users import PLATFORM_MAX, User, UsersRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.company_time import normalize_branch_timezone, zoneinfo_or_default
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials, load_active_yclients_settings
from max_barbershop_bot.services.notifications import PLATFORM_MAX as NOTIFICATION_PLATFORM_MAX, send_business_notification

logger = logging.getLogger(__name__)

POST_VISIT_FEEDBACK_REQUEST = "post_visit_feedback_request"
POST_VISIT_FEEDBACK_NEGATIVE_ALERT = "post_visit_feedback_negative_alert"
_COMPLETED = {"done", "completed", "visit", "paid", "show"}
_NEGATIVE_THRESHOLD = 3
_DELAY = timedelta(hours=2)

REQUEST_TEXT = "Оцените, пожалуйста, ваш визит ⭐️"
POSITIVE_TEXT = "Спасибо за высокую оценку 😊\n\nСсылки на отзывы пока не настроены."
NEGATIVE_COMMENT_PROMPT = "Спасибо, что честно рассказали 🙏\n\nНапишите, пожалуйста, что пошло не так."
NEGATIVE_THANKS_TEXT = "Спасибо. Мы получили ваш комментарий и постараемся разобраться 🙏"
STALE_TEXT = "Спасибо, мы уже получили вашу оценку 🙏"
NON_TEXT_COMMENT_TEXT = "Пожалуйста, отправьте комментарий текстом."
FEEDBACK_ADMIN_NO_ACCESS_TEXT = "⛔ Нет доступа"
FEEDBACK_ADMIN_STALE_TEXT = "⚠️ Событие устарело."
FEEDBACK_ADMIN_REPLY_PROMPT = "Введите ответ клиенту:"
FEEDBACK_ADMIN_REPLY_CONFIRM_TEXT = "Отправить клиенту такой ответ?\n\n{text}"
FEEDBACK_ADMIN_REPLY_CLIENT_TEXT = "💬 Ответ от команды барбершопа:\n\n{text}"
FEEDBACK_ADMIN_REPLY_SUCCESS_TEXT = "✅ Ответ отправлен клиенту."
FEEDBACK_ADMIN_REPLY_TEST_SUCCESS_TEXT = "✅ Тестовый ответ отправлен."
FEEDBACK_ADMIN_REPLY_CANCELLED_TEXT = "Отменено"

@dataclass(frozen=True)
class DueFeedback:
    request: FeedbackRequest
    user: User
    record: dict[str, Any]
    visit_datetime: datetime


def feedback_rating_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([
        [MaxButton(text="⭐⭐⭐⭐⭐", payload="feedback:rate:5")],
        [MaxButton(text="⭐⭐⭐⭐", payload="feedback:rate:4")],
        [MaxButton(text="⭐⭐⭐", payload="feedback:rate:3")],
        [MaxButton(text="⭐⭐", payload="feedback:rate:2")],
        [MaxButton(text="⭐", payload="feedback:rate:1")],
    ])

async def send_due_feedback_requests(sender: MaxMessageSender, *, database_path: str, now: datetime | None = None, timezone_name: str | None = None) -> int:
    sent = 0
    for due in await get_due_feedback_requests(database_path=database_path, now=now, timezone_name=timezone_name):
        recipient_type, recipient_id = _recipient(due.user)
        if recipient_id is None:
            continue
        history = await send_business_notification(
            sender,
            database_path=database_path,
            platform=NOTIFICATION_PLATFORM_MAX,
            platform_user_id=due.user.platform_user_id,
            max_user_id=due.user.max_user_id,
            chat_id=due.user.chat_id,
            yclients_record_id=due.request.yclients_record_id,
            yclients_client_id=due.request.yclients_client_id,
            notification_type=POST_VISIT_FEEDBACK_REQUEST,
            scheduled_for=due.visit_datetime.isoformat(),
            text=REQUEST_TEXT,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            keyboard=feedback_rating_keyboard(),
            metadata={"label": "оценка после визита"},
        )
        sent += 1 if history is not None else 0
        _log_feedback_diagnostic(platform_user_id=due.user.platform_user_id, yclients_record_id=due.request.yclients_record_id, request_sent=history is not None, delivery_status=history.status if history else None, blocked_or_stopped=bool(history and (history.is_blocked or history.is_stopped)))
    return sent

async def get_due_feedback_requests(*, database_path: str, now: datetime | None = None, timezone_name: str | None = None, limit: int = 200) -> list[DueFeedback]:
    settings = load_active_yclients_settings(YClientsSettingsRepository(database_path), operation="get_due_feedback_requests")
    if not has_required_yclients_credentials(settings):
        return []
    tz_name = normalize_branch_timezone(timezone_name or settings.branch_timezone, flow="feedback", operation="get_due_feedback_requests")
    tz = zoneinfo_or_default(tz_name, flow="feedback", operation="get_due_feedback_requests")
    now_local = (now or datetime.now(UTC)).astimezone(tz)
    repo = FeedbackRepository(database_path)
    users = UsersRepository(database_path)
    due: list[DueFeedback] = []
    async with build_yclients_client_from_active_settings(settings) as client:
        service = YClientsServiceLayer(client, company_id=settings.company_id)
        for attribution in PlatformAttributionRepository(database_path).list_with_yclients_record_ids(limit=limit):
            user = users.find_by_platform_user_id(attribution.platform_user_id, platform=attribution.platform)
            if user is None or not user.notifications_enabled or not attribution.yclients_record_id:
                continue
            if repo.get_request(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id) or repo.has_response(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id):
                _log_feedback_diagnostic(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id, feedback_request_existing=True, feedback_response_existing=repo.has_response(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id))
                continue
            try:
                payload = await service.get_booking_details(company_id=settings.company_id, yclients_record_id=attribution.yclients_record_id)
            except Exception:
                logger.warning("MAX post-visit feedback diagnostic: %s", {"platform_user_id_present": True, "yclients_record_id_present": True, "yclients_error_category": "fetch_failed"}, exc_info=True)
                continue
            record = _extract_record(payload)
            completed = _record_is_completed(record)
            visit_dt = _record_datetime(record, tz_name)
            visit_finished = bool(visit_dt and visit_dt + _DELAY <= now_local)
            _log_feedback_diagnostic(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id, record_status=_record_status(record), visit_finished=visit_finished)
            if not completed or not visit_finished or visit_dt is None:
                continue
            request = repo.create_request_if_missing(platform_user_id=user.platform_user_id, yclients_record_id=attribution.yclients_record_id, yclients_client_id=attribution.yclients_client_id or user.yclients_client_id)
            if request is not None:
                due.append(DueFeedback(request=request, user=user, record=record, visit_datetime=visit_dt))
    return due

def save_rating(database_path: str, *, platform_user_id: str, rating: int) -> tuple[FeedbackResponse | None, bool]:
    if rating < 1 or rating > 5:
        return None, False
    repo = FeedbackRepository(database_path)
    request = repo.find_latest_waiting(platform_user_id=platform_user_id)
    if request is None or repo.has_response(platform_user_id=platform_user_id, yclients_record_id=request.yclients_record_id):
        return None, False
    negative = rating <= _NEGATIVE_THRESHOLD
    response = repo.save_rating_once(platform_user_id=platform_user_id, yclients_record_id=request.yclients_record_id, rating=rating, is_negative=negative)
    _log_feedback_diagnostic(platform_user_id=platform_user_id, yclients_record_id=request.yclients_record_id, rating=rating, negative=negative)
    return response, negative

def save_negative_comment(database_path: str, *, platform_user_id: str, comment: str) -> FeedbackResponse | None:
    repo = FeedbackRepository(database_path)
    request = repo.find_latest_waiting(platform_user_id=platform_user_id)
    if request is None:
        return None
    return repo.save_comment(platform_user_id=platform_user_id, yclients_record_id=request.yclients_record_id, comment=comment)

async def notify_negative_feedback(sender: MaxMessageSender, *, database_path: str, response: FeedbackResponse) -> None:
    repo = FeedbackRepository(database_path)
    fresh_response = repo.get_response_by_id(response.id)
    if fresh_response is None or fresh_response.admin_notified_at:
        return
    response = fresh_response
    targets = []
    users = UsersRepository(database_path)
    for staff in StaffRolesRepository(database_path).list_staff():
        if staff.role in {"developer", "admin", "manager"}:
            user = users.find_by_platform_user_id(staff.platform_user_id, platform=staff.platform)
            if user is not None and user.notifications_enabled:
                targets.append(user)
    text = render_post_visit_admin_alert(database_path=database_path, response=response)
    keyboard = feedback_admin_keyboard(response.id, is_test=is_test_feedback_response(database_path=database_path, response=response))
    sent_any = False
    for user in {u.platform_user_id: u for u in targets}.values():
        recipient_type, recipient_id = _recipient(user)
        if recipient_id is None:
            continue
        if recipient_type == "chat":
            result = await sender.send_to_chat(recipient_id, text, keyboard=keyboard, metadata={"rating": response.rating, "feedback_response_id": response.id})
        else:
            result = await sender.send_to_user(recipient_id, text, keyboard=keyboard, metadata={"rating": response.rating, "feedback_response_id": response.id})
        sent_any = sent_any or result.ok
    if sent_any:
        repo.mark_admin_notified(platform_user_id=response.platform_user_id, yclients_record_id=response.yclients_record_id)
    _log_feedback_diagnostic(platform_user_id=response.platform_user_id, yclients_record_id=response.yclients_record_id, rating=response.rating, negative=True, admin_alert_sent=sent_any)

def feedback_admin_keyboard(response_id: int, *, is_test: bool = False) -> MaxInlineKeyboard:
    back_payload = "broadcast:dev_tests:root" if is_test else "broadcast:history:root"
    return MaxInlineKeyboard.from_rows([
        [MaxButton(text="💬 Ответить клиенту", payload=f"feedback_admin_reply:{response_id}")],
        [MaxButton(text="⬅️ Назад", payload=back_payload)],
        [MaxButton(text="🏠 Главное меню", payload="nav:home")],
    ])

def feedback_admin_reply_confirm_keyboard() -> MaxInlineKeyboard:
    return MaxInlineKeyboard.from_rows([
        [MaxButton(text="✅ Отправить", payload="feedback_admin_reply_confirm:send")],
        [MaxButton(text="✏️ Изменить", payload="feedback_admin_reply_confirm:edit")],
        [MaxButton(text="⬅️ Назад", payload="feedback_admin_reply_confirm:back")],
        [MaxButton(text="🏠 Главное меню", payload="nav:home")],
    ])

def render_post_visit_admin_alert(*, database_path: str, response: FeedbackResponse) -> str:
    context = FeedbackRepository(database_path).get_response_context(response.id)
    comment = response.comment or "—"
    if is_test_feedback_response(database_path=database_path, response=response):
        return (
            f"🚨 Тестовая негативная оценка\n\n"
            f"Оценка: {response.rating}/5\n"
            f"Клиент: Тестовый клиент\n"
            f"Телефон: {_mask_phone('+7 999 000-00-00')}\n"
            f"Услуга: Тестовая стрижка\n"
            f"Мастер: Тестовый мастер\n"
            f"Дата визита: тестовый визит\n\n"
            f"Комментарий клиента:\n{comment}\n\n"
            f"🧪 Это тестовое событие. Реальные клиенты и записи не затронуты."
        )
    return (
        f"🚨 Низкая оценка после визита\n\nОценка: {response.rating}/5\n"
        f"Клиент: {_client_name(context)}\nТелефон: {_mask_phone(str(context.get('phone') or ''))}\n"
        f"Услуга: {context.get('service_name') or '—'}\nМастер: {context.get('staff_name') or '—'}\n"
        f"Дата визита: {_visit_datetime(context)}\n\nКомментарий клиента:\n{comment}"
    )

def is_test_feedback_response(*, database_path: str, response: FeedbackResponse) -> bool:
    context = FeedbackRepository(database_path).get_response_context(response.id)
    return bool(context.get("is_test") and context.get("source") == "dev_test")

def is_feedback_admin(database_path: str, platform_user_id: str | None) -> bool:
    if not platform_user_id:
        return False
    return StaffRolesRepository(database_path).get_highest_role(platform_user_id) in {"developer", "admin", "manager"}

def get_feedback_response(database_path: str, response_id: int) -> FeedbackResponse | None:
    response = FeedbackRepository(database_path).get_response_by_id(response_id)
    if response is None or response.status not in {"open", "negative_comment_received"}:
        return None
    return response

def save_feedback_admin_reply(database_path: str, *, response_id: int, admin_platform_user_id: str, text: str) -> FeedbackAdminReply | None:
    return FeedbackRepository(database_path).save_admin_reply(response_id=response_id, admin_platform_user_id=admin_platform_user_id, text=text)

def close_feedback_response(database_path: str, *, response_id: int, admin_platform_user_id: str) -> FeedbackResponse | None:
    return FeedbackRepository(database_path).close_response(response_id=response_id, admin_platform_user_id=admin_platform_user_id)

def client_recipient(database_path: str, response: FeedbackResponse) -> tuple[str, str | None]:
    user = UsersRepository(database_path).find_by_platform_user_id(response.platform_user_id, platform=response.platform)
    if user is None:
        return "user", response.platform_user_id
    return _recipient(user)

def _client_name(context: dict[str, Any]) -> str:
    parts = [str(context.get("first_name") or "").strip(), str(context.get("last_name") or "").strip()]
    full_name = " ".join(part for part in parts if part).strip()
    return str(context.get("display_name") or full_name or context.get("username") or "—")

def _visit_datetime(context: dict[str, Any]) -> str:
    return str(context.get("scheduled_for") or context.get("completed_at") or context.get("requested_at") or "—")

def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return "—"
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"+{'*' * max(0, len(digits) - 4)}{digits[-4:]}"

def _recipient(user: User) -> tuple[str, str | None]:
    if user.chat_id: return "chat", user.chat_id
    return "user", user.max_user_id or user.platform_user_id

def _extract_record(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict): return data
        if isinstance(data, list) and data and isinstance(data[0], dict): return data[0]
        return payload
    return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}

def _record_is_completed(record: dict[str, Any]) -> bool:
    attendance = record.get("attendance", record.get("visit_attendance"))
    if attendance is not None:
        return str(attendance).strip() == "1"
    return _record_status(record).lower() in _COMPLETED

def _record_status(record: dict[str, Any]) -> str:
    return str(record.get("status") or record.get("record_status") or record.get("state") or "").strip()

def _record_datetime(record: dict[str, Any], timezone_name: str) -> datetime | None:
    raw = str(record.get("datetime") or record.get("date") or "").strip()
    if not raw: return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = zoneinfo_or_default(timezone_name, flow="feedback", operation="_record_datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)

def _log_feedback_diagnostic(**fields: Any) -> None:
    safe = {k: v for k, v in fields.items() if k in {"record_status", "visit_finished", "feedback_request_existing", "feedback_response_existing", "request_sent", "rating", "negative", "admin_alert_sent", "delivery_status", "blocked_or_stopped", "yclients_error_category", "trace_id"}}
    safe["platform_user_id_present"] = bool(fields.get("platform_user_id"))
    safe["yclients_record_id_present"] = bool(fields.get("yclients_record_id"))
    logger.info("MAX post-visit feedback diagnostic: %s", safe)
