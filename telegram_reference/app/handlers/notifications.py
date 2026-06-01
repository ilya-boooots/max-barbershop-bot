from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.navigation import clear_state_preserving_navigation, push_screen, render_main_menu
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_MANAGER, has_any_role, require_roles, resolve_role
from app.core.ui_texts import BACK_BTN, BROADCAST_BTN, NOTIFICATIONS_BTN
from app.integrations.yclients import YClientsError, build_yclients_client
from app.integrations.yclients.endpoints import get_client_details, get_company, search_clients
from app.repositories import broadcasts as broadcasts_repo
from app.repositories.automation_settings import get_setting, upsert_setting
from app.repositories.notification_test_events import cleanup_test_events, create_test_event, mark_failed, mark_sent
from app.repositories.post_visit_feedback_events import create_event, get_event, set_status, cleanup_dev_test_events as cleanup_post_visit_dev_test_events
from app.repositories.cancellation_recovery_events import get_event as get_cancel_event, set_status as set_cancel_status
from app.services.cancellation_recovery import create_cancellation_event_from_row, _recovery_kb
from app.repositories.staff_roles import list_staff
from app.repositories.staff_action_logs import log_staff_action
from app.repositories.yclients_settings import get_yclients_settings
from app.repositories.users import get_notifications_enabled, set_notifications_enabled, upsert_telegram_user, get_user_by_tg_id
from app.services.company_time import resolve_company_timezone
from app.services.lost_clients import run_lost_clients_scan
from app.repositories.lost_client_events import create_event as create_lost_client_event, mark_status as mark_lost_status, get_recent_stats, get_event as get_lost_event
from app.repositories.birthday_funnel_events import create_event as create_birthday_event, get_event as get_birthday_event, mark_status as mark_birthday_status
from app.services.birthday_funnel import BIRTHDAY_BUTTON_BOOK, BIRTHDAY_MESSAGE_TEXT, build_birthday_booking_keyboard, run_birthday_scan
from app.services.repeat_visit import BUTTON_CB_PREFIX, run_repeat_visit_scan, select_repeat_visit_text
from app.services.notification_history import get_notification_history, map_event_status_to_label, map_error_summary
from app.services.client_segments import segment_service, SEGMENTS, DESCRIPTIONS
from app.db.sqlite import fetchall
from app.repositories.repeat_visit_events import create_event as create_repeat_event, mark_status as mark_repeat_status, get_event as get_repeat_event
from app.handlers.booking_flow import open_booking_from_notification
from app.services.effectiveness import build_metrics
from app.repositories.booking_reminder_events import cleanup_dev_test_events as cleanup_booking_reminder_dev_test_events
from app.utils.phone import build_phone_match_keys, normalize_phone
from app.utils.staff import display_name, role_label

router = Router()
logger = logging.getLogger(__name__)
MAX_TEXT_LEN = 1024
MAX_CAPTION_LEN = 1024
MAX_MESSAGE_LEN = 4096
DEVELOPER_TG_ID = 378881880


def _role_text(role: str | None) -> str:
    return role_label(role).split(" ", 1)[-1]




def _automation_title(key: str | None) -> str:
    return {
        'post_visit_review': 'оценка после визита',
        'cancellation_return': 'возврат после отмены',
        'lost_clients': 'потерянные клиенты',
        'birthday': 'день рождения',
        'repeat_visit': 'повторный визит',
        'anti_spam': 'антиспам',
        'review_links': 'ссылки на отзывы',
        'quiet_hours': 'рабочее время и тихие часы',
    }.get(key or '', 'уведомления')

async def _log_notification_action(actor_tg_id: int, action_type: str, human_tail: str, **metadata):
    actor = await get_user_by_tg_id(actor_tg_id)
    actor_name = display_name(actor or {})
    actor_role = (actor or {}).get("role") or ("developer" if actor_tg_id == DEVELOPER_TG_ID else None)
    await log_staff_action(
        actor_tg_id=actor_tg_id,
        actor_name=actor_name,
        actor_role=actor_role,
        action_type=action_type,
        human_text=f"{_role_text(actor_role)} {actor_name} {human_tail}.",
        metadata={k: v for k, v in metadata.items() if v not in (None, "")},
    )

class BroadcastStates(StatesGroup):
    waiting_segment = State(); waiting_text = State(); waiting_photo_choice = State(); waiting_photo_upload = State(); waiting_preview = State()

class AutomationEditStates(StatesGroup):
    waiting_input = State()




class PostVisitFeedbackStates(StatesGroup):
    waiting_negative_comment = State()
    waiting_admin_reply = State()
    waiting_admin_reply_confirm = State()


class NotificationHistoryStates(StatesGroup):
    waiting_client_query = State()

# ... keep notifications functions minimally

def _validate_callback_data(callback_data: str) -> str:
    if not isinstance(callback_data, str) or len(callback_data.encode('utf-8')) > 64:
        raise ValueError('callback_data must be a string up to 64 bytes')
    return callback_data


def _with_nav(rows, *, back_callback: str = 'nav:back'):
    rows.append([InlineKeyboardButton(text=BACK_BTN, callback_data=_validate_callback_data(back_callback))])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data=_validate_callback_data('nav:home'))])
    return InlineKeyboardMarkup(inline_keyboard=rows)

WHITE_GREEN_INFO_TEXT = (
    "⚪ Белые уведомления — сервисные сообщения по записи.\n"
    "Они отправляются всегда: подтверждение записи, перенос, отмена, напоминания, ответы администратора.\n\n"
    "🟢 Зелёные уведомления — акции, автоворонки и возврат клиентов.\n"
    "Они проходят через антиспам, отписку и рабочее время филиала."
)


def white_green_info_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK_BTN, callback_data='broadcast:settings:anti_spam')],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')],
        ]
    )


def notifications_kb(): return _with_nav([[InlineKeyboardButton(text='✅ Включить', callback_data='notifications:on')],[InlineKeyboardButton(text='❌ Выключить', callback_data='notifications:off')]])

def broadcast_root_kb(user_id: int):
    rows = [
        [InlineKeyboardButton(text='✉️ Разовая рассылка', callback_data='broadcast:section:one_time')],
        [InlineKeyboardButton(text='🎯 Сегменты клиентов', callback_data='broadcast:section:segments')],
        [InlineKeyboardButton(text='😔 Потерянные клиенты', callback_data='broadcast:section:lost_clients')],
        [InlineKeyboardButton(text='📊 Эффективность', callback_data='broadcast:section:efficiency')],
        [InlineKeyboardButton(text='📜 История уведомлений', callback_data='broadcast:history:root')],
    ]
    if user_id == DEVELOPER_TG_ID:
        rows.append([InlineKeyboardButton(text='🧪 Тест уведомлений', callback_data='broadcast:dev_tests:root')])
    return _with_nav(rows)

def one_time_audience_kb():
    return _with_nav([[InlineKeyboardButton(text='👥 Все клиенты', callback_data='broadcast:aud:all_clients')],[InlineKeyboardButton(text='🔥 Активные за 30 дней', callback_data='broadcast:aud:active_30')],[InlineKeyboardButton(text='😴 Потерянные 30 дней', callback_data='broadcast:aud:lost_30')],[InlineKeyboardButton(text='😴 Потерянные 60 дней', callback_data='broadcast:aud:lost_60')],[InlineKeyboardButton(text='😴 Потерянные 90 дней', callback_data='broadcast:aud:lost_90')],[InlineKeyboardButton(text='📅 Без будущей записи', callback_data='broadcast:aud:no_future_booking')],[InlineKeyboardButton(text='🧪 Отправить себе', callback_data='broadcast:aud:send_to_self')]])

def segment_root_kb():
    return _with_nav([
        [InlineKeyboardButton(text='👥 Все клиенты', callback_data='broadcast:segments:all_clients')],
        [InlineKeyboardButton(text='🔥 Активные за 30 дней', callback_data='broadcast:segments:active_30')],
        [InlineKeyboardButton(text='😴 Не были 30 дней', callback_data='broadcast:segments:inactive_30')],
        [InlineKeyboardButton(text='😴 Не были 60 дней', callback_data='broadcast:segments:inactive_60')],
        [InlineKeyboardButton(text='😴 Не были 90 дней', callback_data='broadcast:segments:inactive_90')],
        [InlineKeyboardButton(text='📅 Без будущей записи', callback_data='broadcast:segments:no_future_booking')],
        [InlineKeyboardButton(text='❌ Отменили запись', callback_data='broadcast:segments:cancelled_30')],
        [InlineKeyboardButton(text='💈 По мастеру', callback_data='broadcast:segments:by_master:picker')],
        [InlineKeyboardButton(text='✂️ По услуге', callback_data='broadcast:segments:by_service_category:picker')],
        [InlineKeyboardButton(text='🎂 День рождения скоро', callback_data='broadcast:segments:birthday_soon')],
        [InlineKeyboardButton(text='🔄 Обновить сегменты', callback_data='broadcast:segments:refresh')],
    ])

def photo_choice_kb(): return _with_nav([[InlineKeyboardButton(text='📷 Добавить фото', callback_data='broadcast:photo:add')],[InlineKeyboardButton(text='➡️ Без фото', callback_data='broadcast:photo:skip')]])

def preview_kb(has_photo: bool):
    return _with_nav([[InlineKeyboardButton(text='✅ Отправить', callback_data='broadcast:send:confirm')],[InlineKeyboardButton(text='✏️ Изменить текст', callback_data='broadcast:edit:text')],[InlineKeyboardButton(text='📷 Изменить фото' if has_photo else '📷 Добавить фото', callback_data='broadcast:edit:photo')]])

def broadcast_report_kb():
    return _with_nav([
        [InlineKeyboardButton(text='📜 История рассылок', callback_data='broadcast:history')],
        [InlineKeyboardButton(text='✉️ Новая рассылка', callback_data='broadcast:section:one_time')],
    ])

async def _can_open_broadcast(user_id:int)->bool: return await has_any_role(user_id,{ROLE_DEVELOPER,ROLE_ADMIN,ROLE_MANAGER})

async def _can_open_effectiveness(user_id:int)->bool: return await has_any_role(user_id,{ROLE_DEVELOPER,ROLE_MANAGER})
async def _deny_broadcast_access(m):
    txt='⛔ Раздел недоступен.'; logger.warning('broadcast_access_denied tg_id=%s', m.from_user.id)
    if isinstance(m, CallbackQuery):
        if m.message: await m.message.answer(txt)
        await m.answer(); return
    await m.answer(txt)





def efficiency_kb(days:int=30):
    return _with_nav([
        [InlineKeyboardButton(text='📅 7 дней', callback_data='broadcast:eff:period:7'), InlineKeyboardButton(text='📅 30 дней', callback_data='broadcast:eff:period:30'), InlineKeyboardButton(text='📅 90 дней', callback_data='broadcast:eff:period:90')],
        [InlineKeyboardButton(text='📊 По воронкам', callback_data='broadcast:eff:funnels')],
        [InlineKeyboardButton(text='💰 Выручка', callback_data='broadcast:eff:revenue')],
        [InlineKeyboardButton(text='⭐️ Репутация', callback_data='broadcast:eff:reputation')],
        [InlineKeyboardButton(text='📜 История уведомлений', callback_data='broadcast:history:root')],
        [InlineKeyboardButton(text='🔄 Обновить', callback_data=f'broadcast:eff:period:{days}')],
    ])


async def _render_eff(callback: CallbackQuery, days: int):
    if not await _can_open_effectiveness(callback.from_user.id):
        logger.warning('effectiveness_access_denied by=%s', callback.from_user.id)
        await callback.message.answer('⛔ Раздел недоступен.')
        return await callback.answer()
    try:
        m = await build_metrics(days, 'Europe/Moscow')
    except Exception:
        logger.exception('effectiveness_calc_failed')
        await callback.message.edit_text('⚠️ Не удалось обновить эффективность. Попробуйте позже.', reply_markup=efficiency_kb(days))
        return await callback.answer()
    conv = f"{m['conversion']:.1f}%" if m['conversion'] is not None else '—'
    text = f"📊 Эффективность\n\nПериод: последние {days} дней\n\nОтправлено уведомлений: {m['sent']}\nДоставлено: {m['delivered']}\nОшибок: {m['errors']}\nКликов по «Записаться»: {m['clicks']}\nСоздано записей после уведомлений: {m['bookings']}\nВозвращённых клиентов: {m['returned_clients']}\nПримерная выручка: ~{int(m['revenue'])} ₽\nПлохих отзывов перехвачено: {m['bad_reviews']}\nКонверсия в запись: {conv}\nСредний чек: ~{int(m['avg_check'])} ₽\nЛучший сценарий: —\nСамая доходная воронка: —"
    await callback.message.edit_text(text, reply_markup=efficiency_kb(days))
    await callback.answer()
def automation_root_kb():
    return _with_nav([[InlineKeyboardButton(text='⭐ Оценка после визита', callback_data='broadcast:settings:post_visit_review')],[InlineKeyboardButton(text='❌ Возврат после отмены', callback_data='broadcast:settings:cancellation_return')],[InlineKeyboardButton(text='😔 Потерянные клиенты', callback_data='broadcast:settings:lost_clients')],[InlineKeyboardButton(text='🎂 День рождения', callback_data='broadcast:settings:birthday')],[InlineKeyboardButton(text='🔁 Повторный визит', callback_data='broadcast:settings:repeat_visit')],[InlineKeyboardButton(text='🔕 Антиспам', callback_data='broadcast:settings:anti_spam')],[InlineKeyboardButton(text='🔗 Ссылки на отзывы', callback_data='broadcast:settings:review_links')],[InlineKeyboardButton(text='⏰ Рабочее время / тихие часы', callback_data='broadcast:settings:quiet_hours')]])



def notification_history_root_kb():
    rows = [[InlineKeyboardButton(text='📋 Все уведомления', callback_data='broadcast:history:list:all:1')],[InlineKeyboardButton(text='✉️ Ручные рассылки', callback_data='broadcast:history:list:manual_broadcast:1')],[InlineKeyboardButton(text='⭐️ Оценка после визита', callback_data='broadcast:history:list:post_visit_rating:1')],[InlineKeyboardButton(text='❌ Возврат после отмены', callback_data='broadcast:history:list:cancellation_recovery:1')],[InlineKeyboardButton(text='😔 Потерянные клиенты', callback_data='broadcast:history:list:lost_client:1')],[InlineKeyboardButton(text='🎂 День рождения', callback_data='broadcast:history:list:birthday:1')],[InlineKeyboardButton(text='🔁 Повторный визит', callback_data='broadcast:history:list:repeat_visit:1')],[InlineKeyboardButton(text='🔎 Поиск по клиенту', callback_data='broadcast:history:search')]]
    rows.append([InlineKeyboardButton(text=BACK_BTN, callback_data='broadcast:root')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_list_kb(filter_key:str,page:int,has_next:bool):
    rows=[]
    if page>1: rows.append([InlineKeyboardButton(text='⬅️ Предыдущая', callback_data=f'broadcast:history:list:{filter_key}:{page-1}')])
    if has_next: rows.append([InlineKeyboardButton(text='➡️ Далее', callback_data=f'broadcast:history:list:{filter_key}:{page+1}')])
    rows.append([InlineKeyboardButton(text=BACK_BTN, callback_data='broadcast:history:root')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_search_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=BACK_BTN, callback_data='broadcast:history:root')], [InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')]])


def _history_filter_title(key:str)->str:
    return {"all":"📜 Все уведомления","manual_broadcast":"✉️ Ручные рассылки","post_visit_rating":"⭐️ Оценка после визита","cancellation_recovery":"❌ Возврат после отмены","lost_client":"😔 Потерянные клиенты","birthday":"🎂 День рождения","repeat_visit":"🔁 Повторный визит"}.get(key,"📜 История уведомлений")


async def _history_default_timezone() -> str:
    tz = await _safe_branch_timezone()
    return tz if '/' in tz else 'Europe/Moscow'


def _format_history_row(row: dict, index: int) -> list[str]:
    lines = [f"\n{index}. {row.get('human_text') or 'Уведомление'}"]
    status = map_event_status_to_label(str(row.get('status') or ''), bool(row.get('is_test')))
    lines.append(f"Статус: {status}")
    err = map_error_summary(row.get('error_summary'), str(row.get('status') or ''))
    if err:
        lines.append(f"Причина: {err}")
    return lines



def _history_extract_rows(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return [payload] if payload else []


def _history_client_id(item: dict) -> str:
    for key in ("id", "client_id"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _history_client_name(item: dict) -> str:
    for key in ("name", "fullname"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    first = str(item.get("first_name") or "").strip()
    last = str(item.get("last_name") or "").strip()
    return f"{first} {last}".strip() or "Клиент"


def _history_phone_values(item: dict) -> list[str]:
    values: list[str] = []
    for key in ("phone", "phones", "tel"):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            for part in raw:
                if isinstance(part, str):
                    values.append(part)
                elif isinstance(part, dict):
                    phone = part.get("phone") or part.get("number")
                    if phone:
                        values.append(str(phone))
        elif isinstance(raw, dict):
            phone = raw.get("phone") or raw.get("number")
            if phone:
                values.append(str(phone))
    return values


def _history_client_phone(item: dict) -> str:
    phones = _history_phone_values(item)
    return phones[0].strip() if phones else ""


def _history_phone_keys(value: str | None) -> set[str]:
    if not value:
        return set()
    bundle = normalize_phone(value, default_region="RU")
    keys = set(build_phone_match_keys(bundle))
    if bundle.digits_only:
        keys.add(bundle.digits_only)
    return {key for key in keys if key}


def _format_ru_phone(value: str | None) -> str:
    bundle = normalize_phone(str(value or ""), default_region="RU")
    digits = bundle.ru_11_with_7 or bundle.digits_only
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"
    return str(value or "").strip() or "—"


def _history_client_has_matching_phone(client_item: dict, expected_keys: set[str]) -> bool:
    row_keys: set[str] = set()
    for raw_phone in _history_phone_values(client_item):
        row_keys.update(_history_phone_keys(raw_phone))
    return bool(row_keys & expected_keys)


async def _load_yclients_client_details(client, company_id: str, client_id: str) -> dict | None:
    if not client_id:
        return None
    payload = await get_client_details(client, company_id=company_id, client_id=client_id)
    for item in _history_extract_rows(payload):
        if _history_client_id(item):
            return item
    return None


async def _find_history_yclients_client(query: str) -> dict | None:
    digits = re.sub(r"\D", "", query or "")
    client, company_id = await build_yclients_client()
    try:
        if digits:
            expected_keys = _history_phone_keys(query)
            if expected_keys:
                candidates: dict[str, dict] = {}
                for key in sorted(expected_keys):
                    payload = await search_clients(client, company_id=company_id, query=key, page=1, count=50, by_phone=True)
                    for item in _history_extract_rows(payload):
                        client_id = _history_client_id(item)
                        if client_id:
                            candidates[client_id] = item
                phone_matches = [item for item in candidates.values() if _history_client_has_matching_phone(item, expected_keys)]
                if len(phone_matches) == 1:
                    details = await _load_yclients_client_details(client, company_id, _history_client_id(phone_matches[0]))
                    return details or phone_matches[0]
                if not phone_matches and len(candidates) == 1:
                    only = next(iter(candidates.values()))
                    details = await _load_yclients_client_details(client, company_id, _history_client_id(only))
                    return details or only

            mapped_user = await get_user_by_tg_id(int(digits)) if digits.isdigit() else None
            mapped_client_id = str((mapped_user or {}).get("yclients_client_id") or "").strip()
            if mapped_client_id:
                details = await _load_yclients_client_details(client, company_id, mapped_client_id)
                if details:
                    return details

        if query:
            payload = await search_clients(client, company_id=company_id, query=query, page=1, count=10, by_name=True)
            rows = _history_extract_rows(payload)
            if rows:
                details = await _load_yclients_client_details(client, company_id, _history_client_id(rows[0]))
                return details or rows[0]
        return None
    finally:
        await client.close()


async def _find_history_telegram_mapping(yclients_client_id: str, phone: str | None) -> dict | None:
    conditions: list[str] = []
    params: list[object] = []
    if yclients_client_id:
        conditions.append("yclients_client_id = ?")
        params.append(yclients_client_id)
    phone_keys = _history_phone_keys(phone)
    if phone_keys:
        placeholders = ",".join("?" for _ in phone_keys)
        conditions.append(f"(phone IN ({placeholders}) OR phone_e164 IN ({placeholders}) OR phone_ru_7 IN ({placeholders}) OR phone_ru_8 IN ({placeholders}) OR phone_digits IN ({placeholders}))")
        keys = sorted(phone_keys)
        params.extend(keys * 5)
    if not conditions:
        return None
    rows = await fetchall(
        f"""
        SELECT user_id, username, yclients_client_id, phone, phone_e164, phone_ru_7, phone_ru_8
        FROM users
        WHERE user_id IS NOT NULL AND ({' OR '.join(conditions)})
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        tuple(params),
    )
    return dict(rows[0]) if rows else None


def _history_row_matches_client(row: dict, yclients_client_id: str, phone_keys: set[str]) -> bool:
    row_client_id = str(row.get("yclients_client_id") or "").strip()
    if yclients_client_id and row_client_id == yclients_client_id:
        return True
    if phone_keys:
        row_keys = _history_phone_keys(str(row.get("client_phone") or ""))
        if row_keys & phone_keys:
            return True
    return False

def _url_like(raw: str) -> bool:
    candidate = raw.strip()
    parsed = urlparse(candidate if '://' in candidate else f'https://{candidate}')
    return bool(parsed.netloc and '.' in parsed.netloc and not any(ch.isspace() for ch in candidate))

@router.message(F.text==NOTIFICATIONS_BTN)
async def open_notifications(message: Message, state: FSMContext):
    await upsert_telegram_user(tg_id=message.from_user.id, username=message.from_user.username, name=message.from_user.full_name)
    await push_screen(state, "notifications")
    enabled = await get_notifications_enabled(message.from_user.id)
    await message.answer(f"🔔 Уведомления\n\nТекущий статус: {'✅ Включены' if enabled else '❌ Выключены'}", reply_markup=notifications_kb())
@router.callback_query(F.data.in_({'notifications:on','notifications:off'}))
async def set_notifications(callback: CallbackQuery):
    enabled = callback.data == "notifications:on"
    await set_notifications_enabled(callback.from_user.id, enabled)
    if callback.message:
        await callback.message.edit_text(f"🔔 Уведомления обновлены\n\nТекущий статус: {'✅ Включены' if enabled else '❌ Выключены'}", reply_markup=notifications_kb())
    await callback.answer("Сохранено ✅")

@router.callback_query(F.data == 'broadcast:root')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def open_broadcast_callback(callback: CallbackQuery, state: FSMContext):
    await push_screen(state, 'broadcast_root')
    await callback.message.edit_text('📣 Рассылка\n\nВыберите раздел 👇', reply_markup=broadcast_root_kb(callback.from_user.id))
    await callback.answer()


@router.message(F.text==BROADCAST_BTN)
async def open_broadcast(message:Message,state:FSMContext):
    if not await _can_open_broadcast(message.from_user.id): return await _deny_broadcast_access(message)
    logger.info("broadcast_section_opened user_id=%s", message.from_user.id)
    await push_screen(state,'broadcast_root'); await message.answer('📣 Рассылка\n\nВыберите раздел 👇',reply_markup=broadcast_root_kb(message.from_user.id))




def _bool_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _shorten(value: object, limit: int = 220) -> str:
    text = str(value) if value is not None else ""
    return text if len(text) <= limit else f"{text[:limit-1]}…"


def _render_service_category_diag(*, callback_data: str, company_id: object, token_present: bool, exc: Exception) -> str:
    tb_entries = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    tb_tail = traceback.format_list(tb_entries)[-5:]
    fail_frame = tb_entries[-1] if tb_entries else None
    fail_location = (
        f"{fail_frame.filename}:{fail_frame.name}:{fail_frame.lineno}"
        if fail_frame
        else "n/a"
    )
    return "\n".join([
        "🧪 Диагностика YClients категорий",
        "",
        "1) Handler",
        f"- file/function: app/handlers/notifications.py:open_service_category_segment_picker",
        f"- callback_data: {callback_data}",
        "",
        "2) Config",
        f"- company_id: {company_id}",
        f"- yclients_token_present: {_bool_yes_no(token_present)}",
        "",
        "3) Endpoint",
        f"- function: segment_service.list_service_categories -> endpoints.list_service_categories",
        f"- endpoint_path: /api/v1/service_categories/{{company_id}}",
        f"- method: GET",
        "",
        "4) Request",
        "- params/body keys: none",
        "",
        "5) Response / exception",
        "- http_status: n/a (exception before successful parse/return)",
        "- response_top_type: n/a",
        "- response_top_keys: n/a",
        "- response_list_length: n/a",
        "- first_item_keys: n/a",
        "- category_related_keys_found: n/a",
        f"- exception_type: {type(exc).__name__}",
        f"- exception_message: {_shorten(exc)}",
        f"- failed_at: {fail_location}",
        "- traceback_last_5_lines:",
        *([f"  {line.rstrip()}" for line in tb_tail] or ["  n/a"]),
        "",
        "6) Result",
        "- categories_count_before_error: 0",
        "- categories_list_empty: yes",
        "- function_raised_exception: yes",
    ])[:3900]

def _developer_only(user_id: int) -> bool:
    return user_id == DEVELOPER_TG_ID


async def _deny_dev_only(callback: CallbackQuery):
    await callback.answer('⛔ Раздел доступен только разработчику.', show_alert=True)


def dev_tests_kb():
    rows = [
        [InlineKeyboardButton(text='⭐️ Тест оценки после визита', callback_data='broadcast:dev_tests:post_visit_review')],
        [InlineKeyboardButton(text='❌ Тест отмены записи', callback_data='broadcast:dev_tests:cancellation')],
        [InlineKeyboardButton(text='😔 Тест потерянного клиента 30 дней', callback_data='broadcast:dev_tests:lost_client_30')],
        [InlineKeyboardButton(text='😔 Тест потерянного клиента 60 дней', callback_data='broadcast:dev_tests:lost_client_60')],
        [InlineKeyboardButton(text='😔 Тест потерянного клиента 90 дней', callback_data='broadcast:dev_tests:lost_client_90')],
        [InlineKeyboardButton(text='🎂 Тест дня рождения', callback_data='broadcast:dev_tests:birthday')],
        [InlineKeyboardButton(text='🔁 Тест повторного визита', callback_data='broadcast:dev_tests:repeat_visit')],
        [InlineKeyboardButton(text='✅ Тест подтверждения записи (48ч+)', callback_data='broadcast:dev_tests:booking_confirm_2d')],
        [InlineKeyboardButton(text='⏰ Тест напоминания о записи (2ч)', callback_data='broadcast:dev_tests:booking_reminder_2h')],
        [InlineKeyboardButton(text='📣 Тест уведомления себе', callback_data='broadcast:dev_tests:self')],
        [InlineKeyboardButton(text='🧹 Очистить тестовые события', callback_data='broadcast:dev_tests:cleanup')],
    ]
    return _with_nav(rows)


@router.callback_query(F.data == 'broadcast:dev_tests:root')
async def open_dev_tests(callback: CallbackQuery, state: FSMContext):
    if not _developer_only(callback.from_user.id):
        return await _deny_dev_only(callback)
    await push_screen(state, 'broadcast_dev_tests')
    await callback.message.edit_text(
        '🧪 Тест уведомлений\n\nЗдесь можно безопасно проверить уведомления и автоворонки без ожидания часов и дней. Все тестовые события помечаются как dev/test и не затрагивают реальных клиентов.',
        reply_markup=dev_tests_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:dev_tests:'))
async def run_dev_test(callback: CallbackQuery, state: FSMContext):
    if not _developer_only(callback.from_user.id):
        return await _deny_dev_only(callback)
    key = callback.data.split(':')[-1]
    if key == 'root':
        return
    if key == 'cleanup':
        kb = _with_nav([[InlineKeyboardButton(text='✅ Очистить', callback_data='broadcast:dev_tests:cleanup_confirm')]])
        await callback.message.edit_text('🧹 Очистить тестовые события?\n\nБудут удалены только события с пометкой dev/test. Реальные уведомления и клиенты не будут затронуты.', reply_markup=kb)
        return await callback.answer()
    event_id = await create_test_event(event_type=key, target_tg_id=DEVELOPER_TG_ID, payload={'source': 'dev_test'})
    log_name_by_key = {
        'post_visit_review': 'dev_test_post_visit',
        'cancellation': 'dev_test_cancellation',
        'birthday': 'dev_test_birthday',
    }
    dev_log_name = log_name_by_key.get(key)
    if dev_log_name:
        logger.info('%s_started user_tg_id=%s event_id=%s callback_key=%s', dev_log_name, callback.from_user.id, event_id, key)
    try:
        if key == 'self':
            await callback.bot.send_message(DEVELOPER_TG_ID, '📣 Тестовое уведомление\n\nЭто тестовое сообщение от системы уведомлений FlowBots.\n\nЕсли вы видите это сообщение — базовая отправка работает ✅')
            await callback.message.answer('✅ Тестовое уведомление отправлено себе.')
        elif key == 'post_visit_review':
            feedback_event_id = await create_event({
                'yclients_record_id': f'dev-post-visit-{event_id}',
                'yclients_client_id': 'dev-test-client',
                'client_tg_id': DEVELOPER_TG_ID,
                'client_name': 'Тестовый клиент',
                'client_phone': '+7 999 000-00-00',
                'staff_id': 'dev-test-staff',
                'staff_name': 'Тестовый мастер',
                'service_id': 'dev-test-service',
                'service_name': 'Тестовая стрижка',
                'visit_datetime_utc': datetime.now(timezone.utc).isoformat(),
                'branch_timezone': 'Europe/Moscow',
                'source': 'dev_test',
                'is_test': True,
            })
            if not feedback_event_id:
                raise RuntimeError('failed to create dev post visit feedback event')
            rating_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'⭐️ {i}', callback_data=f'feedback_rate:{feedback_event_id}:{i}') for i in range(1, 6)]])
            await callback.bot.send_message(DEVELOPER_TG_ID, '⭐️ Как прошёл ваш визит?\n\nОцените, пожалуйста, от 1 до 5 ⭐️', reply_markup=rating_kb)
            logger.info('dev_test_post_visit_sent user_tg_id=%s event_id=%s feedback_event_id=%s source=dev_test', callback.from_user.id, event_id, feedback_event_id)
        elif key == 'cancellation':
            cancel_event_id = await create_cancellation_event_from_row(
                row={
                    "id": f"dev-cancel-{event_id}",
                    "client": {"id": "dev-test-client", "phone": "+70000000000"},
                    "staff": {"id": "dev-test-staff", "name": "Тестовый мастер"},
                    "services": [{"id": "dev-test-service", "title": "Тестовая стрижка"}],
                    "datetime": datetime.now(timezone.utc).isoformat(),
                },
                source='dev_test',
                is_test=True,
                force_tg_id=DEVELOPER_TG_ID,
            )
            if not cancel_event_id:
                raise RuntimeError('failed to create dev cancellation recovery event')
            settings = await get_setting('cancellation_return')
            text = settings.get('message_text') or "Видим, что вы отменили запись 😔\n\nМожем подобрать другое удобное время."
            await callback.bot.send_message(DEVELOPER_TG_ID, text, reply_markup=_recovery_kb(cancel_event_id))
            await set_cancel_status(cancel_event_id, 'sent', sent_at_utc=datetime.now(timezone.utc).isoformat())
            logger.info('dev_test_cancellation_sent user_tg_id=%s event_id=%s cancellation_event_id=%s source=dev_test', callback.from_user.id, event_id, cancel_event_id)
        elif key in {'lost_client_30', 'lost_client_60', 'lost_client_90', 'birthday', 'repeat_visit'}:
            text_map = {
                'lost_client_30': 'Давно вас не видели 😊\n\nСамое время обновить стрижку.',
                'lost_client_60': 'Похоже, вы давно не заглядывали к нам.\n\nПодберём удобное время?',
                'lost_client_90': 'Мы скучаем 😄\n\nДля вас есть специальное предложение на возвращение.',
                'birthday': BIRTHDAY_MESSAGE_TEXT,
                'repeat_visit': 'Пора обновить стрижку? 😊\n\nОбычно к этому времени форма уже начинает теряться.',
            }
            rows = [[InlineKeyboardButton(text='✂️ Записаться', callback_data='lost_clients:book:0')]]
            if key == 'birthday':
                birthday_event_id = await create_birthday_event(
                    yclients_client_id=str(DEVELOPER_TG_ID),
                    client_tg_id=DEVELOPER_TG_ID,
                    birth_date=datetime.now(timezone.utc).date().isoformat(),
                    birthday_year=int(event_id),
                    scheduled_send_at_utc=datetime.now(timezone.utc).isoformat(),
                    status='pending',
                    source='dev_test',
                    is_test=True,
                )
                rows = build_birthday_booking_keyboard(birthday_event_id).inline_keyboard
                await mark_birthday_status(birthday_event_id, 'sent', sent=True)
                logger.info(
                    'dev_test_birthday_sent user_tg_id=%s birthday_event_id=%s is_test=%s source=%s',
                    DEVELOPER_TG_ID,
                    birthday_event_id,
                    True,
                    'dev_test',
                )
            if key.startswith('lost_client_'):
                threshold = int(key.split('_')[-1])
                event_id = await create_lost_client_event(yclients_client_id=str(DEVELOPER_TG_ID), client_tg_id=DEVELOPER_TG_ID, threshold_days=threshold, segment_key=f'lost_{threshold}', has_future_booking=False, status='pending', source='dev_test', is_test=True)
                rows = [[InlineKeyboardButton(text='✂️ Записаться', callback_data=f'lost_clients:book:{event_id}')]]
            if key == 'repeat_visit':
                s = await get_setting('repeat_visit')
                selected_template_index, text = select_repeat_visit_text(s, user_tg_id=DEVELOPER_TG_ID)
                rv_event_id = await create_repeat_event(
                    yclients_client_id=str(DEVELOPER_TG_ID),
                    client_tg_id=DEVELOPER_TG_ID,
                    yclients_visit_id=f'dev-{event_id}',
                    yclients_service_id='dev-service',
                    service_name='Классическая стрижка',
                    delay_days=30,
                    selected_template_index=selected_template_index,
                    selected_template_text=text,
                    status='pending',
                    source='dev_test',
                    is_test=True,
                )
                rows = [[InlineKeyboardButton(text='✂️ Записаться', callback_data=f'{BUTTON_CB_PREFIX}{rv_event_id}')]]
                await callback.bot.send_message(DEVELOPER_TG_ID, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
                await mark_repeat_status(rv_event_id, 'sent', sent=True)
            else:
                await callback.bot.send_message(DEVELOPER_TG_ID, text_map[key], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        elif key in {'booking_confirm_2d', 'booking_reminder_2h'}:
            from app.repositories.booking_reminder_events import create_event as create_reminder_event, get_event
            from app.services.booking_reminders import process_due_events
            now_utc = datetime.now(timezone.utc)
            reminder_type = 'confirm_2d' if key == 'booking_confirm_2d' else 'reminder_2h'
            dev_record_id = f"dev-test-{'confirm-48h' if key == 'booking_confirm_2d' else '2h'}-{callback.from_user.id}-{int(time.time() * 1000)}-{uuid4().hex[:8]}"
            logger.info(
                "dev_test_booking_reminder_clicked actor_tg_id=%s yclients_record_id=%s reminder_type=%s",
                callback.from_user.id,
                dev_record_id,
                reminder_type,
            )
            reminder_event_id = await create_reminder_event(
                yclients_record_id=dev_record_id,
                yclients_client_id=str(DEVELOPER_TG_ID),
                client_tg_id=DEVELOPER_TG_ID,
                client_phone='+79990000000',
                company_id='dev_test',
                visit_datetime_utc=(now_utc + timedelta(days=3 if key == 'booking_confirm_2d' else 1)).isoformat(),
                branch_timezone='Europe/Moscow',
                reminder_type=reminder_type,
                status='pending',
                scheduled_at_utc=now_utc.isoformat(),
            )
            logger.info(
                "dev_test_booking_reminder_event_created actor_tg_id=%s event_id=%s yclients_record_id=%s reminder_type=%s",
                callback.from_user.id,
                reminder_event_id,
                dev_record_id,
                reminder_type,
            )
            before = await get_event(int(reminder_event_id))
            logger.info(
                "dev_test_booking_reminder_process_started actor_tg_id=%s event_id=%s yclients_record_id=%s reminder_type=%s status_before=%s",
                callback.from_user.id,
                reminder_event_id,
                dev_record_id,
                reminder_type,
                (before or {}).get("status"),
            )
            await process_due_events(callback.bot)
            after = await get_event(int(reminder_event_id))
            status_after = (after or {}).get("status")
            sent_at_utc = (after or {}).get("sent_at_utc")
            error_summary = (after or {}).get("error")
            logger.info(
                "dev_test_booking_reminder_process_finished actor_tg_id=%s event_id=%s yclients_record_id=%s reminder_type=%s status_after=%s sent_at_utc=%s error_summary=%s",
                callback.from_user.id,
                reminder_event_id,
                dev_record_id,
                reminder_type,
                status_after,
                sent_at_utc,
                error_summary or "",
            )
            if status_after == "sent" and sent_at_utc:
                logger.info(
                    "dev_test_booking_reminder_sent actor_tg_id=%s event_id=%s yclients_record_id=%s reminder_type=%s status_before=%s status_after=%s sent_at_utc=%s error_summary=%s",
                    callback.from_user.id,
                    reminder_event_id,
                    dev_record_id,
                    reminder_type,
                    (before or {}).get("status"),
                    status_after,
                    sent_at_utc,
                    error_summary or "",
                )
                await callback.message.answer('✅ Тестовое уведомление отправлено.')
            else:
                logger.warning(
                    "dev_test_booking_reminder_not_sent actor_tg_id=%s event_id=%s yclients_record_id=%s reminder_type=%s status_before=%s status_after=%s sent_at_utc=%s error_summary=%s",
                    callback.from_user.id,
                    reminder_event_id,
                    dev_record_id,
                    reminder_type,
                    (before or {}).get("status"),
                    status_after,
                    sent_at_utc,
                    error_summary or "",
                )
                await callback.message.answer(
                    "⚠️ Тестовое событие создано, но уведомление не отправилось. Проверьте логи.\n"
                    f"event_id={reminder_event_id}\n"
                    f"yclients_record_id={dev_record_id}\n"
                    f"reminder_type={reminder_type}\n"
                    f"status={status_after or 'unknown'}\n"
                    f"error_summary={error_summary or '-'}"
                )
        await mark_sent(event_id)
    except Exception as exc:
        if dev_log_name:
            logger.exception(
                '%s_failed user_tg_id=%s event_id=%s callback_key=%s exception_type=%s exception_message=%s',
                dev_log_name,
                callback.from_user.id,
                event_id,
                key,
                type(exc).__name__,
                str(exc)[:180],
            )
        logger.exception(
            "dev_test_booking_reminder_failed actor_tg_id=%s event_id=%s callback_key=%s error_summary=%s",
            callback.from_user.id,
            event_id,
            key,
            str(exc)[:180],
        )
        await mark_failed(event_id, str(exc))
        await callback.message.answer('⚠️ Ошибка при отправке тестового события.')
    await callback.answer()


def lost_clients_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔍 Проверить сейчас', callback_data='broadcast:lost_clients:scan_now')],
        [InlineKeyboardButton(text='⚙️ Настройки', callback_data='broadcast:settings:lost_clients')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='broadcast:root')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')],
    ])


def _lost_client_key(client: dict[str, Any]) -> str:
    yc_id = str(client.get('yclients_client_id') or client.get('id') or client.get('client_id') or '').strip()
    if yc_id:
        return f'yc:{yc_id}'
    phone = ''.join(ch for ch in str(client.get('phone') or '') if ch.isdigit())
    if phone:
        return f'phone:{phone}'
    name = str(client.get("name") or "").strip()
    return f'name:{name}' if name else ''


async def _resolve_lost_clients_preview(thresholds: list[int], actor_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    days = min(thresholds or [30, 60, 90])
    clients, _ = await segment_service.resolve_lost_clients_from_yclients(days, actor_tg_id=actor_id)
    unique_clients: list[dict[str, Any]] = []
    seen: set[str] = set()
    for client in clients:
        key = _lost_client_key(client)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_clients.append(client)
    recipients = await broadcasts_repo._map_yclients_clients_to_telegram(
        unique_clients,
        actor_id=actor_id,
        audience_key='lost_clients_preview',
    )
    return unique_clients, recipients


DEV_TEST_STALE_TEXT = '⚠️ Это тестовое событие уже обработано или устарело.'


def _is_dev_feedback_event(event: dict | None) -> bool:
    return bool(event and event.get('is_test') and event.get('source') == 'dev_test')


def _post_visit_admin_kb(event_id: int, *, is_test: bool) -> InlineKeyboardMarkup:
    back_cb = 'broadcast:dev_tests:root' if is_test else 'broadcast:history:root'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💬 Ответить клиенту', callback_data=f'feedback_admin_reply:{event_id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data=back_cb)],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')],
    ])


def _admin_reply_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Отправить', callback_data='feedback_admin_reply_confirm:send')],
        [InlineKeyboardButton(text='✏️ Изменить', callback_data='feedback_admin_reply_confirm:edit')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='feedback_admin_reply_confirm:back')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')],
    ])


def _render_post_visit_admin_alert(event: dict, comment: str) -> str:
    if _is_dev_feedback_event(event):
        return (
            f"🚨 Тестовая негативная оценка\n\n"
            f"Оценка: {event.get('rating')}/5\n"
            f"Клиент: Тестовый клиент\n"
            f"Телефон: +7 999 000-00-00\n"
            f"Услуга: Тестовая стрижка\n"
            f"Мастер: Тестовый мастер\n"
            f"Дата визита: тестовый визит\n\n"
            f"Комментарий клиента:\n{comment}\n\n"
            f"🧪 Это тестовое событие. Реальные клиенты и записи не затронуты."
        )
    return (
        f"🚨 Низкая оценка после визита\n\nОценка: {event.get('rating')}/5\n"
        f"Клиент: {event.get('client_name') or '—'}\nТелефон: {event.get('client_phone') or '—'}\n"
        f"Услуга: {event.get('service_name') or '—'}\nМастер: {event.get('staff_name') or '—'}\n"
        f"Дата визита: {event.get('visit_datetime_utc') or '—'}\n\nКомментарий клиента:\n{comment}"
    )


async def _clear_fsm_with_log(state: FSMContext, *, user_id: int, event_id: int, reason: str, rating: int | None = None, is_test: bool = False) -> None:
    await clear_state_preserving_navigation(state)
    logger.info(
        'post_visit_feedback_fsm_cleared user_id=%s event_id=%s rating=%s is_test=%s source=dev_test reason=%s',
        user_id,
        event_id,
        rating,
        bool(is_test),
        reason,
    )


def birthday_section_kb() -> InlineKeyboardMarkup:
    return _with_nav([
        [InlineKeyboardButton(text='🔍 Проверить сейчас', callback_data='broadcast:birthday:scan_now')],
        [InlineKeyboardButton(text='⚙️ Настройки', callback_data='broadcast:settings:birthday')],
    ])


def repeat_visit_section_kb() -> InlineKeyboardMarkup:
    return _with_nav([
        [InlineKeyboardButton(text='🔍 Проверить сейчас', callback_data='broadcast:repeat_visit:scan_now')],
        [InlineKeyboardButton(text='⚙️ Настройки', callback_data='broadcast:settings:repeat_visit')],
    ])


@router.callback_query(F.data == 'broadcast:section:repeat_visit')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def open_repeat_visit_section(callback: CallbackQuery, state: FSMContext):
    await push_screen(state, 'broadcast_repeat_visit')
    s = await get_setting('repeat_visit')
    active_templates = len([x for x in (s.get('templates') or []) if str(x).strip()])
    await callback.message.edit_text(
        f"🔁 Повторный визит\n\nСтатус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\nСрок по умолчанию: {s.get('delay_days', 30)} дней\nШаблонов активно: {active_templates}/5",
        reply_markup=repeat_visit_section_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == 'broadcast:repeat_visit:scan_now')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def run_repeat_visit_scan_now(callback: CallbackQuery):
    if callback.message:
        await callback.message.answer('🔍 Проверяю клиентов для повторного визита...')
    try:
        summary = await run_repeat_visit_scan(callback.bot, force=callback.from_user.id == DEVELOPER_TG_ID)
        if summary.candidates == 0 and summary.sent == 0 and summary.errors == 0:
            await callback.message.answer('😌 Сейчас нет клиентов для повторного визита.')
        await callback.message.answer(f"✅ Проверка завершена\n\nКандидатов найдено: {summary.candidates}\nОтправлено: {summary.sent}\nПропущено: {summary.skipped}\nОшибок: {summary.errors}")
    except YClientsError:
        await callback.message.answer('⚠️ Не удалось проверить повторные визиты через YClients. Попробуйте позже.')
    except Exception:
        logger.exception('repeat_visit_manual_scan_failed')
        await callback.message.answer('⚠️ Не удалось завершить проверку. Попробуйте позже.')
    await callback.answer()


@router.callback_query(F.data.startswith(BUTTON_CB_PREFIX))
async def repeat_visit_book_click(callback: CallbackQuery, state: FSMContext):
    callback_data = callback.data or ""
    raw_id = callback.data.split(':')[-1]
    event_id = int(raw_id) if raw_id.isdigit() and int(raw_id) > 0 else None
    event = await get_repeat_event(event_id) if event_id else None
    if event_id:
        await mark_repeat_status(event_id, 'clicked_booking', clicked=True)
    logger.info(
        "automation_booking_cta_clicked user_tg_id=%s origin_type=%s lost_days=%s callback_data=%s booking_flow_started=%s",
        callback.from_user.id,
        "repeat_visit",
        "n/a",
        callback_data,
        "pending",
    )
    logger.info("repeat_visit_booking_origin_set user_tg_id=%s origin_type=%s", callback.from_user.id, "repeat_visit")
    try:
        await open_booking_from_notification(
            callback,
            state,
            funnel_type='repeat_visit',
            notification_event_id=event_id,
            yclients_client_id=str(event.get('yclients_client_id') or '') or None if event else None,
            is_test=bool(event and int(event.get('is_test') or 0)),
            source=str(event.get('source') or '') or None if event else None,
            preserve_data={"booking_source": "repeat_visit", "booking_origin_type": "repeat_visit"},
        )
        logger.info(
            "automation_booking_cta_clicked user_tg_id=%s origin_type=%s lost_days=%s callback_data=%s booking_flow_started=%s",
            callback.from_user.id,
            "repeat_visit",
            "n/a",
            callback_data,
            "yes",
        )
    except Exception as exc:
        logger.error(
            "automation_booking_cta_failed user_tg_id=%s origin_type=%s lost_days=%s callback_data=%s booking_flow_started=%s error_type=%s error_message=%s",
            callback.from_user.id,
            "repeat_visit",
            "n/a",
            callback_data,
            "no",
            type(exc).__name__,
            str(exc)[:180],
        )
        raise


@router.callback_query(F.data == 'broadcast:section:lost_clients')
async def open_lost_clients_section(callback: CallbackQuery, state: FSMContext):
    if not await _automation_allowed(callback):
        return
    await push_screen(state, 'broadcast_lost_clients')
    s = await get_setting('lost_clients')
    recent_sent = await get_recent_stats(7)
    await callback.message.edit_text(
        f"😔 Потерянные клиенты\n\nСтатус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\nПороги: {s.get('threshold_days',[30,60,90])}\nОтправлено за 7 дней: {recent_sent}\n\nАвтоматизация ищет клиентов без визита 30/60/90 дней и без будущей записи.",
        reply_markup=lost_clients_section_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == 'broadcast:lost_clients:scan_now')
async def run_lost_clients_scan_now(callback: CallbackQuery):
    if not await _automation_allowed(callback):
        return
    if callback.message:
        await callback.message.answer('🔍 Проверяю потерянных клиентов...')
    try:
        settings = await get_setting('lost_clients')
        thresholds = [int(x) for x in settings.get('threshold_days', [30, 60, 90]) if int(x) > 0]
        clients, recipients = await _resolve_lost_clients_preview(thresholds, callback.from_user.id)
        summary = await run_lost_clients_scan(callback.bot, force=callback.from_user.id == DEVELOPER_TG_ID)

        if not clients:
            result_text = '😌 Потерянных клиентов сейчас нет.'
        elif not recipients:
            result_text = '😌 Клиенты найдены в YClients, но получателей в Telegram пока нет.'
        else:
            result_text = '✅ Проверка завершена'

        await callback.message.answer(
            f"{result_text}\n\n"
            f"Клиентов в YClients: {len(clients)}\n"
            f"Получателей в Telegram: {len(recipients)}\n"
            f"Готово к отправке: {len(recipients)}\n"
            f"Отправлено сейчас: {summary.sent}\n"
            f"Пропущено: {summary.skipped}\n"
            f"Ошибок: {summary.errors}"
        )
    except YClientsError:
        await callback.message.answer('⚠️ Не удалось проверить клиентов через YClients. Попробуйте позже.')
    except Exception:
        logger.exception('lost_clients_manual_scan_failed')
        await callback.message.answer('⚠️ Не удалось завершить проверку. Попробуйте позже.')
    await callback.answer()


@router.callback_query(F.data == 'broadcast:birthday:scan_now')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def run_birthday_scan_now(callback: CallbackQuery):
    if callback.message:
        await callback.message.answer('🔍 Проверяю дни рождения...')
    try:
        summary = await run_birthday_scan(callback.bot, force=callback.from_user.id == DEVELOPER_TG_ID)
        if summary.candidates == 0 and summary.sent == 0 and summary.errors == 0:
            await callback.message.answer('😌 Сейчас нет клиентов для поздравления.')
        await callback.message.answer(f"✅ Проверка завершена\n\nКандидатов найдено: {summary.candidates}\nОтправлено: {summary.sent}\nПропущено: {summary.skipped}\nОшибок: {summary.errors}")
    except Exception:
        logger.exception('birthday_manual_scan_failed')
        await callback.message.answer('⚠️ Не удалось проверить дни рождения через YClients. Попробуйте позже.')
    await callback.answer()


@router.callback_query(F.data.startswith('lost_clients:book:'))
async def lost_client_book_click(callback: CallbackQuery, state: FSMContext):
    callback_data = callback.data or ""
    raw_id = callback.data.split(':')[-1]
    event_id = int(raw_id) if raw_id.isdigit() and int(raw_id) > 0 else None
    event = await get_lost_event(event_id) if event_id else None
    if event_id:
        await mark_lost_status(event_id, 'clicked_rebook', clicked=True)
    lost_days = int(event.get('threshold_days') or 0) if event else None
    if not lost_days and raw_id.isdigit() and int(raw_id) in {30, 60, 90}:
        lost_days = int(raw_id)
    logger.info(
        "lost_client_booking_cta_clicked user_tg_id=%s lost_days=%s booking_origin=%s callback_data=%s",
        callback.from_user.id,
        lost_days if lost_days else "n/a",
        "lost_client",
        callback_data,
    )
    try:
        await open_booking_from_notification(
            callback,
            state,
            funnel_type='lost_client',
            notification_event_id=event_id,
            yclients_client_id=str(event.get('yclients_client_id') or '') or None if event else None,
            is_test=bool(event and int(event.get('is_test') or 0)),
            source=str(event.get('source') or '') or None if event else None,
            preserve_data={
                "booking_source": "lost_client",
                "booking_origin": "lost_client",
                "booking_origin_type": "lost_client",
                "lost_days": lost_days,
            },
        )
        logger.info(
            "lost_client_booking_origin_saved user_tg_id=%s lost_days=%s booking_origin=%s fsm_keys=%s",
            callback.from_user.id,
            lost_days if lost_days else "n/a",
            "lost_client",
            ",".join(sorted((await state.get_data()).keys())),
        )
    except Exception as exc:
        logger.error(
            "automation_booking_cta_failed user_tg_id=%s origin_type=%s lost_days=%s callback_data=%s booking_flow_started=%s error_type=%s error_message=%s",
            callback.from_user.id,
            "lost_client",
            lost_days if lost_days else "n/a",
            callback_data,
            "no",
            type(exc).__name__,
            str(exc)[:180],
        )
        raise


@router.callback_query(F.data.startswith('birthday_funnel:claim:'))
async def birthday_claim_click(callback: CallbackQuery, state: FSMContext):
    raw_id = callback.data.split(':')[-1]
    if not raw_id.isdigit():
        await callback.answer('⚠️ Это предложение уже устарело. Вы можете записаться через главное меню.', show_alert=True)
        return
    event_id = int(raw_id)
    event = await get_birthday_event(event_id)
    if not event or int(event.get('client_tg_id') or 0) != callback.from_user.id:
        await callback.answer('⚠️ Это предложение уже устарело. Вы можете записаться через главное меню.', show_alert=True)
        return
    is_test = bool(int(event.get('is_test') or 0))
    if is_test and not _developer_only(callback.from_user.id):
        return await _deny_dev_only(callback)
    await mark_birthday_status(event_id, 'clicked_gift', clicked=True)
    source = str(event.get('source') or '') or None
    await state.update_data(
        booking_source='birthday_funnel',
        birthday_event_id=event_id,
        birthday_discount_context=True,
        birthday_is_test=is_test,
        birthday_source=source,
        birthday_claimed_at_utc=datetime.now(timezone.utc).isoformat(),
        notification_is_test=is_test,
        notification_source=source,
    )
    logger.info(
        'birthday_booking_context_created user_tg_id=%s birthday_event_id=%s is_test=%s source=%s',
        callback.from_user.id,
        event_id,
        is_test,
        source or 'n/a',
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✂️ Записаться', callback_data=BIRTHDAY_BUTTON_BOOK)]])
    await callback.message.answer('🎁 Ваш подарок активирован!\n\nПокажите это сообщение администратору — при оплате он сделает скидку.', reply_markup=kb)
    await callback.answer('Подарок активирован ✅')


@router.callback_query(F.data.startswith(BIRTHDAY_BUTTON_BOOK))
async def birthday_book_click(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    event_id = data.get('birthday_event_id')
    raw_id = (callback.data or '').removeprefix(BIRTHDAY_BUTTON_BOOK).lstrip(':')
    if not isinstance(event_id, int) and raw_id.isdigit():
        event_id = int(raw_id)
    event_id = event_id if isinstance(event_id, int) else None
    event = await get_birthday_event(event_id) if event_id else None
    if event_id and (not event or int(event.get('client_tg_id') or 0) != callback.from_user.id):
        logger.warning(
            'birthday_booking_cta_skipped_invalid_event user_tg_id=%s birthday_event_id=%s is_test=%s source=%s',
            callback.from_user.id,
            event_id,
            False,
            'n/a',
        )
        await callback.answer('⚠️ Не удалось открыть запись. Попробуйте через главное меню.', show_alert=True)
        return
    if event_id:
        await mark_birthday_status(event_id, 'clicked_booking', clicked=True)
    is_test = bool(int(event.get('is_test') or 0)) if event else bool(data.get('notification_is_test'))
    source = (str(event.get('source') or '') or None) if event else (str(data.get('notification_source') or '') or None)
    logger.info(
        'birthday_booking_cta_clicked user_tg_id=%s birthday_event_id=%s is_test=%s source=%s',
        callback.from_user.id,
        event_id or 'n/a',
        is_test,
        source or 'n/a',
    )
    logger.info(
        'birthday_booking_context_created user_tg_id=%s birthday_event_id=%s is_test=%s source=%s',
        callback.from_user.id,
        event_id or 'n/a',
        is_test,
        source or 'n/a',
    )
    await open_booking_from_notification(
        callback,
        state,
        funnel_type='birthday',
        notification_event_id=event_id,
        yclients_client_id=(str(event.get('yclients_client_id') or '') or None) if event else None,
        is_test=is_test,
        source=source,
        preserve_data={
            'booking_source': 'birthday_funnel',
            'birthday_event_id': event_id,
            'birthday_discount_context': True,
            'birthday_is_test': is_test,
            'birthday_source': source,
            'birthday_claimed_at_utc': data.get('birthday_claimed_at_utc') or datetime.now(timezone.utc).isoformat(),
        },
    )


@router.callback_query(F.data == 'broadcast:dev_tests:cleanup_confirm')
async def confirm_cleanup(callback: CallbackQuery):
    if not _developer_only(callback.from_user.id):
        return await _deny_dev_only(callback)
    await cleanup_test_events()
    await cleanup_post_visit_dev_test_events()
    await cleanup_booking_reminder_dev_test_events()
    await callback.message.answer('✅ Тестовые события очищены.')
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:dev_rating:'))
async def handle_legacy_dev_rating(callback: CallbackQuery):
    if not _developer_only(callback.from_user.id):
        return await _deny_dev_only(callback)
    logger.info(
        'dev_rating_stale_callback user_id=%s event_id=%s rating=%s is_test=%s source=dev_test',
        callback.from_user.id,
        None,
        None,
        True,
    )
    await callback.answer(DEV_TEST_STALE_TEXT, show_alert=True)


SEGMENT_ACCESS_DENIED_TEXT = "⛔ Раздел недоступен."
SEGMENT_STALE_TEXT = "⚠️ Данные устарели. Откройте раздел заново."
SEGMENT_LOAD_FAILED_TEXT = "⚠️ Не удалось загрузить сегмент. Попробуйте позже."


def _segment_business_context(callback: CallbackQuery, role: str | None, segment_key: str | None = None, elapsed_ms: int | None = None, error: str | None = None) -> dict[str, object]:
    return {
        "user_id": callback.from_user.id,
        "role": role or "unknown",
        "segment_key": segment_key or "n/a",
        "elapsed_ms": elapsed_ms if elapsed_ms is not None else -1,
        "error": error or "",
    }


async def _ensure_segment_access(callback: CallbackQuery) -> str | None:
    role = await resolve_role(callback.from_user.id)
    if role not in {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}:
        logger.warning("access_denied section=client_segments user_id=%s role=%s", callback.from_user.id, role or "user")
        if callback.message:
            await callback.message.answer(SEGMENT_ACCESS_DENIED_TEXT)
        await callback.answer()
        return None
    return role


def _segment_root_text() -> str:
    return (
        "🎯 Сегменты клиентов\n\n"
        "Бот автоматически распределяет клиентов по группам на основе визитов, записей и данных из YClients."
    )


def _segment_detail_kb(
    refresh_callback: str | None = None,
    back_callback: str = "broadcast:section:segments",
    use_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📣 Использовать для рассылки", callback_data=use_callback or "broadcast:segment_use_placeholder")]]
    if refresh_callback:
        rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback)])
    rows.append([InlineKeyboardButton(text=BACK_BTN, callback_data=back_callback)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _valid_callback_data(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value if len(value.encode("utf-8")) <= 64 else None


def _segment_picker_kb(items: list[tuple[str, str]], prefix: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    skipped = 0
    for item_id, name in items[:30]:
        callback_data = _valid_callback_data(f"{prefix}:{item_id}")
        if not callback_data:
            skipped += 1
            logger.warning("segment_picker_callback_skipped prefix=%s item_id=%s callback_len=%s", prefix, item_id, len(f"{prefix}:{item_id}".encode("utf-8")))
            continue
        rows.append([InlineKeyboardButton(text=name[:40], callback_data=callback_data)])
    if skipped:
        logger.info("segment_picker_callbacks_validated prefix=%s skipped_over_limit=%s", prefix, skipped)
    rows.append([InlineKeyboardButton(text=BACK_BTN, callback_data="broadcast:section:segments")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _segment_nav_only_kb(back_callback: str = "broadcast:section:segments") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BACK_BTN, callback_data=back_callback)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


def _format_segment_detail(
    title: str,
    description: str,
    count: int,
    updated: str | None,
    warning: str | None = None,
    telegram_count: int | None = None,
) -> str:
    yclients_count = max(0, int(count or 0))
    if telegram_count is None:
        count_text = f"Количество клиентов: {yclients_count}"
    else:
        count_text = (
            f"Клиентов в YClients: {yclients_count}\n"
            f"Получателей в Telegram: {max(0, int(telegram_count or 0))}"
        )
    text = f"{title}\n\n{description}\n\n{count_text}\nОбновлено: {updated or '—'}"
    if telegram_count is not None and yclients_count != int(telegram_count or 0):
        text += "\n\nℹ️ YClients показывает бизнес-аудиторию, а Telegram — только клиентов, связанных с ботом для отправки."
    if warning:
        text += f"\n\n{warning}"
    if not yclients_count:
        text += "\n\n😌 В этом сегменте пока нет клиентов."
    return text


def _segment_refresh_callback(key: str, extra_id: str | None = None) -> str | None:
    suffix = f":{extra_id}" if extra_id else ""
    return _valid_callback_data(f"broadcast:segments:refresh_one:{key}{suffix}")


def _segment_use_callback(key: str, extra_id: str | None = None) -> str | None:
    suffix = f":{extra_id}" if extra_id else ""
    return _valid_callback_data(f"broadcast:segments:use:{key}{suffix}")


def _is_message_not_modified_error(exc: Exception) -> bool:
    return isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc).lower()


def _master_segment_title(master_name: str | None = None) -> str:
    name = (master_name or "").strip()
    if name:
        return f"💈 Клиенты мастера: {name}"
    return "💈 Клиенты выбранного мастера"


async def _send_segment_dev_diag(
    callback: CallbackQuery,
    *,
    action: str,
    segment_key: str,
    endpoint: str,
    exc: Exception,
    master_id: str | None = None,
    extra_context: dict[str, Any] | None = None,
) -> None:
    extra_lines = ""
    if extra_context:
        extra_lines = "\n".join(f"{key}: {str(value)[:120]}" for key, value in extra_context.items()) + "\n"
    text = (
        "⚠️ Segment diagnostic\n"
        f"action: {action}\n"
        f"segment_key: {segment_key}\n"
        f"master_id: {master_id or '—'}\n"
        f"{extra_lines}"
        f"endpoint/function: {endpoint}\n"
        f"exception: {type(exc).__name__}: {str(exc)[:300]}"
    )
    try:
        await callback.bot.send_message(DEVELOPER_TG_ID, text[:1000])
    except Exception:
        logger.exception("segment_dev_diag_send_failed action=%s segment_key=%s", action, segment_key)


async def _render_live_yclients_segment_detail(
    callback: CallbackQuery,
    *,
    segment_key: str,
    audience_key: str,
    master_id: str | None = None,
    back_callback: str = "broadcast:section:segments",
) -> None:
    clients, recipients, diag = await broadcasts_repo.resolve_yclients_audience_with_mapping(
        audience_key,
        callback.from_user.id,
        payload=master_id,
    )
    tz_name = await segment_service.branch_timezone()
    now_utc = broadcasts_repo.now_iso()
    if segment_key == "by_master" and master_id:
        filter_json = json.dumps({"master_id": str(master_id).strip()}, ensure_ascii=False, separators=(",", ":"))
        await broadcasts_repo.execute(
            """
            INSERT INTO client_segment_cache (segment_key, segment_filter_json, client_count, calculated_at_utc, branch_timezone, error_summary, created_at_utc, updated_at_utc)
            VALUES ('by_master', ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(segment_key, segment_filter_json) DO UPDATE SET
                client_count=excluded.client_count,
                calculated_at_utc=excluded.calculated_at_utc,
                branch_timezone=excluded.branch_timezone,
                error_summary=NULL,
                updated_at_utc=excluded.updated_at_utc
            """,
            (filter_json, len(clients), now_utc, tz_name, now_utc, now_utc),
        )
    else:
        await segment_service._save_cache(segment_key, len(clients), tz_name, None, now_utc)
    updated_local = segment_service._fmt_local(now_utc, tz_name)
    title = SEGMENTS.get(segment_key, audience_key)
    if segment_key == "by_master":
        title = _master_segment_title(await segment_service.resolve_master_name(master_id or ""))
    text = _format_segment_detail(
        title,
        DESCRIPTIONS.get(segment_key, "Клиенты из YClients."),
        len(clients),
        updated_local,
        None,
        telegram_count=len(recipients),
    )
    await callback.message.edit_text(
        text,
        reply_markup=_segment_detail_kb(
            _segment_refresh_callback(segment_key, master_id),
            back_callback,
            _segment_use_callback(segment_key, master_id),
        ),
    )


@router.callback_query(F.data == 'broadcast:section:segments')
async def open_segments(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    started = time.perf_counter()
    logger.info("segment_root_opened user_id=%s role=%s", callback.from_user.id, role)
    await push_screen(state, "broadcast_segments")
    try:
        await callback.message.edit_text(_segment_root_text(), reply_markup=segment_root_kb())
    except TelegramBadRequest:
        await callback.message.answer(_segment_root_text(), reply_markup=segment_root_kb())
    logger.info("segment_root_rendered user_id=%s role=%s elapsed_ms=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000))
    await callback.answer()


@router.callback_query(F.data == 'broadcast:segments:refresh')
async def refresh_segments(callback: CallbackQuery):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    started = time.perf_counter()
    logger.info("segment_manual_refresh_started user_id=%s role=%s segment_key=all", callback.from_user.id, role)
    await callback.message.answer("🔄 Обновляю сегменты клиентов...")
    try:
        await asyncio.wait_for(segment_service.refresh_segment_cache(), timeout=20)
        logger.info("segment_manual_refresh_finished user_id=%s role=%s segment_key=all elapsed_ms=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000))
        await callback.message.answer("✅ Сегменты обновлены.", reply_markup=segment_root_kb())
    except Exception as exc:
        logger.exception("segment_refresh_failed user_id=%s role=%s elapsed_ms=%s error=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await callback.message.answer("⚠️ Не удалось обновить сегменты. Попробуйте позже.")
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:segments:refresh_one:'))
async def refresh_one_segment(callback: CallbackQuery):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    payload = callback.data.removeprefix('broadcast:segments:refresh_one:').strip()
    parts = payload.split(":")
    key = parts[0] if parts else ""
    extra_id = ":".join(parts[1:]) if len(parts) > 1 else None
    if key not in SEGMENTS and key != "by_master":
        await callback.message.answer(SEGMENT_STALE_TEXT)
        return await callback.answer()
    started = time.perf_counter()
    logger.info("segment_manual_refresh_started user_id=%s role=%s segment_key=%s", callback.from_user.id, role, key)
    await callback.answer("🔄 Обновляю...")
    try:
        if key in {"all_clients", "active_30", "no_future_booking"}:
            await _render_live_yclients_segment_detail(callback, segment_key=key, audience_key=key)
            logger.info("segment_manual_refresh_finished user_id=%s role=%s segment_key=%s elapsed_ms=%s source_used=yclients_live", callback.from_user.id, role, key, int((time.perf_counter() - started) * 1000))
            return
        if key == "by_master" and extra_id:
            await _render_live_yclients_segment_detail(callback, segment_key=key, audience_key="by_master", master_id=extra_id, back_callback="broadcast:segments:by_master:picker")
            logger.info("segment_manual_refresh_finished user_id=%s role=%s segment_key=%s master_id=%s elapsed_ms=%s source_used=yclients_live", callback.from_user.id, role, key, extra_id, int((time.perf_counter() - started) * 1000))
            return
        row, _ = await segment_service.ensure_segment_fresh(key, force=True)
        summary = await segment_service.get_segment_summary(key)
        text = _format_segment_detail(summary.title, summary.description, summary.count, summary.updated_local, summary.warning)
        await callback.message.edit_text(text, reply_markup=_segment_detail_kb(_segment_refresh_callback(key), use_callback=_segment_use_callback(key)))
        if key == "cancelled_30":
            logger.info(
                "cancelled_recent_cache_refreshed actor_tg_id=%s segment_key=%s segment_count=%s calculated_at_utc=%s",
                callback.from_user.id,
                key,
                int(row["client_count"]) if row else summary.count,
                row["calculated_at_utc"] if row else None,
            )
        logger.info("segment_manual_refresh_finished user_id=%s role=%s segment_key=%s old_count=%s new_count=%s calculated_at_utc=%s elapsed_ms=%s", callback.from_user.id, role, key, None, int(row["client_count"]) if row else summary.count, row["calculated_at_utc"] if row else None, int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        if _is_message_not_modified_error(exc):
            logger.info("segment_manual_refresh_noop user_id=%s role=%s segment_key=%s master_id=%s elapsed_ms=%s reason=message_not_modified", callback.from_user.id, role, key, extra_id, int((time.perf_counter() - started) * 1000))
            return
        endpoint = {
            "all_clients": "resolve_all_clients_from_yclients",
            "active_30": "resolve_active_clients_from_yclients",
            "no_future_booking": "resolve_no_future_booking_clients_from_yclients",
            "by_master": "fetch_master_segment_clients",
        }.get(key, "ensure_segment_fresh")
        logger.exception("segment_refresh_failed user_id=%s role=%s segment_key=%s elapsed_ms=%s error=%s", callback.from_user.id, role, key, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await _send_segment_dev_diag(callback, action="segment_refresh", segment_key=key, master_id=extra_id, endpoint=endpoint, exc=exc)
        await callback.message.answer("⚠️ Не удалось обновить сегменты. Попробуйте позже.")


@router.callback_query(F.data == "broadcast:segment_use_placeholder")
async def segment_use_placeholder(callback: CallbackQuery):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    logger.info("segment_button_clicked user_id=%s role=%s segment_key=use_for_broadcast", callback.from_user.id, role)
    await callback.answer()
    await callback.message.answer("📣 Использование сегмента для рассылки будет добавлено в следующем шаге.")


@router.callback_query(F.data == "broadcast:segments:bad_rating")
async def bad_rating_segment_unavailable(callback: CallbackQuery):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    logger.info("bad_rating_segment_hidden actor_tg_id=%s company_id=%s error_summary=%s", callback.from_user.id, None, "segment_temporarily_unavailable")
    await callback.message.edit_text(
        "⚠️ Этот сегмент временно недоступен.",
        reply_markup=_segment_nav_only_kb(),
    )
    await callback.answer()


async def _start_one_time_from_audience(
    callback: CallbackQuery,
    state: FSMContext,
    audience_key: str,
    role: str | None = None,
    segment_key: str | None = None,
    payload: str | None = None,
):
    role = role or await resolve_role(callback.from_user.id)
    started = time.perf_counter()
    logger.info("broadcast_audience_resolve_started actor_tg_id=%s role=%s audience_key=%s", callback.from_user.id, role, audience_key)
    if segment_key == "birthday_soon":
        logger.info(
            "birthday_use_for_broadcast_handler_entered actor_tg_id=%s callback_data=%s segment_key=%s audience_key=%s",
            callback.from_user.id,
            callback.data,
            segment_key,
            audience_key,
        )
    business_clients_count: int | None = None
    try:
        recipients_payload = None
        if segment_key in {"by_master", "by_service_category"}:
            parts = (payload or "").split(":")
            recipients_payload = ":".join(parts[1:]) if len(parts) > 1 else None
        if segment_key in {"all_clients", "active_30", "no_future_booking", "by_master"}:
            clients, recipients, diag = await broadcasts_repo.resolve_yclients_audience_with_mapping(audience_key, callback.from_user.id, payload=recipients_payload)
            business_clients_count = len(clients)
            logger.info("broadcast_audience_resolve_yclients_finished actor_tg_id=%s role=%s audience_key=%s company_id=%s endpoint=%s business_clients_count=%s telegram_mapped_recipients_count=%s elapsed_ms=%s", callback.from_user.id, role, audience_key, diag.get("company_id"), diag.get("endpoint"), len(clients), len(recipients), int((time.perf_counter() - started) * 1000))
        else:
            recipients = await broadcasts_repo.resolve_one_time_audience(audience_key, callback.from_user.id, payload=recipients_payload)
        logger.info("broadcast_audience_resolve_finished actor_tg_id=%s role=%s audience_key=%s count=%s elapsed_ms=%s", callback.from_user.id, role, audience_key, len(recipients), int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        tb_tail = "\n".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-5:])
        logger.exception("broadcast_audience_resolve_failed actor_tg_id=%s role=%s audience_key=%s elapsed_ms=%s error_summary=%s", callback.from_user.id, role, audience_key, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        if segment_key == "birthday_soon":
            logger.error(
                "birthday_audience_resolve_failed actor_tg_id=%s callback_data=%s segment_key=%s audience_key=%s exception_type=%s exception_message=%s traceback_tail=%s",
                callback.from_user.id,
                callback.data,
                segment_key,
                audience_key,
                type(exc).__name__,
                str(exc)[:200],
                tb_tail[-400:],
            )
        if segment_key:
            logger.exception("segment_use_for_broadcast_failed actor_tg_id=%s segment_key=%s audience_key=%s payload=%s recipient_count=%s error_summary=%s", callback.from_user.id, segment_key, audience_key, payload, None, str(exc)[:200])
        await callback.message.answer('⚠️ Не удалось получить аудиторию. Попробуйте позже.')
        return await callback.answer()
    if not recipients:
        if segment_key == "birthday_soon":
            logger.info(
                "birthday_audience_resolve_empty actor_tg_id=%s callback_data=%s segment_key=%s audience_key=%s birthday_clients_count=%s telegram_mapped_recipients_count=0",
                callback.from_user.id,
                callback.data,
                segment_key,
                audience_key,
                None,
            )
        if segment_key:
            logger.info("segment_use_for_broadcast_empty actor_tg_id=%s segment_key=%s audience_key=%s payload=%s recipient_count=%s", callback.from_user.id, segment_key, audience_key, payload, 0)
        await push_screen(state, 'one_time_broadcast_empty_audience', payload={'audience_key': audience_key, 'audience_name': broadcasts_repo.audience_name(audience_key)})
        empty_text = '😌 В этой аудитории пока нет клиентов для рассылки.'
        if segment_key in {"all_clients", "active_30", "no_future_booking", "by_master"} and business_clients_count:
            empty_text = '😌 В этой аудитории пока нет получателей в Telegram.'
        elif segment_key in {"by_master", "by_service_category"}:
            empty_text = '😌 В этом сегменте пока нет клиентов для рассылки в Telegram.'
        if segment_key == "birthday_soon":
            birthday_clients, _ = await segment_service.resolve_birthday_soon_clients(actor_tg_id=callback.from_user.id)
            empty_text = '😌 В этом сегменте пока нет клиентов для рассылки в Telegram.' if birthday_clients else '😌 В этом сегменте пока нет клиентов.'
        await callback.message.answer(empty_text, reply_markup=_with_nav([]))
        return await callback.answer()
    await state.update_data(audience=audience_key, recipients=recipients, audience_count=len(recipients))
    if segment_key:
        await _log_notification_action(callback.from_user.id, 'broadcast_segment_used', f'выбрал сегмент «{broadcasts_repo.audience_name(audience_key)}» для разовой рассылки', segment_key=segment_key, audience_key=audience_key, recipients_count=len(recipients))
    await state.set_state(BroadcastStates.waiting_text)
    if segment_key:
        logger.info("segment_use_for_broadcast_started actor_tg_id=%s segment_key=%s audience_key=%s payload=%s recipient_count=%s", callback.from_user.id, segment_key, audience_key, payload, len(recipients))
    if segment_key == "birthday_soon":
        logger.info(
            "birthday_one_time_flow_started actor_tg_id=%s callback_data=%s segment_key=%s audience_key=%s telegram_mapped_recipients_count=%s",
            callback.from_user.id,
            callback.data,
            segment_key,
            audience_key,
            len(recipients),
        )
    await callback.message.answer(f"Аудитория: {broadcasts_repo.audience_name(audience_key)}\nПолучателей: {len(recipients)}\n\nВведите текст рассылки:")
    return await callback.answer()


@router.callback_query(F.data.startswith("broadcast:segments:use:"))
async def segment_use_for_broadcast(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    payload = callback.data.removeprefix("broadcast:segments:use:").strip()
    parts = payload.split(":")
    segment_key = parts[0] if parts else ""
    extra_id = ":".join(parts[1:]) if len(parts) > 1 else None
    logger.info("segment_use_for_broadcast_clicked actor_tg_id=%s segment_key=%s payload=%s", callback.from_user.id, segment_key, payload)
    mapping = {
        "all_clients": "all_clients",
        "active_30": "active_30",
        "lost_30": "lost_30",
        "lost_60": "lost_60",
        "lost_90": "lost_90",
        "no_future_booking": "no_future_booking",
        "cancelled_30": "cancelled_recent",
        "bad_rating": "bad_rating",
        "birthday_soon": "birthday_soon",
        "by_master": "by_master",
        "by_service_category": "by_service_category",
    }
    audience_key = mapping.get(segment_key)
    if not audience_key:
        logger.info("segment_use_for_broadcast_failed actor_tg_id=%s segment_key=%s audience_key=%s payload=%s recipient_count=%s error_summary=%s", callback.from_user.id, segment_key, None, payload, None, "unsupported_segment")
        await callback.message.answer("⚠️ Этот сегмент пока нельзя использовать для разовой рассылки.")
        return await callback.answer()
    logger.info("segment_use_for_broadcast_mapped actor_tg_id=%s segment_key=%s audience_key=%s payload=%s", callback.from_user.id, segment_key, audience_key, payload)
    if segment_key not in {"by_master", "by_service_category"} and extra_id:
        logger.info("segment_use_for_broadcast_failed actor_tg_id=%s segment_key=%s audience_key=%s payload=%s recipient_count=%s error_summary=%s", callback.from_user.id, segment_key, audience_key, payload, None, "unsupported_segment_payload")
        await callback.message.answer("⚠️ Этот сегмент пока нельзя использовать для разовой рассылки.")
        return await callback.answer()
    await clear_state_preserving_navigation(state)
    await push_screen(state, 'one_time_broadcast_audience_selection')
    if segment_key == "by_service_category":
        logger.info("service_category_broadcast_started actor_tg_id=%s category_payload=%s", callback.from_user.id, payload)
    if segment_key == "cancelled_30":
        logger.info("cancelled_recent_use_for_broadcast_started actor_tg_id=%s segment_key=%s audience_key=%s", callback.from_user.id, segment_key, audience_key)
    await _start_one_time_from_audience(callback, state, audience_key, role=role, segment_key=segment_key, payload=payload)


@router.callback_query(F.data == 'broadcast:segments:by_master:picker')
async def open_master_segment_picker(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    started = time.perf_counter()
    logger.info("segment_button_clicked user_id=%s role=%s segment_key=by_master_picker", callback.from_user.id, role)
    await push_screen(state, "broadcast_segments_by_master_picker")
    try:
        masters = await segment_service.list_masters()
        if not masters:
            await callback.message.edit_text("⚠️ Не удалось загрузить мастеров. Проверьте интеграцию YClients или попробуйте позже.", reply_markup=_segment_detail_kb(None))
        else:
            await callback.message.edit_text("💈 Выберите мастера", reply_markup=_segment_picker_kb(masters, "broadcast:segments:by_master"))
        logger.info("master_picker_loaded user_id=%s role=%s count=%s elapsed_ms=%s", callback.from_user.id, role, len(masters), int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        logger.exception("master_picker_failed user_id=%s role=%s elapsed_ms=%s error=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await callback.message.edit_text("⚠️ Не удалось загрузить мастеров. Проверьте интеграцию YClients или попробуйте позже.", reply_markup=_segment_detail_kb(None))
    await callback.answer()


@router.callback_query(F.data == 'broadcast:segments:by_service_category:picker')
async def open_service_category_segment_picker(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    started = time.perf_counter()
    logger.info("service_category_picker_opened actor_tg_id=%s segment_key=by_service_category_picker", callback.from_user.id)
    await push_screen(state, "broadcast_segments_by_service_category_picker")
    try:
        categories, diag = await segment_service.list_service_categories(actor_tg_id=callback.from_user.id)
        if not categories:
            await callback.message.edit_text("😌 В YClients пока нет категорий услуг.", reply_markup=_segment_nav_only_kb())
        else:
            picker_items = [(str(item['id']), item['name']) for item in categories]
            prefix = "broadcast:segments:by_service_category"
            callback_lengths = [len(f"{prefix}:{item_id}".encode("utf-8")) for item_id, _ in picker_items[:30]]
            if callback_lengths:
                logger.info("service_category_picker_callback_lengths actor_tg_id=%s min_len=%s max_len=%s over_limit=%s", callback.from_user.id, min(callback_lengths), max(callback_lengths), any(length > 64 for length in callback_lengths))
            for item_id, name in picker_items[:30]:
                callback_data = f"{prefix}:{item_id}"
                logger.info("service_category_button_payload actor_tg_id=%s button_text=%s callback_prefix=%s callback_len=%s", callback.from_user.id, name[:80], prefix, len(callback_data.encode("utf-8")))
            await callback.message.edit_text("✂️ Выберите категорию услуг", reply_markup=_segment_picker_kb(picker_items, prefix))
        logger.info("service_category_buttons_built actor_tg_id=%s company_id=%s raw_services_count=%s unique_categories_count=%s elapsed_ms=%s", callback.from_user.id, diag.get("company_id"), diag.get("raw_services_count"), diag.get("unique_categories_count"), int((time.perf_counter() - started) * 1000))
    except TelegramBadRequest as exc:
        logger.exception("service_category_picker_callback_invalid actor_tg_id=%s callback_data=%s elapsed_ms=%s error_summary=%s", callback.from_user.id, callback.data, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await callback.message.edit_text("⚠️ Ошибка кнопок категорий. Обратитесь к разработчику.", reply_markup=_segment_nav_only_kb())
    except Exception as exc:
        logger.exception("service_category_yclients_fetch_failed actor_tg_id=%s callback_data=%s endpoint=%s method=%s elapsed_ms=%s error_summary=%s", callback.from_user.id, callback.data, "/api/v1/service_categories/{company_id}", "GET", int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await callback.message.edit_text("⚠️ Не удалось загрузить категории услуг из YClients. Попробуйте позже.", reply_markup=_segment_nav_only_kb())
        if _developer_only(callback.from_user.id):
            settings = await get_yclients_settings()
            company_id = settings.company_id if settings else None
            token_present = bool(settings and settings.partner_token)
            diag_text = _render_service_category_diag(
                callback_data=callback.data or "",
                company_id=company_id,
                token_present=token_present,
                exc=exc,
            )
            await callback.message.answer(diag_text)
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:segments:by_master:'))
async def open_master_segment_detail(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    master_id = callback.data.removeprefix('broadcast:segments:by_master:').strip()
    if not master_id or master_id == 'picker':
        await callback.message.answer(SEGMENT_STALE_TEXT)
        return await callback.answer()
    started = time.perf_counter()
    await push_screen(state, "broadcast_segments_by_master_detail", payload={"master_id": master_id})
    logger.info("master_segment_opened actor_tg_id=%s selected_callback_master_id=%s", callback.from_user.id, master_id)
    try:
        debug_info = await segment_service.resolve_master_debug_info(master_id)
        logger.info("master_segment_id_mapping actor_tg_id=%s selected_callback_master_id=%s local_master_id=%s yclients_staff_id=%s master_name=%s company_id=%s is_active=%s is_deleted=%s is_fired=%s", callback.from_user.id, debug_info.get("selected_callback_master_id"), debug_info.get("local_master_id"), debug_info.get("yclients_staff_id"), debug_info.get("master_name"), debug_info.get("company_id"), debug_info.get("is_active"), debug_info.get("is_deleted"), debug_info.get("is_fired"))
        filter_json = json.dumps({"master_id": str(master_id).strip()}, ensure_ascii=False, separators=(",", ":"))
        cache_before = await broadcasts_repo.fetchone("SELECT client_count, calculated_at_utc FROM client_segment_cache WHERE segment_key='by_master' AND segment_filter_json=?", (filter_json,))
        logger.info("master_segment_resolve_started actor_tg_id=%s selected_callback_master_id=%s data_source_used=%s cache_count_before=%s", callback.from_user.id, master_id, "yclients_records_api", cache_before["client_count"] if cache_before else None)
        clients, diag = await segment_service.fetch_master_segment_clients(master_id, actor_tg_id=callback.from_user.id)
        recipients = await broadcasts_repo._map_yclients_clients_to_telegram(clients, actor_id=callback.from_user.id, audience_key="by_master")
        summary = await segment_service.get_master_segment_summary(master_id)
        summary.count = len(clients)
        now = broadcasts_repo.now_iso()
        tz_name = await segment_service.branch_timezone()
        await broadcasts_repo.execute(
            """
            INSERT INTO client_segment_cache (segment_key, segment_filter_json, client_count, calculated_at_utc, branch_timezone, error_summary, created_at_utc, updated_at_utc)
            VALUES ('by_master', ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(segment_key, segment_filter_json) DO UPDATE SET
                client_count=excluded.client_count,
                calculated_at_utc=excluded.calculated_at_utc,
                updated_at_utc=excluded.updated_at_utc
            """,
            (filter_json, summary.count, now, tz_name, now, now),
        )
        cache_after = await broadcasts_repo.fetchone("SELECT client_count, calculated_at_utc FROM client_segment_cache WHERE segment_key='by_master' AND segment_filter_json=?", (filter_json,))
        logger.info("master_segment_cache_refreshed actor_tg_id=%s selected_callback_master_id=%s yclients_staff_id=%s company_id=%s date_from=%s date_to=%s cache_count_before=%s cache_count_after=%s calculated_at_utc=%s records_count=%s unique_yclients_clients_count=%s", callback.from_user.id, master_id, debug_info.get("yclients_staff_id"), diag.get("company_id"), diag.get("date_from"), diag.get("date_to"), cache_before["client_count"] if cache_before else None, cache_after["client_count"] if cache_after else None, cache_after["calculated_at_utc"] if cache_after else None, diag.get("records_count"), len(clients))
        updated_local = segment_service._fmt_local(now, tz_name) or summary.updated_local
        await callback.message.edit_text(
            _format_segment_detail(_master_segment_title(debug_info.get("master_name")), summary.description, summary.count, updated_local, None, telegram_count=len(recipients)),
            reply_markup=_segment_detail_kb(_segment_refresh_callback("by_master", master_id), "broadcast:segments:by_master:picker", _segment_use_callback("by_master", master_id)),
        )
        logger.info("master_segment_resolve_finished actor_tg_id=%s selected_callback_master_id=%s data_source_used=%s raw_count=%s unique_client_count=%s", callback.from_user.id, master_id, "yclients_records_api", diag.get("records_count"), summary.count)
        logger.info("segment_summary_loaded user_id=%s role=%s segment_key=by_master elapsed_ms=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        logger.exception("master_segment_resolve_failed actor_tg_id=%s selected_callback_master_id=%s error_summary=%s", callback.from_user.id, master_id, str(exc)[:200])
        logger.exception("segment_summary_failed user_id=%s role=%s segment_key=by_master elapsed_ms=%s error=%s", callback.from_user.id, role, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await _send_segment_dev_diag(callback, action="segment_open", segment_key="by_master", master_id=master_id, endpoint="fetch_master_segment_clients", exc=exc)
        await callback.message.answer("⚠️ Не удалось загрузить клиентов мастера из YClients. Попробуйте позже.")
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:segments:by_service_category:'))
async def open_service_category_segment_detail(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    payload = callback.data.removeprefix('broadcast:segments:by_service_category:').strip()
    if not payload or payload == 'picker':
        await callback.message.answer(SEGMENT_STALE_TEXT)
        return await callback.answer()
    category_id, category_name_from_payload = (payload.split("|", 1) + [""])[:2]
    if not category_id:
        await callback.message.answer(SEGMENT_STALE_TEXT)
        return await callback.answer()
    started = time.perf_counter()
    await push_screen(state, "broadcast_segments_by_service_category_detail", payload={"category_id": category_id})
    category_name = category_name_from_payload.strip()
    logger.info("service_category_selected actor_tg_id=%s category_id=%s category_name=%s", callback.from_user.id, category_id, category_name)
    try:
        categories, _ = await segment_service.list_service_categories(actor_tg_id=callback.from_user.id)
        category_lookup = {str(item['id']): item['name'] for item in categories}
        category_name = category_name or category_lookup.get(category_id) or f"Категория {category_id}"
        logger.info("service_category_segment_yclients_fetch_started actor_tg_id=%s category_id=%s category_name=%s", callback.from_user.id, category_id, category_name)
        clients, diag = await segment_service.fetch_service_category_segment_clients(category_id, category_name, actor_tg_id=callback.from_user.id)
        filter_json = json.dumps({"category_id": category_id, "category_name": category_name}, ensure_ascii=False, separators=(",", ":"))
        cache_before = await broadcasts_repo.fetchone("SELECT client_count FROM client_segment_cache WHERE segment_key='by_service_category' AND segment_filter_json=?", (filter_json,))
        now = broadcasts_repo.now_iso()
        await broadcasts_repo.execute(
            """
            INSERT INTO client_segment_cache (segment_key, segment_filter_json, client_count, calculated_at_utc, branch_timezone, error_summary, created_at_utc, updated_at_utc)
            VALUES ('by_service_category', ?, ?, ?, 'Europe/Moscow', NULL, ?, ?)
            ON CONFLICT(segment_key, segment_filter_json) DO UPDATE SET
                client_count=excluded.client_count,
                calculated_at_utc=excluded.calculated_at_utc,
                updated_at_utc=excluded.updated_at_utc
            """,
            (filter_json, len(clients), now, now, now),
        )
        cache_after = await broadcasts_repo.fetchone("SELECT client_count FROM client_segment_cache WHERE segment_key='by_service_category' AND segment_filter_json=?", (filter_json,))
        updated_local = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y в %H:%M")
        text = f"✂️ Клиенты категории: {category_name}\n\nКлиенты, которые пользовались услугами из выбранной категории.\n\nКоличество клиентов: {len(clients)}\nОбновлено: {updated_local}"
        if not clients:
            text += "\n\n😌 В этом сегменте пока нет клиентов."
        await callback.message.edit_text(text, reply_markup=_segment_detail_kb(f"broadcast:segments:by_service_category:{category_id}", "broadcast:segments:by_service_category:picker", f"broadcast:segments:use:by_service_category:{category_id}"))
        logger.info("service_category_segment_yclients_fetch_finished actor_tg_id=%s company_id=%s category_id=%s category_name=%s records_count=%s unique_yclients_clients_count=%s", callback.from_user.id, diag.get("company_id"), category_id, category_name, diag.get("records_count"), len(clients))
        logger.info("service_category_cache_refreshed actor_tg_id=%s company_id=%s category_id=%s category_name=%s service_ids=%s cache_count_before=%s cache_count_after=%s", callback.from_user.id, diag.get("company_id"), category_id, category_name, diag.get("service_ids"), cache_before["client_count"] if cache_before else None, cache_after["client_count"] if cache_after else None)
    except Exception as exc:
        logger.exception("service_category_segment_failed actor_tg_id=%s category_id=%s elapsed_ms=%s error_summary=%s", callback.from_user.id, category_id, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await _send_segment_dev_diag(
            callback,
            action="segment_open",
            segment_key="by_service_category",
            endpoint="list_service_categories/fetch_service_category_segment_clients",
            exc=exc,
            extra_context={
                "category_id": category_id,
                "category_name": category_name,
                "callback_data": callback.data or "n/a",
                "actor_tg_id": callback.from_user.id,
            },
        )
        await callback.message.answer("⚠️ Не удалось загрузить категории услуг из YClients. Попробуйте позже.")
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:segments:'))
async def open_segment_detail(callback: CallbackQuery, state: FSMContext):
    role = await _ensure_segment_access(callback)
    if not role:
        return
    key = callback.data.removeprefix('broadcast:segments:').strip()
    if key not in SEGMENTS:
        logger.warning("segment_stale_callback user_id=%s role=%s callback_data=%s", callback.from_user.id, role, callback.data)
        await callback.message.answer(SEGMENT_STALE_TEXT)
        return await callback.answer()
    started = time.perf_counter()
    await push_screen(state, "broadcast_segment_detail", payload={"segment_key": key})
    logger.info("segment_button_clicked user_id=%s role=%s segment_key=%s", callback.from_user.id, role, key)
    if key == "birthday_soon":
        logger.info("birthday_segment_opened actor_tg_id=%s company_id=%s", callback.from_user.id, None)
    try:
        if key in {"all_clients", "active_30", "no_future_booking"}:
            await _render_live_yclients_segment_detail(callback, segment_key=key, audience_key=key)
            logger.info("segment_summary_loaded user_id=%s role=%s segment_key=%s elapsed_ms=%s source_used=yclients_live", callback.from_user.id, role, key, int((time.perf_counter() - started) * 1000))
            return await callback.answer()
        summary = await segment_service.get_segment_summary(key)
        text = _format_segment_detail(summary.title, summary.description, summary.count, summary.updated_local, summary.warning)
        if summary.auto_refresh_failed:
            text += "\n\n⚠️ Не удалось обновить данные автоматически. Показаны последние доступные данные."
        await callback.message.edit_text(text, reply_markup=_segment_detail_kb(_segment_refresh_callback(key), use_callback=_segment_use_callback(key)))
        logger.info("segment_summary_loaded user_id=%s role=%s segment_key=%s elapsed_ms=%s", callback.from_user.id, role, key, int((time.perf_counter() - started) * 1000))
        if key == "birthday_soon":
            logger.info("birthday_segment_cache_refreshed actor_tg_id=%s company_id=%s cache_count_before=%s cache_count_after=%s", callback.from_user.id, None, None, summary.count)
    except Exception as exc:
        endpoint = {
            "all_clients": "resolve_all_clients_from_yclients",
            "active_30": "resolve_active_clients_from_yclients",
            "no_future_booking": "resolve_no_future_booking_clients_from_yclients",
        }.get(key, "get_segment_summary")
        logger.exception("segment_summary_failed user_id=%s role=%s segment_key=%s elapsed_ms=%s error=%s", callback.from_user.id, role, key, int((time.perf_counter() - started) * 1000), str(exc)[:200])
        await _send_segment_dev_diag(callback, action="segment_open", segment_key=key, endpoint=endpoint, exc=exc)
        await callback.message.answer(SEGMENT_LOAD_FAILED_TEXT)
    await callback.answer()

@router.callback_query(F.data=='broadcast:section:one_time')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def open_one_time(callback:CallbackQuery,state:FSMContext):
    logger.info("one_time_broadcast_started user_id=%s", callback.from_user.id)
    await clear_state_preserving_navigation(state); await push_screen(state, 'one_time_broadcast_audience_selection'); await state.set_state(BroadcastStates.waiting_segment)
    await callback.message.edit_text('✉️ Разовая рассылка\n\nВыберите аудиторию 👇', reply_markup=one_time_audience_kb()); await callback.answer()

@router.callback_query(BroadcastStates.waiting_segment, F.data.startswith('broadcast:aud:'))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def pick_aud(callback:CallbackQuery,state:FSMContext):
    aud=callback.data.split(':')[-1]
    role = await resolve_role(callback.from_user.id)
    logger.info("audience_selected user_id=%s role=%s audience_key=%s", callback.from_user.id, role, aud)
    await _start_one_time_from_audience(callback, state, aud, role=role)

@router.message(BroadcastStates.waiting_text)
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def input_text(message:Message,state:FSMContext):
    text=(message.text or '').strip()
    if not text: return await message.answer('⚠️ Текст рассылки не может быть пустым. Введите сообщение.')
    if len(text)>MAX_MESSAGE_LEN: return await message.answer(f'⚠️ Слишком длинный текст. Максимум {MAX_MESSAGE_LEN} символов.')
    logger.info("broadcast_text_received user_id=%s text_len=%s", message.from_user.id, len(text))
    await state.update_data(text=text, photo_file_id=None); await state.set_state(BroadcastStates.waiting_photo_choice)
    await message.answer('Хотите добавить фото к рассылке?', reply_markup=photo_choice_kb())

@router.callback_query(BroadcastStates.waiting_photo_choice, F.data.in_({'broadcast:photo:add','broadcast:photo:skip'}))
async def photo_choice(callback:CallbackQuery,state:FSMContext):
    if callback.data.endswith('add'):
        await state.set_state(BroadcastStates.waiting_photo_upload); await callback.message.answer('Отправьте фото для рассылки.');
    else:
        await _show_preview(callback.message,state)
    await callback.answer()

@router.message(BroadcastStates.waiting_photo_upload)
async def photo_upload(message:Message,state:FSMContext):
    if not message.photo: return await message.answer('⚠️ Отправьте фото или нажмите "Без фото".')
    logger.info("broadcast_photo_received user_id=%s", message.from_user.id)
    await state.update_data(photo_file_id=message.photo[-1].file_id); await _show_preview(message,state)

async def _show_preview(target:Message,state:FSMContext):
    d=await state.get_data(); text=d.get('text',''); photo=d.get('photo_file_id'); recips=d.get('recipients',[]); aud=broadcasts_repo.audience_name(d.get('audience','—'))
    await state.set_state(BroadcastStates.waiting_preview)
    await push_screen(state, "one_time_broadcast_preview")
    if photo: await target.answer_photo(photo=photo, caption=text)
    else: await target.answer(text)
    logger.info("broadcast_preview_shown user_id=%s audience_key=%s audience_count=%s has_photo=%s", target.from_user.id if target.from_user else None, d.get("audience"), len(recips), bool(photo))
    await target.answer(f'👀 Предпросмотр рассылки\n\nАудитория: {aud}\nПолучателей: {len(recips)}\n\nОтправить рассылку?', reply_markup=preview_kb(bool(photo)))

@router.callback_query(BroadcastStates.waiting_preview, F.data=='broadcast:send:confirm')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def send_confirmed(callback:CallbackQuery,state:FSMContext):
    data = await state.get_data()
    if data.get("sending_locked"):
        await callback.message.answer("⚠️ Эта рассылка уже отправляется или была отправлена.")
        logger.info("broadcast_double_send_prevented by=%s", callback.from_user.id)
        return await callback.answer()
    await state.update_data(sending_locked=True)
    logger.info("broadcast_confirmed user_id=%s audience_key=%s", callback.from_user.id, data.get("audience"))
    actor_role = await resolve_role(callback.from_user.id)
    if not actor_role and callback.from_user.id == DEVELOPER_TG_ID:
        actor_role = ROLE_DEVELOPER
    ok, reason, now_local, next_window, tz = await broadcasts_repo.check_working_hours()
    if not ok:
        if callback.from_user.id == DEVELOPER_TG_ID:
            await callback.message.answer("⚠️ Сейчас филиал не работает. Режим разработчика позволяет отправить рассылку, но для обычных ролей отправка была бы заблокирована.")
        elif reason=='unavailable':
            await callback.message.answer('⚠️ Не удалось проверить рабочее время филиала. Рассылка не отправлена. Попробуйте позже или проверьте интеграцию YClients.')
            await state.update_data(sending_locked=False)
            return await callback.answer()
        else:
            d = await state.get_data()
            bid = await broadcasts_repo.create_campaign(
                created_by_tg_id=callback.from_user.id,
                created_by_role=actor_role,
                audience_key=d["audience"],
                text=d["text"],
                photo_file_id=d.get("photo_file_id"),
                total_count=len(d.get("recipients", [])),
                branch_timezone=tz,
                is_test=d["audience"] in {"self_test", "send_to_self"},
            )
            scheduled_at_utc, scheduled_local, _ = await broadcasts_repo.next_working_start_utc()
            await broadcasts_repo.execute("UPDATE broadcast_campaigns SET status='scheduled', sent_at_utc=? WHERE id=?", (scheduled_at_utc, bid))
            for item in d.get("recipients", []):
                await broadcasts_repo.execute(
                    "INSERT INTO broadcast_recipient_logs (campaign_id,recipient_tg_id,yclients_client_id,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?)",
                    (bid, item.get("user_id") or item.get("tg_id"), item.get("yclients_client_id"), "pending", broadcasts_repo.now_iso(), broadcasts_repo.now_iso()),
                )
            await _log_notification_action(callback.from_user.id, 'broadcast_scheduled', f'запланировал разовую рассылку по сегменту «{broadcasts_repo.audience_name(d["audience"])}»', audience_key=d.get("audience"), recipients_count=len(d.get("recipients", [])), campaign_id=bid)
            await state.clear()
            await callback.message.answer(
                f"⏰ Сейчас филиал не работает.\n\nРассылка запланирована на ближайшее рабочее время: {scheduled_local or (next_window or '—')}",
                reply_markup=broadcast_report_kb(),
            )
            return await callback.answer()
    d=await state.get_data()
    bid = await broadcasts_repo.create_campaign(
        created_by_tg_id=callback.from_user.id,
        created_by_role=actor_role,
        audience_key=d["audience"],
        text=d["text"],
        photo_file_id=d.get("photo_file_id"),
        total_count=len(d.get("recipients", [])),
        branch_timezone=tz,
        is_test=d["audience"] in {"self_test", "send_to_self"},
    )
    await broadcasts_repo.execute("UPDATE broadcast_campaigns SET status='sending' WHERE id=?", (bid,))
    logger.info("broadcast_send_started user_id=%s campaign_id=%s audience_key=%s audience_count=%s", callback.from_user.id, bid, d.get("audience"), len(d.get("recipients", [])))
    sent = failed = blocked = skipped = 0
    skipped_reasons: dict[str, int] = {}
    delivery_type = "green"
    actor_tg_id = callback.from_user.id
    for item in d.get("recipients", []):
        uid = item.get("user_id") or item.get("tg_id")
        local_user_id = item.get("local_user_id") or item.get("user_id")
        yclients_client_id = item.get("yclients_client_id")
        skip_reason = None
        if not uid:
            skipped += 1
            status = "skipped_no_tg_id"
            skip_reason = "нет Telegram ID"
            skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1
            logger.info("broadcast_recipient_skipped campaign_id=%s recipient_tg_id=%s local_user_id=%s yclients_client_id=%s audience_key=%s skip_reason=%s delivery_type=%s actor_tg_id=%s", bid, None, local_user_id, yclients_client_id, d.get("audience"), status, delivery_type, actor_tg_id)
            await broadcasts_repo.execute("INSERT INTO broadcast_recipient_logs (campaign_id,recipient_tg_id,yclients_client_id,status,error_code,error_summary,sent_at_utc,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)", (bid, None, yclients_client_id, status, "no_tg_id", skip_reason, None, broadcasts_repo.now_iso(), broadcasts_repo.now_iso()))
            continue
        status = "sent"; err_code = None; err_summary = None; sent_at = None
        explicit_unsubscribed = bool(item.get("unsubscribed_at_utc") or item.get("unsubscribe_source"))
        marketing_unsubscribed = int(item.get("marketing_unsubscribed") or 0) == 1
        if d["audience"] not in {"self_test", "send_to_self"} and marketing_unsubscribed and explicit_unsubscribed:
            status = "skipped_unsubscribed"; skipped += 1
            skip_reason = "отписались от акций"
            err_code = "unsubscribed"
            err_summary = skip_reason
            skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1
            logger.info("broadcast_recipient_skipped campaign_id=%s recipient_tg_id=%s local_user_id=%s yclients_client_id=%s audience_key=%s skip_reason=%s delivery_type=%s actor_tg_id=%s", bid, uid, local_user_id, yclients_client_id, d.get("audience"), status, delivery_type, actor_tg_id)
        else:
            try:
                if d.get("photo_file_id"):
                    await callback.bot.send_photo(uid, photo=d["photo_file_id"], caption=d["text"][:MAX_CAPTION_LEN])
                else:
                    await callback.bot.send_message(uid, d["text"])
                sent += 1; sent_at = broadcasts_repo.now_iso()
            except TelegramForbiddenError:
                blocked += 1; failed += 1; status = "blocked"; err_code = "forbidden"
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
                failed += 1; status = "failed"; err_code = "retry_after"; err_summary = str(exc)[:180]
            except TelegramBadRequest as exc:
                failed += 1; status = "skipped_invalid"; err_code = "bad_request"; err_summary = str(exc)[:180]
            except Exception as exc:
                failed += 1; status = "failed"; err_code = "unknown"; err_summary = str(exc)[:180]
                logger.exception("broadcast_recipient_failed campaign_id=%s recipient_tg_id=%s error=%s", bid, uid, str(exc)[:200])
        if status.startswith("skipped") and not skip_reason:
            skip_reason = err_summary or status
            skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1
            logger.info("broadcast_recipient_skipped campaign_id=%s recipient_tg_id=%s local_user_id=%s yclients_client_id=%s audience_key=%s skip_reason=%s delivery_type=%s actor_tg_id=%s", bid, uid, local_user_id, yclients_client_id, d.get("audience"), status, delivery_type, actor_tg_id)
        await broadcasts_repo.execute("INSERT INTO broadcast_recipient_logs (campaign_id,recipient_tg_id,yclients_client_id,status,error_code,error_summary,sent_at_utc,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)", (bid, uid, yclients_client_id, status, err_code, (skip_reason or err_summary), sent_at, broadcasts_repo.now_iso(), broadcasts_repo.now_iso()))
        await asyncio.sleep(0.05)
    await broadcasts_repo.execute("UPDATE broadcast_campaigns SET status='sent', sent_count=?, failed_count=?, blocked_count=?, skipped_count=?, sent_at_utc=?, branch_local_sent_at=? WHERE id=?", (sent, failed, blocked, skipped, broadcasts_repo.now_iso(), now_local, bid))
    logger.info("broadcast_send_finished user_id=%s campaign_id=%s sent=%s failed=%s blocked=%s skipped=%s", callback.from_user.id, bid, sent, failed, blocked, skipped)
    await state.clear();
    logger.info("broadcast_fsm_cleared user_id=%s campaign_id=%s", callback.from_user.id, bid)
    skipped_reasons_text = ""
    if skipped_reasons:
        reason_lines = "\n".join([f"— {reason}: {count}" for reason, count in sorted(skipped_reasons.items(), key=lambda x: x[0])])
        skipped_reasons_text = f"\nПричины:\n{reason_lines}"
    await _log_notification_action(callback.from_user.id, 'broadcast_sent', f'отправил разовую рассылку по сегменту «{broadcasts_repo.audience_name(d["audience"])}»', audience_key=d.get("audience"), recipients_count=len(d.get("recipients", [])), sent=sent, failed=failed, campaign_id=bid)
    await callback.message.answer(f"✅ Рассылка завершена\n\nАудитория: {broadcasts_repo.audience_name(d['audience'])}\nВсего клиентов: {len(d.get('recipients', []))}\nОтправлено: {sent}\nОшибок: {failed}\nЗаблокировали бота: {blocked}\nПропущено: {skipped}{skipped_reasons_text}", reply_markup=broadcast_report_kb())
    await callback.answer()

@router.callback_query(F.data == "broadcast:history")
@router.callback_query(F.data == "broadcast:history:root")
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def broadcast_history_root(callback: CallbackQuery, state: FSMContext):
    logger.info('notification_history_opened by=%s', callback.from_user.id)
    await push_screen(state, 'broadcast_history_root')
    await callback.message.edit_text('📜 История уведомлений\n\nЗдесь видно, какие уведомления бот отправлял клиентам: автоматические воронки, ручные рассылки и результат доставки.', reply_markup=notification_history_root_kb())
    await callback.answer()


@router.callback_query(F.data.startswith('broadcast:history:list:'))
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def broadcast_history_list(callback: CallbackQuery, state: FSMContext):
    _,_,_,fkey,pg = callback.data.split(':')
    page = max(1, int(pg))
    is_dev = callback.from_user.id == DEVELOPER_TG_ID
    logger.info('notification_history_filter_opened by=%s filter=%s page=%s', callback.from_user.id, fkey, page)
    default_tz = await _history_default_timezone()
    rows = await get_notification_history(fkey if fkey != 'all' else None, page, 11, include_test=is_dev, default_timezone=default_tz)
    shown = rows[:10]
    has_next = len(rows) > 10
    lines = [_history_filter_title(fkey)]
    if not shown:
        lines.append('\nПока записей нет.')
    for i, row in enumerate(shown, start=1 + (page-1)*10):
        lines.extend(_format_history_row(row, i))
    await push_screen(state, f'broadcast_history_{fkey}_{page}')
    await callback.message.edit_text('\n'.join(lines)[:3900], reply_markup=history_list_kb(fkey, page, has_next))
    await callback.answer()


_AUTOMATION_KEYS = {
    'post_visit_review',
    'cancellation_return',
    'lost_clients',
    'birthday',
    'repeat_visit',
    'anti_spam',
    'review_links',
    'quiet_hours',
}


def _automation_edit_prompt(key: str, field: str, current: object) -> str:
    hints = {
        'delay_hours': 'Введите положительное число часов:',
        'min_interval_hours': 'Введите положительное число часов:',
        'delay_days': 'Введите положительное число дней:',
        'send_days_before': 'Введите положительное число дней:',
        'max_weekly_marketing': 'Введите положительное число сообщений в неделю:',
        'threshold_days': 'Введите три срока через /, например 30/60/90:',
        'range': 'Введите интервал в формате HH:MM-HH:MM, например 21:00-09:00:',
        'yandex_url': 'Введите ссылку Яндекс или отправьте пустое значение для очистки:',
        'two_gis_url': 'Введите ссылку 2ГИС или отправьте пустое значение для очистки:',
    }
    prompt = hints.get(field, 'Введите новый текст:')
    return f'Текущее значение: {current or "—"}\n\n{prompt}'


async def _automation_allowed(event: CallbackQuery | Message) -> bool:
    if await has_any_role(event.from_user.id, {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}):
        return True
    logger.warning('automation_settings_access_denied tg_id=%s', event.from_user.id)
    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.answer('⛔ Раздел недоступен.')
        await event.answer()
    else:
        await event.answer('⛔ Раздел недоступен.')
    return False


async def _safe_branch_timezone() -> str:
    try:
        ys = await get_yclients_settings()
        if ys and ys.company_id:
            return (await resolve_company_timezone(ys.company_id)).timezone_name
    except Exception:
        logger.exception('automation_settings_timezone_failed')
    return 'не настроен'


async def _safe_edit_text(target: Message, text: str, keyboard: InlineKeyboardMarkup) -> None:
    try:
        await target.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            return
        logger.info('automation_settings_edit_fallback reason=%s', exc)
        await target.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == 'broadcast:settings:root')
async def open_automation_settings_root(callback: CallbackQuery, state: FSMContext):
    if not await _automation_allowed(callback):
        return
    logger.info('automation_settings_opened by=%s', callback.from_user.id)
    await push_screen(state, 'broadcast_settings_root')
    text = (
        '⚙️ Настройки рассылок\n\n'
        'Здесь настраиваются автоматические уведомления и правила рассылок. '
        'Бот будет сам возвращать клиентов по заданным сценариям.'
    )
    await _safe_edit_text(callback.message, text, automation_root_kb())
    await callback.answer()


async def _render_automation_module(target: Message, key: str):
    if key not in _AUTOMATION_KEYS:
        logger.warning('automation_unknown_key key=%s', key)
        await target.answer('⚠️ Раздел настроек не найден.', reply_markup=automation_root_kb())
        return
    try:
        s = await get_setting(key)
        tz = await _safe_branch_timezone() if key == 'quiet_hours' else '—'
        texts = {
            'post_visit_review': (
                f"⭐ Оценка после визита\n\n"
                f"Статус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\n"
                f"Задержка после визита: {s.get('delay_hours', 2)} ч\n"
                f"Текст сообщения:\n{s.get('message_text', '')}\n\n"
                "Положительная оценка: 4–5 ⭐ → клиенту показываются ссылки на отзывы.\n"
                "Негативная оценка: 1–3 ⭐ → бот попросит комментарий и передаст администратору."
            ),
            'cancellation_return': (
                f"❌ Возврат после отмены\n\n"
                f"Статус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\n"
                f"Задержка после отмены: {s.get('delay_hours', 2)} ч\n"
                f"Текст сообщения:\n{s.get('message_text', '')}\n\n"
                "Правило: отправлять только если у клиента нет новой будущей записи."
            ),
            'lost_clients': (
                f"😔 Потерянные клиенты\n\n"
                f"Статус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\n"
                f"Сроки: {' / '.join(map(str, s.get('threshold_days') or [30, 60, 90]))} дней\n"
                "Правило: не отправлять, если у клиента есть будущая запись.\n\n"
                f"30 дней:\n{s.get('text_30', '')}\n\n"
                f"60 дней:\n{s.get('text_60', '')}\n\n"
                f"90 дней:\n{s.get('text_90', '')}"
            ),
            'birthday': (
                f"🎂 День рождения\n\n"
                f"Статус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\n"
                f"Отправлять за: {s.get('send_days_before', 7)} дней\n"
                f"Текст сообщения:\n{s.get('message_text', '')}\n\n"
                "Кнопка в уведомлении: ✂️ Записаться\n"
                "Комментарий в YClients при записи: У КЛИЕНТА ДЕНЬ РОЖДЕНИЕ - НУЖНО СДЕЛАТЬ СКИДКУ"
            ),
            'repeat_visit': (
                f"🔁 Повторный визит\n\n"
                f"Статус: {'✅ Включено' if s.get('enabled') else '❌ Выключено'}\n"
                f"Срок по умолчанию: {s.get('delay_days', 30)} дней\n"
                "Правило: напоминать только если нет будущей записи.\n\n"
                + '\n\n'.join(f"Текст {i}:\n{txt}" for i, txt in enumerate((s.get('templates') or [])[:5], 1))
            ),
            'anti_spam': (
                f"🔕 Антиспам\n\n"
                f"Лимит зелёных сообщений в неделю: {s.get('max_weekly_marketing', 2)}\n"
                f"Минимальный интервал между зелёными сообщениями: {s.get('min_interval_hours', 48)} ч\n"
                "Отписка: зелёные уведомления не отправляются отписавшимся клиентам.\n\n"
                "ℹ️ Подробности по белым и зелёным уведомлениям: кнопка ниже."
            ),
            'review_links': (
                f"🔗 Ссылки на отзывы\n\n"
                "Используются после оценки 4–5 ⭐.\n\n"
                f"Яндекс: {s.get('yandex_url') or '—'}\n"
                f"2ГИС: {s.get('two_gis_url') or '—'}"
            ),
            'quiet_hours': (
                "⏰ Рабочее время и тихие часы\n\n"
                f"Часовой пояс филиала: {tz}\n"
                "Рабочее время филиала берётся из YClients.\n"
                f"Статус тихих часов: {'✅ Включены' if s.get('enabled') else '❌ Выключены'}\n"
                f"Тихие часы: {s.get('start', '21:00')}–{s.get('end', '09:00')}\n"
                f"Режим вне рабочего времени: {s.get('outside_allowed_behavior', 'postpone_to_next_allowed')}\n\n"
                "Зелёные уведомления не отправляются в нерабочее время.\n"
                "Белые уведомления по записи отправляются всегда."
            ),
        }
        enabled = bool(s.get('enabled', False))
        toggle_text = '⛔ Выключить' if enabled else '✅ Включить'
        if key == 'quiet_hours':
            toggle_text = '☀️ Выключить тихие часы' if enabled else '🌙 Включить тихие часы'
        rows = []
        if key in {'post_visit_review','cancellation_return','lost_clients','birthday','repeat_visit','quiet_hours'}:
            rows.append([InlineKeyboardButton(text=toggle_text, callback_data=f'broadcast:settings:toggle:{key}')])
        mapping = {
            'post_visit_review': [('⏱ Изменить задержку','delay_hours'),('✏️ Изменить текст','message_text'),('🔗 Ссылки на отзывы','go_review_links')],
            'cancellation_return': [('⏱ Изменить задержку','delay_hours'),('✏️ Изменить текст','message_text')],
            'lost_clients': [('⏱ Настроить сроки','threshold_days'),('✏️ Текст 30 дней','text_30'),('✏️ Текст 60 дней','text_60'),('✏️ Текст 90 дней','text_90')],
            'birthday': [('📅 Изменить срок отправки','send_days_before'),('✏️ Изменить текст','message_text')],
            'repeat_visit': [('⏱ Изменить срок по умолчанию','delay_days'),('✏️ Текст 1','template_1'),('✏️ Текст 2','template_2'),('✏️ Текст 3','template_3'),('✏️ Текст 4','template_4'),('✏️ Текст 5','template_5')],
            'anti_spam': [('🔢 Изменить лимит в неделю','max_weekly_marketing'),('⏱ Изменить минимальный интервал','min_interval_hours'),('ℹ️ Белые и зелёные уведомления','white_green_info')],
            'review_links': [('🟡 Изменить ссылку Яндекс','yandex_url'),('🟢 Изменить ссылку 2ГИС','two_gis_url'),('🧹 Очистить ссылку Яндекс','clear_yandex'),('🧹 Очистить ссылку 2ГИС','clear_two_gis')],
            'quiet_hours': [('⏰ Изменить тихие часы','range'),('📌 Режим вне рабочего времени','outside_allowed_behavior')],
        }
        for label, field in mapping[key]:
            rows.append([InlineKeyboardButton(text=label, callback_data=f'broadcast:settings:edit:{key}:{field}')])
        back_callback = 'broadcast:settings:root' if key == 'lost_clients' else 'nav:back'
        await _safe_edit_text(target, texts[key], _with_nav(rows, back_callback=back_callback))
    except Exception:
        logger.exception('automation_module_render_failed key=%s', key)
        await target.answer('⚠️ Не удалось открыть настройки. Попробуйте позже.', reply_markup=automation_root_kb())


@router.callback_query(F.data.startswith('broadcast:settings:') & ~F.data.startswith('broadcast:settings:root') & ~F.data.startswith('broadcast:settings:toggle:') & ~F.data.startswith('broadcast:settings:edit:'))
async def open_automation_module(callback: CallbackQuery, state: FSMContext):
    if not await _automation_allowed(callback):
        return
    key = callback.data.split(':')[-1]
    logger.info('automation_module_opened key=%s by=%s', key, callback.from_user.id)
    await push_screen(state, f'broadcast_settings_{key}')
    await _render_automation_module(callback.message, key)
    await callback.answer()


@router.callback_query(F.data == 'broadcast:history:search')
@require_roles(ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER)
async def broadcast_history_search_start(callback: CallbackQuery, state: FSMContext):
    logger.info('notification_history_search_started by=%s', callback.from_user.id)
    await state.set_state(NotificationHistoryStates.waiting_client_query)
    await callback.message.answer('Введите имя, телефон или Telegram ID клиента:', reply_markup=history_search_kb())
    await callback.answer()


@router.message(NotificationHistoryStates.waiting_client_query)
async def broadcast_history_search_input(message: Message, state: FSMContext):
    q = (message.text or '').strip()
    is_dev = message.from_user.id == DEVELOPER_TG_ID
    if not await has_any_role(message.from_user.id, {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}):
        await message.answer('⛔ Раздел недоступен.')
        await state.clear()
        return
    try:
        yclients_client = await _find_history_yclients_client(q)
    except YClientsError:
        logger.exception('notification_history_search_yclients_failed by=%s q=%s', message.from_user.id, q)
        await message.answer('⚠️ Не удалось проверить клиента в YClients. Попробуйте позже.', reply_markup=history_search_kb())
        return
    if not yclients_client:
        logger.info('notification_history_search_client_not_found by=%s q=%s', message.from_user.id, q)
        await message.answer('😔 Клиент не найден.', reply_markup=history_search_kb())
        return

    yclients_client_id = _history_client_id(yclients_client)
    client_name = _history_client_name(yclients_client)
    client_phone = _history_client_phone(yclients_client)
    client_phone_keys = _history_phone_keys(client_phone)
    tg_mapping = await _find_history_telegram_mapping(yclients_client_id, client_phone)

    default_tz = await _history_default_timezone()
    rows = await get_notification_history(None, 1, 200, include_test=is_dev, default_timezone=default_tz)
    matched = [r for r in rows if _history_row_matches_client(r, yclients_client_id, client_phone_keys)]

    lines = [f'✅ Клиент найден: {client_name}', f'📞 Телефон: {_format_ru_phone(client_phone)}']
    if tg_mapping:
        username = str(tg_mapping.get('username') or '').strip()
        username_text = f'@{username}' if username and not username.startswith('@') else (username or '—')
        lines.append(f'👤 Telegram: {username_text} / TG ID: {tg_mapping.get("user_id")}')
    else:
        lines.append('Telegram-связка пока не найдена.')

    if not matched:
        lines.append('📭 Уведомлений по этому клиенту пока нет.')
        logger.info('notification_history_search_client_found_no_history by=%s q=%s yclients_client_id=%s', message.from_user.id, q, yclients_client_id)
        await message.answer('\n'.join(lines), reply_markup=history_search_kb())
        await state.clear()
        return

    lines.append('\n📜 Уведомления по клиенту')
    for i,row in enumerate(matched[:10],1):
        lines.extend(_format_history_row(row, i))
    await message.answer('\n'.join(lines), reply_markup=history_search_kb())
    await state.clear()
@router.callback_query(F.data.startswith('broadcast:settings:toggle:'))
async def toggle_automation_module(callback: CallbackQuery):
    if not await _automation_allowed(callback):
        return
    key = callback.data.split(':')[-1]
    s = await get_setting(key); s['enabled'] = not bool(s.get('enabled', False))
    await upsert_setting(key, s, updated_by_tg_id=callback.from_user.id)
    await _log_notification_action(callback.from_user.id, 'automation_toggled', f'{"включил" if s.get("enabled") else "выключил"} автоматизацию «{_automation_title(key)}»', setting_key=key, enabled=bool(s.get('enabled')))
    logger.info('automation_setting_changed key=%s field=enabled by=%s', key, callback.from_user.id)
    await _render_automation_module(callback.message, key)
    await callback.answer('Сохранено ✅')

@router.callback_query(F.data.startswith('broadcast:settings:edit:'))
async def edit_automation_setting(callback: CallbackQuery, state: FSMContext):
    if not await _automation_allowed(callback):
        return
    _, _, _, key, field = callback.data.split(':', 4)
    if field == 'go_review_links':
        await _render_automation_module(callback.message, 'review_links'); return await callback.answer()
    if field == 'white_green_info':
        await callback.message.answer(WHITE_GREEN_INFO_TEXT, reply_markup=white_green_info_kb())
        return await callback.answer()
    if field in {'clear_yandex', 'clear_two_gis'}:
        s = await get_setting('review_links'); s['yandex_url' if field == 'clear_yandex' else 'two_gis_url'] = ''
        await upsert_setting('review_links', s, updated_by_tg_id=callback.from_user.id)
        await _log_notification_action(callback.from_user.id, 'review_link_changed', f'очистил ссылку на отзывы {"Яндекс" if field == "clear_yandex" else "2ГИС"}', setting_key='review_links', field=field)
        logger.info('automation_setting_changed key=review_links field=%s by=%s', field, callback.from_user.id)
        await _render_automation_module(callback.message, 'review_links'); return await callback.answer('Сохранено ✅')
    if key == 'repeat_visit' and field == 'service_rules':
        await callback.message.answer('🧾 Правила по услугам\n\nMVP: хранилище правил уже готово (settings.repeat_visit.service_rules). UI детальной настройки услуг будет добавлен отдельным шагом.')
        return await callback.answer()
    current = (await get_setting(key)).get(field, '')
    if key == 'repeat_visit' and field.startswith('template_'):
        idx = int(field.split('_')[-1]) - 1
        templates = (await get_setting(key)).get('templates') or []
        current = templates[idx] if idx < len(templates) else ''
    await state.set_state(AutomationEditStates.waiting_input)
    await state.update_data(automation_edit_key=key, automation_edit_field=field)
    await callback.message.answer(_automation_edit_prompt(key, field, current), reply_markup=_with_nav([]))
    await callback.answer()

@router.message(AutomationEditStates.waiting_input)
async def save_automation_setting(message: Message, state: FSMContext):
    if not await _automation_allowed(message):
        return
    if (message.text or '').strip() in {BACK_BTN, '⬅️ Назад'}:
        data = await state.get_data(); key = data.get('automation_edit_key') or 'post_visit_review'
        await clear_state_preserving_navigation(state)
        await _render_automation_module(message, key)
        return
    if (message.text or '').strip() == '🏠 Главное меню':
        await state.clear()
        await render_main_menu(message, message.from_user.id)
        return
    data = await state.get_data(); key = data.get('automation_edit_key'); field = data.get('automation_edit_field'); raw = (message.text or '').strip()
    s = await get_setting(key)
    try:
        if field in {'delay_hours', 'min_interval_hours'}: value = int(raw); assert 1 <= value <= 720
        elif field in {'delay_days', 'send_days_before', 'max_weekly_marketing'}: value = int(raw); assert 1 <= value <= 365
        elif field == 'threshold_days': value = [int(x.strip()) for x in re.split(r'[/,\s]+', raw) if x.strip()]; assert len(value) == 3 and all(1 <= x <= 365 for x in value)
        elif key == 'repeat_visit' and field.startswith('template_'): value = raw; assert len(value) <= MAX_TEXT_LEN
        elif field == 'range':
            assert re.match(r'^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$', raw)
            start, end = raw.split('-'); s['start'] = start; s['end'] = end; value = None
        elif field in {'yandex_url', 'two_gis_url'}:
            if raw and not _url_like(raw):
                logger.warning('automation_validation_error key=%s field=%s by=%s', key, field, message.from_user.id)
                return await message.answer('⚠️ Похоже, это не ссылка. Проверьте и попробуйте ещё раз.')
            value = raw
        else:
            assert raw and len(raw) <= MAX_TEXT_LEN; value = raw
    except Exception:
        logger.warning('automation_validation_error key=%s field=%s by=%s', key, field, message.from_user.id)
        return await message.answer('⚠️ Некорректное значение. Проверьте и попробуйте ещё раз.')
    if key == 'repeat_visit' and field.startswith('template_'):
        idx = int(field.split('_')[-1]) - 1
        templates = list(s.get('templates') or [])
        while len(templates) < 5:
            templates.append('')
        templates[idx] = value
        s['templates'] = templates
    elif field != 'range':
        s[field] = value
    await upsert_setting(key, s, updated_by_tg_id=message.from_user.id)
    field_titles = {'message_text': 'текст автоматического сообщения', 'delay_hours': 'время отправки автоматизации', 'delay_days': 'время отправки автоматизации', 'send_days_before': 'время отправки автоматизации', 'min_interval_hours': 'антиспам-лимит', 'max_weekly_marketing': 'антиспам-лимит', 'range': 'тихие часы', 'yandex_url': 'ссылку на отзывы Яндекс', 'two_gis_url': 'ссылку на отзывы 2ГИС'}
    await _log_notification_action(message.from_user.id, 'automation_setting_changed', f'изменил {field_titles.get(field, "настройку уведомлений")}', setting_key=key, field=field)
    logger.info('automation_setting_changed key=%s field=%s by=%s', key, field, message.from_user.id)
    await clear_state_preserving_navigation(state)
    await message.answer('Сохранено ✅')
    await _render_automation_module(message, key)

@router.callback_query(F.data.startswith('feedback_rate:'))
async def handle_post_visit_rating(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(':')
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        logger.info('post_visit_feedback_stale_callback user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, None, None, False)
        return await callback.answer('⚠️ Эта оценка уже обработана или устарела.', show_alert=True)
    event_id = int(parts[1]); rating = int(parts[2])
    event = await get_event(event_id)
    is_test = _is_dev_feedback_event(event)
    if not event or int(event.get('client_tg_id') or 0) != callback.from_user.id or event.get('status') not in {'sent', 'pending'}:
        logger.info('post_visit_feedback_stale_callback user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, rating, bool(is_test))
        return await callback.answer(DEV_TEST_STALE_TEXT if is_test else '⚠️ Эта оценка уже обработана или устарела.', show_alert=True)
    if is_test and callback.from_user.id != DEVELOPER_TG_ID:
        logger.warning('dev_rating_test_wrong_user user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, rating, True)
        return await callback.answer(DEV_TEST_STALE_TEXT, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        logger.info('post_visit_feedback_rating_markup_remove_failed user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, rating, bool(is_test))
    logger.info('post_visit_feedback_rating_selected user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, rating, bool(is_test))
    if rating >= 4:
        await set_status(event_id, 'rated_positive', rating=rating, rated_at_utc=datetime.now(timezone.utc).isoformat())
        links = await get_setting('review_links')
        rows = []
        if links.get('yandex_url'):
            rows.append([InlineKeyboardButton(text='🟡 Оставить отзыв в Яндекс', url=links['yandex_url'])])
        if links.get('two_gis_url'):
            rows.append([InlineKeyboardButton(text='🟢 Оставить отзыв в 2ГИС', url=links['two_gis_url'])])
        rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='nav:home')])
        msg = 'Спасибо за высокую оценку 😊\n\nБудем очень благодарны, если вы оставите отзыв. Это помогает нам становиться лучше и расти.' if rows[:-1] else 'Спасибо за высокую оценку 😊\n\nСсылки на отзывы пока не настроены.'
        await callback.message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    else:
        await set_status(event_id, 'waiting_negative_comment', rating=rating, rated_at_utc=datetime.now(timezone.utc).isoformat())
        await state.set_state(PostVisitFeedbackStates.waiting_negative_comment)
        await state.update_data(feedback_event_id=event_id)
        await callback.message.answer('Спасибо, что честно рассказали 🙏\n\nНапишите, пожалуйста, что пошло не так.')
    await callback.answer()


@router.message(PostVisitFeedbackStates.waiting_negative_comment)
async def handle_negative_comment(message: Message, state: FSMContext):
    data = await state.get_data(); event_id = int(data.get('feedback_event_id') or 0)
    if (message.text or '').strip() in {'🏠 Главное меню', '/menu'}:
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='menu_during_negative_comment')
        return await message.answer('Возвращаемся в меню.', reply_markup=None)
    if not message.text:
        return await message.answer('Пожалуйста, отправьте комментарий текстом.')
    event = await get_event(event_id)
    is_test = _is_dev_feedback_event(event)
    if not event or event.get('status') != 'waiting_negative_comment':
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='stale_negative_comment', is_test=bool(is_test))
        return await message.answer(DEV_TEST_STALE_TEXT if is_test else '⚠️ Эта оценка уже обработана или устарела.')
    if is_test and message.from_user.id != DEVELOPER_TG_ID:
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='wrong_user_test_comment', rating=event.get('rating'), is_test=True)
        return await message.answer(DEV_TEST_STALE_TEXT)
    comment = message.text.strip()
    await set_status(event_id, 'negative_comment_received', client_comment=comment, comment_at_utc=datetime.now(timezone.utc).isoformat())
    logger.info('post_visit_feedback_negative_comment_received user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', message.from_user.id, event_id, event.get('rating'), bool(is_test))
    await message.answer('Спасибо. Мы получили ваш комментарий и постараемся разобраться 🙏')
    if is_test:
        targets = [DEVELOPER_TG_ID]
    else:
        staff = await list_staff()
        targets = [int(s['tg_id']) for s in staff if s.get('role') in {'developer','admin','manager'}]
    updated_event = await get_event(event_id) or event
    card = _render_post_visit_admin_alert(updated_event, comment)
    kb = _post_visit_admin_kb(event_id, is_test=bool(is_test))
    for tg_id in set(targets):
        if is_test and tg_id != DEVELOPER_TG_ID:
            continue
        try:
            await message.bot.send_message(tg_id, card, reply_markup=kb)
            logger.info('post_visit_feedback_admin_alert_sent user_id=%s event_id=%s rating=%s is_test=%s source=dev_test target_tg_id=%s', message.from_user.id, event_id, event.get('rating'), bool(is_test), tg_id)
        except Exception:
            logger.exception('post_visit_feedback_admin_alert_failed event_id=%s tg_id=%s is_test=%s source=dev_test', event_id, tg_id, bool(is_test))
    await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='negative_comment_received', rating=event.get('rating'), is_test=bool(is_test))


@router.callback_query(F.data.startswith('feedback_admin_reply:'))
async def start_admin_reply(callback: CallbackQuery, state: FSMContext):
    if not await has_any_role(callback.from_user.id, {ROLE_DEVELOPER, ROLE_ADMIN, ROLE_MANAGER}):
        return await callback.answer('⛔ Нет доступа', show_alert=True)
    event_id_raw = callback.data.split(':')[-1]
    if not event_id_raw.isdigit():
        return await callback.answer(DEV_TEST_STALE_TEXT, show_alert=True)
    event_id = int(event_id_raw)
    event = await get_event(event_id)
    is_test = _is_dev_feedback_event(event)
    if not event or event.get('status') != 'negative_comment_received' or (is_test and callback.from_user.id != DEVELOPER_TG_ID):
        logger.info('post_visit_feedback_stale_callback user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, event.get('rating') if event else None, bool(is_test))
        return await callback.answer(DEV_TEST_STALE_TEXT, show_alert=True)
    await state.set_state(PostVisitFeedbackStates.waiting_admin_reply)
    await state.update_data(admin_reply_event_id=event_id)
    logger.info('post_visit_feedback_admin_reply_started user_id=%s event_id=%s rating=%s is_test=%s source=dev_test', callback.from_user.id, event_id, event.get('rating'), bool(is_test))
    await callback.message.answer('Введите ответ клиенту:')
    await callback.answer()


@router.message(PostVisitFeedbackStates.waiting_admin_reply)
async def submit_admin_reply(message: Message, state: FSMContext):
    data = await state.get_data(); event_id = int(data.get('admin_reply_event_id') or 0)
    if (message.text or '').strip() in {'🏠 Главное меню', '/menu'}:
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='menu_during_admin_reply')
        return await message.answer('Возвращаемся в меню.', reply_markup=None)
    event = await get_event(event_id)
    is_test = _is_dev_feedback_event(event)
    if not event or event.get('status') != 'negative_comment_received' or not message.text:
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='stale_admin_reply', is_test=bool(is_test))
        return await message.answer(DEV_TEST_STALE_TEXT if is_test else '⚠️ Событие устарело.')
    if is_test and message.from_user.id != DEVELOPER_TG_ID:
        await _clear_fsm_with_log(state, user_id=message.from_user.id, event_id=event_id, reason='wrong_user_test_admin_reply', rating=event.get('rating'), is_test=True)
        return await message.answer(DEV_TEST_STALE_TEXT)
    reply_text = message.text.strip()
    await state.set_state(PostVisitFeedbackStates.waiting_admin_reply_confirm)
    await state.update_data(admin_reply_event_id=event_id, admin_reply_text=reply_text)
    await message.answer(f'Отправить клиенту такой ответ?\n\n{reply_text}', reply_markup=_admin_reply_confirm_kb())


@router.callback_query(F.data.startswith('feedback_admin_reply_confirm:'))
async def confirm_admin_reply(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(':')[-1]
    data = await state.get_data()
    event_id = int(data.get('admin_reply_event_id') or 0)
    reply_text = str(data.get('admin_reply_text') or '').strip()
    event = await get_event(event_id)
    is_test = _is_dev_feedback_event(event)
    if action == 'edit':
        await state.set_state(PostVisitFeedbackStates.waiting_admin_reply)
        await callback.message.answer('Введите ответ клиенту:')
        return await callback.answer()
    if action == 'back':
        await _clear_fsm_with_log(state, user_id=callback.from_user.id, event_id=event_id, reason='admin_reply_back', rating=event.get('rating') if event else None, is_test=bool(is_test))
        return await callback.answer('Отменено')
    if action != 'send' or not event or event.get('status') != 'negative_comment_received' or not reply_text:
        await _clear_fsm_with_log(state, user_id=callback.from_user.id, event_id=event_id, reason='stale_admin_reply_confirm', rating=event.get('rating') if event else None, is_test=bool(is_test))
        return await callback.answer(DEV_TEST_STALE_TEXT if is_test else '⚠️ Событие устарело.', show_alert=True)
    if is_test and callback.from_user.id != DEVELOPER_TG_ID:
        await _clear_fsm_with_log(state, user_id=callback.from_user.id, event_id=event_id, reason='wrong_user_test_admin_reply_confirm', rating=event.get('rating'), is_test=True)
        return await callback.answer(DEV_TEST_STALE_TEXT, show_alert=True)
    try:
        target_tg_id = DEVELOPER_TG_ID if is_test else int(event['client_tg_id'])
        await callback.bot.send_message(target_tg_id, f'💬 Ответ от команды барбершопа:\n\n{reply_text}')
        await set_status(event_id, 'admin_replied', admin_reply=reply_text, admin_replied_at_utc=datetime.now(timezone.utc).isoformat())
        logger.info('post_visit_feedback_admin_reply_sent user_id=%s event_id=%s rating=%s is_test=%s source=dev_test target_tg_id=%s', callback.from_user.id, event_id, event.get('rating'), bool(is_test), target_tg_id)
        await callback.message.answer('✅ Тестовый ответ отправлен.' if is_test else '✅ Ответ отправлен клиенту.')
    except TelegramForbiddenError:
        await set_status(event_id, 'failed')
        await callback.message.answer('⚠️ Не удалось отправить: клиент заблокировал бота.')
    await _clear_fsm_with_log(state, user_id=callback.from_user.id, event_id=event_id, reason='admin_reply_sent', rating=event.get('rating'), is_test=bool(is_test))
    await callback.answer()


@router.callback_query(F.data.startswith('cancel_recovery:'))
async def handle_cancellation_recovery_click(callback: CallbackQuery, state: FSMContext):
    _, action, event_raw = (callback.data or '').split(':')
    if not event_raw.isdigit():
        return await callback.answer('⚠️ Это уведомление уже устарело. Вы можете открыть запись через главное меню.', show_alert=True)
    event_id = int(event_raw)
    event = await get_cancel_event(event_id)
    if not event or int(event.get('client_tg_id') or 0) != callback.from_user.id or event.get('status') not in {'sent', 'pending'}:
        await callback.answer('⚠️ Это уведомление уже устарело. Вы можете открыть запись через главное меню.', show_alert=True)
        return
    now = datetime.now(timezone.utc).isoformat()
    if action in {'rebook', 'date'}:
        await set_cancel_status(event_id, 'clicked_rebook', clicked_at_utc=now)
        await open_booking_from_notification(
            callback,
            state,
            funnel_type='cancellation_recovery',
            notification_event_id=event_id,
            yclients_client_id=str(event.get('yclients_client_id') or '') or None,
            is_test=bool(int(event.get('is_test') or 0)),
            source=str(event.get('source') or '') or None,
            entry='datetime_first' if action == 'date' else 'hub',
        )
        return
    if action == 'later':
        await set_cancel_status(event_id, 'clicked_later', clicked_at_utc=now)
        await callback.message.answer('Хорошо, будем ждать вас позже 😊')
        await callback.answer('Принято ✅')
        return
    await callback.answer('⚠️ Это уведомление уже устарело. Вы можете открыть запись через главное меню.', show_alert=True)
@router.callback_query(F.data == 'broadcast:section:efficiency')
async def open_efficiency(callback: CallbackQuery, state: FSMContext):
    logger.info('effectiveness_opened by=%s', callback.from_user.id)
    await push_screen(state, 'broadcast_efficiency')
    await _render_eff(callback, 30)


@router.callback_query(F.data.startswith('broadcast:eff:period:'))
async def eff_period(callback: CallbackQuery):
    days = int(callback.data.rsplit(':',1)[-1])
    logger.info('effectiveness_period_selected by=%s days=%s', callback.from_user.id, days)
    await _render_eff(callback, days)


from app.repositories.marketing_preferences import set_unsubscribed, set_subscribed, is_unsubscribed


def marketing_unsubscribe_confirm_kb():
    return _with_nav([[InlineKeyboardButton(text='✅ Отключить акции', callback_data='marketing:unsubscribe:confirm')]])


def marketing_status_kb(unsubscribed: bool):
    btn = '🔔 Включить акции' if unsubscribed else '🔕 Отключить акции'
    act = 'marketing:subscribe' if unsubscribed else 'marketing:unsubscribe:ask'
    return _with_nav([[InlineKeyboardButton(text=btn, callback_data=act)]])


@router.callback_query(F.data == 'marketing:unsubscribe:ask')
async def marketing_unsub_ask(callback: CallbackQuery):
    await callback.message.edit_text('🔕 Отключить акции и специальные предложения?\n\nСервисные уведомления о ваших записях продолжат приходить.', reply_markup=marketing_unsubscribe_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == 'marketing:unsubscribe:confirm')
async def marketing_unsub_confirm(callback: CallbackQuery):
    await set_unsubscribed(callback.from_user.id)
    await callback.message.edit_text('✅ Готово. Мы больше не будем присылать вам акции и специальные предложения.\n\nУведомления о ваших записях продолжат приходить.', reply_markup=marketing_status_kb(True))
    await callback.answer('Сохранено ✅')


@router.callback_query(F.data == 'marketing:subscribe')
async def marketing_subscribe(callback: CallbackQuery):
    await set_subscribed(callback.from_user.id)
    await callback.message.edit_text('✅ Акции и специальные предложения снова включены.', reply_markup=marketing_status_kb(False))
    await callback.answer('Сохранено ✅')
