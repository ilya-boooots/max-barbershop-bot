from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.repositories.role_onboarding import get_role_onboarding
from app.services.role_onboarding import (
    ROLE_ONBOARDING_TEXTS,
    finish_onboarding,
    format_step_text,
    mark_step,
    normalize_onboarding_role_key,
    role_onboarding_keyboard,
    skip_onboarding,
)

router = Router()
logger = logging.getLogger(__name__)


async def _render_onboarding(callback: CallbackQuery, role_key: str, step: int, target_tg_id: int) -> None:
    if not callback.message:
        return
    text = format_step_text(role_key, step)
    markup = role_onboarding_keyboard(role=role_key, step=step, target_tg_id=target_tg_id)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        await callback.bot.send_message(target_tg_id, text, reply_markup=markup)


@router.callback_query(F.data.startswith("role_onb:"))
async def handle_role_onboarding(callback: CallbackQuery) -> None:
    if not callback.message or not callback.data:
        return
    parts = callback.data.split(":")
    if len(parts) != 5:
        logger.info("stale_callback_reason=malformed callback_user_id=%s data=%s", callback.from_user.id, callback.data)
        await callback.answer("⚠️ Это обучение уже завершено или устарело.", show_alert=True)
        return
    _, action, role, step_raw, target_raw = parts
    role_key = normalize_onboarding_role_key(role)
    if role_key not in ROLE_ONBOARDING_TEXTS or not step_raw.isdigit() or not target_raw.isdigit():
        logger.info("stale_callback_reason=invalid_payload callback_user_id=%s role=%s step=%s target=%s", callback.from_user.id, role, step_raw, target_raw)
        await callback.answer("⚠️ Это обучение уже завершено или устарело.", show_alert=True)
        return
    step = int(step_raw)
    target_tg_id = int(target_raw)
    logger.info("onboarding_callback_received callback_user_id=%s target_user_id=%s role=%s action=%s step=%s", callback.from_user.id, target_tg_id, role_key, action, step)
    if callback.from_user.id != target_tg_id:
        logger.info("onboarding_user_mismatch callback_user_id=%s target_user_id=%s role=%s action=%s", callback.from_user.id, target_tg_id, role_key, action)
        await callback.answer("⛔ Это обучение доступно только сотруднику, которому выдали роль.", show_alert=True)
        return

    state = await get_role_onboarding(target_tg_id, role_key)
    if not state or state.get("status") in {"completed", "skipped"}:
        logger.info("stale_callback_reason=missing_or_final callback_user_id=%s target_user_id=%s role=%s status=%s", callback.from_user.id, target_tg_id, role_key, (state or {}).get("status"))
        await callback.answer("⚠️ Это обучение уже завершено или устарело.", show_alert=True)
        return
    logger.info("onboarding_record_found target_user_id=%s role=%s status=%s", target_tg_id, role_key, state.get("status"))

    total = len(ROLE_ONBOARDING_TEXTS[role_key])
    current_step = max(1, min(total, int(state.get("current_step") or 1)))

    if action == "prev":
        if current_step <= 1:
            await callback.answer("Это первый шаг обучения.", show_alert=True)
            return
        next_step = max(1, current_step - 1)
        await mark_step(role_key, target_tg_id, next_step)
        logger.info("onboarding_step_changed target_user_id=%s role=%s action=prev from_step=%s to_step=%s", target_tg_id, role_key, current_step, next_step)
        await _render_onboarding(callback, role_key, next_step, target_tg_id)
        await callback.answer()
        return

    if action == "next":
        next_step = min(total, current_step + 1)
        await mark_step(role_key, target_tg_id, next_step)
        logger.info("onboarding_step_changed target_user_id=%s role=%s action=next from_step=%s to_step=%s", target_tg_id, role_key, current_step, next_step)
        await _render_onboarding(callback, role_key, next_step, target_tg_id)
        await callback.answer()
        return

    if action == "finish":
        await finish_onboarding(role_key, target_tg_id)
        logger.info("onboarding_completed target_user_id=%s role=%s", target_tg_id, role_key)
        await callback.message.edit_text("✅ Обучение завершено. Хорошей работы!")
        await callback.answer()
        return

    if action == "skip":
        await skip_onboarding(role_key, target_tg_id)
        logger.info("onboarding_skipped target_user_id=%s role=%s", target_tg_id, role_key)
        await callback.message.edit_text("⏭ Обучение пропущено. Вы можете пользоваться доступными функциями бота.")
        await callback.answer()
        return

    await callback.answer("⚠️ Это обучение уже завершено или устарело.", show_alert=True)
