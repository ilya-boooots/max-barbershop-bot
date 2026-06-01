from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.auth import has_role, normalize_role
from app.core.navigation import push_screen
from app.keyboards.menu import back_reply_kb
from app.keyboards.staff import (
    broadcast_segment_kb,
    confirm_action_kb,
    select_user_kb,
    staff_messages_menu_kb,
    staff_thread_controls_kb,
    thread_list_kb,
    user_reply_kb,
)
from app.repositories.messaging import (
    add_thread_message,
    get_open_threads,
    get_or_create_open_thread,
    get_thread,
    get_thread_messages,
    log_direct_message,
    log_user_thread_reply,
    save_broadcast_log,
    set_thread_status,
    touch_thread,
)
from app.repositories.users import find_user_by_identifier, get_segment_user_ids, get_user

router = Router()
STAFF_ROLES = ["developer", "manager", "admin"]
ALLOWED_PAYLOAD_TYPES = {"text", "photo", "video", "animation"}
BASE_SEND_DELAY_SECONDS = 0.07


class StaffMessageStates(StatesGroup):
    waiting_broadcast_segment = State()
    waiting_broadcast_content = State()
    waiting_direct_identifier = State()
    waiting_direct_content = State()
    waiting_thread_reply_text = State()
    waiting_thread_search = State()


class UserThreadReplyStates(StatesGroup):
    waiting_reply_text = State()


async def _is_staff(user_id: int) -> bool:
    user = await get_user(user_id)
    role = normalize_role(user_id, user["role"] if user else None)
    return has_role(role, STAFF_ROLES)


def _fmt_user(user: dict) -> str:
    username = f"@{user['username']}" if user.get("username") else "—"
    return (
        "👤 Пользователь найден:\n"
        f"ID: {user['user_id']}\n"
        f"Юзернейм: {username}\n"
        f"Телефон: {user.get('phone') or '—'}\n"
        f"Имя: {user.get('name') or '—'}"
    )


def _render_thread_dialog(thread: dict, messages: list[dict]) -> str:
    lines = [f"🧵 Диалог #{thread['id']} (статус: {thread['status']})"]
    for item in messages:
        who = "Клиент" if item["sender_role"] == "user" else "Сотрудник"
        lines.append(f"{who}: {item['text']}")
    return "\n\n".join(lines)[:4000]


def _extract_payload(message: Message) -> dict[str, Any] | None:
    if message.text:
        return {"payload_type": "text", "text": message.text}
    if message.photo:
        return {
            "payload_type": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": message.caption or "",
        }
    if message.video:
        return {
            "payload_type": "video",
            "file_id": message.video.file_id,
            "caption": message.caption or "",
        }
    if message.animation:
        return {
            "payload_type": "animation",
            "file_id": message.animation.file_id,
            "caption": message.caption or "",
        }
    return None


def _payload_type_label(payload_type: str) -> str:
    labels = {
        "text": "Текст",
        "photo": "Фото",
        "video": "Видео",
        "animation": "GIF",
    }
    return labels.get(payload_type, payload_type)


def _preview_text(payload: dict[str, Any], limit: int = 200) -> str:
    body = (payload.get("text") or payload.get("caption") or "").strip()
    if len(body) <= limit:
        return body or "—"
    return f"{body[:limit]}..."


def _payload_log_text(payload: dict[str, Any]) -> str:
    if payload["payload_type"] == "text":
        return payload.get("text", "")
    caption = payload.get("caption") or ""
    if caption:
        return f"[{payload['payload_type']}] {caption}"
    return f"[{payload['payload_type']}]"


def _build_admin_caption(payload: dict[str, Any]) -> str:
    header = "Сообщение от администратора"
    caption = (payload.get("caption") or "").strip()
    if caption:
        return f"{header}\n\n{caption}"[:1024]
    return header


async def _send_payload(
    *,
    message: Message,
    chat_id: int,
    payload: dict[str, Any],
    mode: Literal["direct", "dialog"] = "direct",
    thread_id: int | None = None,
):
    payload_type = payload["payload_type"]
    if payload_type not in ALLOWED_PAYLOAD_TYPES:
        raise ValueError("unsupported payload type")

    include_admin_header = mode == "dialog"
    reply_markup: Any | None = user_reply_kb(thread_id) if mode == "dialog" and thread_id else None

    if payload_type == "text":
        text = payload.get("text", "")
        if include_admin_header:
            text = f"Сообщение от администратора\n\n{text}".strip()
        return await message.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    caption = payload.get("caption") or ""
    if include_admin_header:
        caption = _build_admin_caption(payload)

    if payload_type == "photo":
        return await message.bot.send_photo(
            chat_id=chat_id,
            photo=payload["file_id"],
            caption=caption,
            reply_markup=reply_markup,
        )
    if payload_type == "video":
        return await message.bot.send_video(
            chat_id=chat_id,
            video=payload["file_id"],
            caption=caption,
            reply_markup=reply_markup,
        )
    return await message.bot.send_animation(
        chat_id=chat_id,
        animation=payload["file_id"],
        caption=caption,
        reply_markup=reply_markup,
    )


async def _show_messages_menu(target: Message, state: FSMContext) -> None:
    await push_screen(state, "staff_messages")
    await target.answer("💬 Сообщения\nВыберите действие:", reply_markup=staff_messages_menu_kb())


@router.message(F.text == "💬 Сообщения")
async def open_messages_menu(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await _show_messages_menu(message, state)


@router.callback_query(F.data == "staff:messages")
async def open_messages_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await _show_messages_menu(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "staff:msg:broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.set_state(StaffMessageStates.waiting_broadcast_segment)
    await callback.message.answer("Кому отправляем рассылку?", reply_markup=broadcast_segment_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("staff:msg:broadcast:segment:"))
async def choose_broadcast_segment(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    segment = callback.data.split(":")[-1]
    if segment not in {"all", "active_7", "active_30", "active_90"}:
        await callback.answer("Неизвестный сегмент.", show_alert=True)
        return
    await state.update_data(broadcast_segment=segment)
    await state.set_state(StaffMessageStates.waiting_broadcast_content)
    await callback.message.answer(
        "Отправьте текст, фото, видео или GIF для рассылки.\n"
        "Документы, голосовые и стикеры не поддерживаются.",
        reply_markup=back_reply_kb(),
    )
    await callback.answer()


@router.message(StaffMessageStates.waiting_broadcast_content)
async def preview_broadcast(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        return
    payload = _extract_payload(message)
    if not payload:
        await message.answer("Поддерживаются только: текст, фото, видео или GIF.")
        return
    data = await state.get_data()
    segment = data.get("broadcast_segment") or "all"
    recipients = await get_segment_user_ids(segment)
    await state.update_data(broadcast_payload=payload)
    await message.answer(
        "Предпросмотр рассылки:\n"
        f"Тип: {_payload_type_label(payload['payload_type'])}\n"
        f"Текст/подпись: {_preview_text(payload)}\n"
        f"Получателей: {len(recipients)}\n\n"
        "Отправляем?",
        reply_markup=confirm_action_kb("staff:msg:broadcast_send", "staff:msg:cancel"),
    )


@router.callback_query(F.data == "staff:msg:broadcast_send")
async def send_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    payload = data.get("broadcast_payload")
    segment = data.get("broadcast_segment") or "all"
    if not payload:
        await callback.answer("Сначала отправьте контент для рассылки.", show_alert=True)
        return
    recipients = await get_segment_user_ids(segment)
    delivered = 0
    failed = 0
    blocked = 0

    for user_id in recipients:
        pending_retry = False
        try:
            await _send_payload(message=callback.message, chat_id=int(user_id), payload=payload)
            delivered += 1
        except TelegramRetryAfter as exc:
            pending_retry = True
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramForbiddenError:
            failed += 1
            blocked += 1
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1

        if pending_retry:
            try:
                await _send_payload(message=callback.message, chat_id=int(user_id), payload=payload)
                delivered += 1
            except TelegramForbiddenError:
                failed += 1
                blocked += 1
            except Exception:
                failed += 1

        await asyncio.sleep(BASE_SEND_DELAY_SECONDS)

    await save_broadcast_log(
        staff_id=callback.from_user.id,
        segment=segment,
        payload_type=payload["payload_type"],
        text=_payload_log_text(payload),
        recipients_total=len(recipients),
        delivered=delivered,
        failed=failed,
        blocked=blocked,
    )
    await state.clear()
    await callback.message.answer(
        "Рассылка завершена ✅\n"
        f"Получателей: {len(recipients)}\n"
        f"Успешно: {delivered}\n"
        f"Ошибок: {failed}"
    )
    await callback.answer()


@router.callback_query(F.data == "staff:msg:direct")
async def start_direct_message(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.set_state(StaffMessageStates.waiting_direct_identifier)
    await callback.message.answer("Введите Telegram ID / телефон / @username пользователя:")
    await callback.answer()


@router.message(StaffMessageStates.waiting_direct_identifier)
async def find_direct_user(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        return
    user = await find_user_by_identifier((message.text or "").strip())
    if not user:
        await message.answer("Пользователь не найден. Проверьте идентификатор.")
        return
    await state.update_data(direct_user_id=int(user["user_id"]))
    await message.answer(_fmt_user(user), reply_markup=select_user_kb())


@router.callback_query(F.data == "staff:msg:user_select")
async def ask_direct_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("direct_user_id"):
        await callback.answer("Сначала найдите пользователя.", show_alert=True)
        return
    await state.set_state(StaffMessageStates.waiting_direct_content)
    await callback.message.answer("Отправьте сообщение, фото, видео или GIF пользователю.")
    await callback.answer()


@router.message(StaffMessageStates.waiting_direct_content)
async def send_direct_text(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        return
    payload = _extract_payload(message)
    if not payload:
        await message.answer("Поддерживаются только: текст, фото, видео или GIF.")
        return
    data = await state.get_data()
    target_user_id = data.get("direct_user_id")
    if not target_user_id:
        await message.answer("Сначала выберите пользователя.")
        return

    thread = await get_or_create_open_thread(int(target_user_id), message.from_user.id)
    try:
        sent = await _send_payload(
            message=message,
            chat_id=int(target_user_id),
            payload=payload,
            mode="direct",
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer("Не удалось отправить сообщение пользователю.")
        return

    text_for_log = _payload_log_text(payload)
    await add_thread_message(
        thread_id=int(thread["id"]),
        sender_role="staff",
        staff_id=message.from_user.id,
        text=text_for_log,
        tg_message_id=sent.message_id,
    )
    await log_direct_message(message.from_user.id, int(target_user_id), int(thread["id"]), text_for_log)
    await state.clear()
    await message.answer("Отправлено ✅")


@router.callback_query(F.data.startswith("thread:reply:"))
async def user_reply_entry(callback: CallbackQuery, state: FSMContext) -> None:
    thread_id = int(callback.data.split(":")[-1])
    thread = await get_thread(thread_id)
    if not thread or int(thread["user_id"]) != callback.from_user.id:
        await callback.answer("Диалог недоступен.", show_alert=True)
        return
    await state.set_state(UserThreadReplyStates.waiting_reply_text)
    await state.update_data(user_reply_thread_id=thread_id)
    await callback.message.answer("Напишите ответ — я передам администратору.")
    await callback.answer()


@router.message(UserThreadReplyStates.waiting_reply_text)
async def receive_user_reply(message: Message, state: FSMContext) -> None:
    thread_data = await state.get_data()
    thread_id = int(thread_data.get("user_reply_thread_id") or 0)
    thread = await get_thread(thread_id)
    if not thread or int(thread["user_id"]) != message.from_user.id:
        await message.answer("Диалог недоступен.")
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение пустое.")
        return
    await add_thread_message(thread_id=thread_id, sender_role="user", text=text)
    user = await get_user(message.from_user.id)
    await log_user_thread_reply(
        user_id=message.from_user.id,
        username=(message.from_user.username or "").lower() or None,
        phone=user.get("phone") if user else None,
        thread_id=thread_id,
        text=text,
    )
    staff_chat = int(thread.get("last_staff_id") or 378881880)
    username = f"@{message.from_user.username}" if message.from_user.username else "—"
    staff_text = (
        "📩 Ответ от пользователя\n"
        f"ID: {message.from_user.id}\n"
        f"Юзернейм: {username}\n"
        f"Телефон: {(user or {}).get('phone') or '—'}\n"
        f"Диалог: #{thread_id}\n\n"
        f"{text}"
    )
    await message.bot.send_message(
        chat_id=staff_chat,
        text=staff_text,
        reply_markup=staff_thread_controls_kb(thread_id),
    )
    await state.clear()
    await message.answer("Ответ отправлен администратору ✅")


@router.callback_query(F.data == "staff:msg:inbox")
async def show_inbox(callback: CallbackQuery) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    threads = await get_open_threads()
    if not threads:
        await callback.message.answer("Открытых диалогов нет.")
        await callback.answer()
        return
    rows: list[tuple[int, str]] = []
    for thread in threads:
        updated = datetime.fromisoformat(thread["updated_ts"]).strftime("%d.%m %H:%M")
        username = f"@{thread['username']}" if thread.get("username") else str(thread["user_id"])
        rows.append((int(thread["id"]), f"{username} / {thread['user_id']} — обновлено {updated}"))
    await callback.message.answer("📥 Открытые диалоги:", reply_markup=thread_list_kb(rows))
    await callback.answer()


@router.callback_query(F.data == "staff:msg:search_thread")
async def search_thread_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await state.set_state(StaffMessageStates.waiting_thread_search)
    await callback.message.answer("Введите Telegram ID / телефон / @username для поиска диалога:")
    await callback.answer()


@router.message(StaffMessageStates.waiting_thread_search)
async def search_thread_by_user(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        return
    user = await find_user_by_identifier((message.text or "").strip())
    await state.clear()
    if not user:
        await message.answer("Пользователь не найден.")
        return
    threads = [row for row in await get_open_threads(limit=100) if int(row["user_id"]) == int(user["user_id"])]
    if not threads:
        await message.answer("Открытых диалогов не найдено.")
        return
    rows = []
    for thread in threads:
        updated = datetime.fromisoformat(thread["updated_ts"]).strftime("%d.%m %H:%M")
        rows.append((int(thread["id"]), f"Диалог #{thread['id']} — обновлено {updated}"))
    await message.answer("Найденные диалоги:", reply_markup=thread_list_kb(rows))


@router.callback_query(F.data.startswith("staff:thread:open:"))
async def open_thread(callback: CallbackQuery) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    thread_id = int(callback.data.split(":")[-1])
    thread = await get_thread(thread_id)
    if not thread:
        await callback.answer("Диалог не найден.", show_alert=True)
        return
    messages = await get_thread_messages(thread_id, limit=10)
    await callback.message.answer(_render_thread_dialog(thread, messages), reply_markup=staff_thread_controls_kb(thread_id))
    await callback.answer()


@router.callback_query(F.data.startswith("staff:thread:reply:"))
async def start_thread_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    thread_id = int(callback.data.split(":")[-1])
    thread = await get_thread(thread_id)
    if not thread:
        await callback.answer("Диалог не найден.", show_alert=True)
        return
    await state.set_state(StaffMessageStates.waiting_thread_reply_text)
    await state.update_data(reply_thread_id=thread_id)
    await callback.message.answer("Введите ответ пользователю:")
    await callback.answer()


@router.message(StaffMessageStates.waiting_thread_reply_text)
async def send_thread_reply(message: Message, state: FSMContext) -> None:
    if not await _is_staff(message.from_user.id):
        return
    data = await state.get_data()
    thread_id = int(data.get("reply_thread_id") or 0)
    thread = await get_thread(thread_id)
    text = (message.text or "").strip()
    if not thread:
        await message.answer("Диалог не найден.")
        await state.clear()
        return
    if not text:
        await message.answer("Текст не должен быть пустым.")
        return
    try:
        sent = await message.bot.send_message(
            chat_id=int(thread["user_id"]),
            text=f"Сообщение от администратора\n\n{text}",
            reply_markup=user_reply_kb(thread_id),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer("Не удалось отправить сообщение пользователю.")
        return
    await add_thread_message(
        thread_id=thread_id,
        sender_role="staff",
        text=text,
        staff_id=message.from_user.id,
        tg_message_id=sent.message_id,
    )
    await state.clear()
    await message.answer("Ответ отправлен пользователю ✅")


@router.callback_query(F.data.startswith("staff:thread:close:"))
async def close_thread(callback: CallbackQuery) -> None:
    if not await _is_staff(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    thread_id = int(callback.data.split(":")[-1])
    await set_thread_status(thread_id, "closed")
    await touch_thread(thread_id, callback.from_user.id)
    await callback.message.answer(f"Диалог #{thread_id} закрыт ✅")
    await callback.answer()


@router.callback_query(F.data == "staff:msg:cancel")
async def cancel_message_action(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Действие отменено.", reply_markup=staff_messages_menu_kb())
    await callback.answer()
