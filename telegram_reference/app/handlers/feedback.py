from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.auth import has_role, normalize_role
from app.core.navigation import clear_state_preserving_navigation, push_screen
from app.db.feedback_repo import (
    close_feedback,
    create_feedback,
    format_gmt4,
    get_admin_ids,
    get_feedback_by_id,
    get_feedback_user_context,
    save_feedback_reply,
)
from app.keyboards.feedback import (
    feedback_admin_actions_kb,
    feedback_public_review_links_kb,
    feedback_stars_kb,
)
from app.repositories.diagnostics import log_bot_event, log_user_event
from app.repositories.users import get_user

router = Router()
ADMIN_ROLES = ["admin"]


class FeedbackStates(StatesGroup):
    waiting_feedback_text = State()


class AdminFeedbackStates(StatesGroup):
    waiting_reply_text = State()



async def _is_admin(user_id: int) -> bool:
    user = await get_user(user_id)
    role = normalize_role(user_id, user["role"] if user else None)
    return has_role(role, ADMIN_ROLES)


def _render_admin_feedback_card(*, rating: int, feedback_text: str, tg_id: int, user: dict | None, ts_local: str, visits_count: int, bookings_total: int, bookings_approved: int, bookings_cancelled: int, last_booking: dict | None) -> str:
    if user and user.get("username"):
        user_display = f"{user.get('name') or user.get('display_name') or 'Гость'} (@{user['username']})"
    else:
        user_display = user.get("name") if user else "Гость"
    phone = (user or {}).get("phone") or "не указан"
    if last_booking:
        last_booking_text = f"{format_gmt4(last_booking.get('ts_value'))} — {last_booking.get('status') or '—'}"
    else:
        last_booking_text = "—"
    return (
        f"⚠️ Негативный отзыв ({rating}⭐)\n\n"
        f"Гость: {user_display}\n"
        f"Telegram ID: {tg_id}\n"
        f"Телефон: {phone}\n"
        f"Время: {format_gmt4(ts_local)} (GMT+4)\n\n"
        f"Текст:\n{feedback_text}\n\n"
        f"Посещений: {visits_count}\n"
        f"Броней за всё время: {bookings_total}\n"
        f"Подтверждено броней: {bookings_approved}\n"
        f"Отменено броней: {bookings_cancelled}\n"
        f"Последняя бронь: {last_booking_text}"
    )


@router.message(F.text == "📝 Оставить отзыв")
async def start_feedback(message: Message, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await push_screen(state, "review_prompt")
    await message.answer("Оцените, пожалуйста, ваш визит ⭐️", reply_markup=feedback_stars_kb())


@router.callback_query(F.data.startswith("fb:rate:"))
async def choose_feedback_rating(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[-1].isdigit():
        await callback.answer("Некорректная оценка", show_alert=True)
        return
    rating = int(parts[-1])
    if rating < 1 or rating > 5:
        await callback.answer("Некорректная оценка", show_alert=True)
        return

    await state.update_data(feedback_rating=rating)
    if rating >= 4:
        feedback_id = await create_feedback(user_id=callback.from_user.id, rating=rating)
        user = await get_user(callback.from_user.id)
        await log_user_event(
            user_id=callback.from_user.id,
            username=(user or {}).get("username"),
            phone=(user or {}).get("phone"),
            event_type="feedback",
            event_name="feedback_rating_high",
            screen="review_prompt",
            payload={"rating": rating, "feedback_id": feedback_id},
        )
        await log_bot_event(
            level="INFO",
            source="feedback",
            message="feedback_rating_high",
            details={"user_id": callback.from_user.id, "feedback_id": feedback_id, "rating": rating},
        )
        await callback.message.answer(
            "Спасибо за высокую оценку! ❤️\n"
            "Если будет минутка — оставьте, пожалуйста, отзыв в сервисах ниже:",
            reply_markup=feedback_public_review_links_kb(),
        )
        await clear_state_preserving_navigation(state)
        await callback.answer()
        return

    await state.set_state(FeedbackStates.waiting_feedback_text)
    await callback.message.answer(
        "Жаль, что вам не понравилось 😔\n"
        "Что пошло не так? Напишите, пожалуйста, подробнее — мы разберёмся и исправимся."
    )
    await callback.answer()


@router.message(FeedbackStates.waiting_feedback_text)
async def receive_feedback_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Пожалуйста, опишите чуть подробнее (хотя бы 5 символов).")
        return
    if len(text) > 1000:
        await message.answer("Пожалуйста, сократите отзыв до 1000 символов.")
        return

    data = await state.get_data()
    rating = int(data.get("feedback_rating") or 0)
    if rating < 1 or rating > 3:
        await clear_state_preserving_navigation(state)
        await message.answer("Не удалось определить оценку. Попробуйте снова через меню.")
        return

    feedback_id = await create_feedback(user_id=message.from_user.id, rating=rating, text=text)
    user_context = await get_feedback_user_context(message.from_user.id)
    admins = await get_admin_ids()

    await log_user_event(
        user_id=message.from_user.id,
        username=(user_context.get("user") or {}).get("username"),
        phone=(user_context.get("user") or {}).get("phone"),
        event_type="feedback",
        event_name="feedback_submitted",
        screen="review_prompt",
        payload={"feedback_id": feedback_id, "rating": rating},
    )
    await log_bot_event(
        level="INFO",
        source="feedback",
        message="feedback_submitted",
        details={"user_id": message.from_user.id, "feedback_id": feedback_id, "rating": rating},
    )

    card_text = _render_admin_feedback_card(
        rating=rating,
        feedback_text=text,
        tg_id=message.from_user.id,
        user=user_context.get("user"),
        ts_local=(await get_feedback_by_id(feedback_id) or {}).get("ts_local", ""),
        visits_count=user_context["visits_count"],
        bookings_total=user_context["bookings_total"],
        bookings_approved=user_context["bookings_approved"],
        bookings_cancelled=user_context["bookings_cancelled"],
        last_booking=user_context.get("last_booking"),
    )
    for admin_id in admins:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=card_text,
                reply_markup=feedback_admin_actions_kb(feedback_id),
            )
        except Exception:
            continue

    await clear_state_preserving_navigation(state)
    await message.answer("Спасибо, что поделились. Мы обязательно разберёмся 🙏")


@router.callback_query(F.data.startswith("fb:reply:"))
async def start_admin_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw_feedback_id = callback.data.split(":")[-1]
    if not raw_feedback_id.isdigit():
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    feedback_id = int(raw_feedback_id)
    feedback = await get_feedback_by_id(feedback_id)
    if not feedback:
        await callback.answer("Отзыв не найден.", show_alert=True)
        return
    await state.set_state(AdminFeedbackStates.waiting_reply_text)
    await state.update_data(reply_feedback_id=feedback_id)
    await callback.message.answer("Напишите ответ гостю:")
    await callback.answer()


@router.message(AdminFeedbackStates.waiting_reply_text)
async def send_admin_reply(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await clear_state_preserving_navigation(state)
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст ответа.")
        return
    if len(text) > 2000:
        await message.answer("Пожалуйста, сократите ответ до 2000 символов.")
        return

    data = await state.get_data()
    feedback_id = int(data.get("reply_feedback_id") or 0)
    feedback = await get_feedback_by_id(feedback_id)
    if not feedback:
        await clear_state_preserving_navigation(state)
        await message.answer("Отзыв не найден.")
        return

    await save_feedback_reply(feedback_id=feedback_id, admin_id=message.from_user.id, text=text)
    await message.bot.send_message(
        chat_id=int(feedback["user_id"]),
        text=f"Ответ администратора:\n{text}",
    )
    admin_user = await get_user(message.from_user.id)
    await log_user_event(
        user_id=message.from_user.id,
        username=(admin_user or {}).get("username"),
        phone=(admin_user or {}).get("phone"),
        event_type="feedback",
        event_name="feedback_admin_reply",
        screen="review_prompt",
        payload={"feedback_id": feedback_id, "target_user_id": feedback["user_id"]},
    )
    await log_bot_event(
        level="INFO",
        source="feedback",
        message="admin_feedback_reply_sent",
        details={"feedback_id": feedback_id, "admin_id": message.from_user.id, "user_id": feedback["user_id"]},
    )
    await clear_state_preserving_navigation(state)
    await message.answer("Ответ отправлен ✅")


@router.callback_query(F.data.startswith("fb:close:"))
async def close_feedback_callback(callback: CallbackQuery) -> None:
    if not await _is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    raw_feedback_id = callback.data.split(":")[-1]
    if not raw_feedback_id.isdigit():
        await callback.answer("Некорректный идентификатор.", show_alert=True)
        return
    feedback_id = int(raw_feedback_id)
    feedback = await get_feedback_by_id(feedback_id)
    if not feedback:
        await callback.answer("Отзыв не найден.", show_alert=True)
        return
    await close_feedback(feedback_id=feedback_id, admin_id=callback.from_user.id)
    await callback.answer("Отзыв закрыт ✅")
    await callback.message.answer("Отзыв закрыт ✅")
