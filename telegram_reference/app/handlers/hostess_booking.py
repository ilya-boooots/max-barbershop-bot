from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.core.auth import has_role, normalize_role
from app.db.bookings_repo import approve_booking, cancel_booking, get_booking_by_id
from app.repositories.diagnostics import log_bot_event, log_user_event
from app.repositories.users import get_user

router = Router()
ALLOWED_ROLES = ["manager", "admin", "developer"]
ADDRESS_TEXT = "Саратов, улица Пушкина 1"


def _parse_booking_id(data: str | None) -> int | None:
    try:
        return int((data or "").split(":")[-1])
    except (TypeError, ValueError):
        return None


async def _actor_role(user_id: int) -> str:
    user = await get_user(user_id)
    return normalize_role(user_id, user["role"] if user else None)


@router.callback_query(F.data.startswith("hostess:approve:"))
async def hostess_approve(callback: CallbackQuery) -> None:
    role = await _actor_role(callback.from_user.id)
    if not has_role(role, ALLOWED_ROLES):
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    booking_id = _parse_booking_id(callback.data)
    if booking_id is None:
        await callback.answer("Некорректный идентификатор заявки", show_alert=True)
        return
    updated = await approve_booking(booking_id, callback.from_user.id)
    booking = await get_booking_by_id(booking_id)
    if not booking:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if updated:
        try:
            await callback.bot.send_message(
                int(booking["user_id"]),
                "Ваша бронь подтверждена ✅\n"
                f"Дата: {booking['date']}\n"
                f"Время: {booking['time']}\n"
                f"Адрес: {ADDRESS_TEXT}\n"
                "Ждём вас 🙂\n"
                "Если планы поменяются или будете опаздывать — позвоните, пожалуйста, нам по телефону +79999999999",
            )
        except Exception:
            pass
        await log_user_event(
            user_id=int(booking["user_id"]),
            username=None,
            phone=booking.get("phone"),
            event_type="booking",
            event_name="booking_approved",
            payload={"booking_id": booking_id, "staff_id": callback.from_user.id},
        )
        if callback.message:
            await callback.message.edit_text("✅ Бронь подтверждена. Статус обновлён.")
        await callback.answer("Готово")
        await log_bot_event(
            level="INFO",
            source="hostess_action",
            message="Хостес обработала заявку",
            details={"booking_id": booking_id, "staff_id": callback.from_user.id, "status": "approved"},
        )
    else:
        await callback.answer("Заявка уже обработана", show_alert=True)


@router.callback_query(F.data.startswith("hostess:cancel:"))
async def hostess_cancel(callback: CallbackQuery) -> None:
    role = await _actor_role(callback.from_user.id)
    if not has_role(role, ALLOWED_ROLES):
        await callback.answer("⛔️ Недостаточно прав", show_alert=True)
        return

    booking_id = _parse_booking_id(callback.data)
    if booking_id is None:
        await callback.answer("Некорректный идентификатор заявки", show_alert=True)
        return
    updated = await cancel_booking(booking_id, callback.from_user.id)
    booking = await get_booking_by_id(booking_id)
    if not booking:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if updated:
        try:
            await callback.bot.send_message(
                int(booking["user_id"]),
                "К сожалению, бронь отменена ❌\n"
                f"Дата: {booking['date']}\n"
                f"Время: {booking['time']}\n"
                "Если хотите — создайте новую заявку.",
            )
        except Exception:
            pass
        await log_user_event(
            user_id=int(booking["user_id"]),
            username=None,
            phone=booking.get("phone"),
            event_type="booking",
            event_name="booking_cancelled",
            payload={"booking_id": booking_id, "staff_id": callback.from_user.id},
        )
        if callback.message:
            await callback.message.edit_text("❌ Бронь отменена. Статус обновлён.")
        await callback.answer("Готово")
    else:
        await callback.answer("Заявка уже обработана", show_alert=True)

    await log_bot_event(
        level="INFO",
        source="hostess_action",
        message="Хостес обработала заявку",
        details={"booking_id": booking_id, "staff_id": callback.from_user.id, "status": "cancelled"},
    )
