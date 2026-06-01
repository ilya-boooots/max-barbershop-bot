from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.navigation import push_screen
from app.core.permissions import ROLE_DEVELOPER, ROLE_MANAGER, require_roles
from app.core.staff_permissions import can_view_statistics, resolve_role
from app.core.ui_texts import STATISTICS_BTN
from app.integrations.yclients.service import get_yclients_credentials
from app.keyboards.statistics import CB_PREFIX, statistics_menu_kb
from app.repositories.staff_action_logs import add_staff_action_log
from app.repositories.users import get_user_by_tg_id
from app.services.statistics import business_summary_metrics

router = Router()
logger = logging.getLogger(__name__)


ERROR_TEXT = "⚠️ Не удалось загрузить статистику. Попробуйте позже."


def _money(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def _render_business_summary(payload: dict) -> str:
    return "\n".join(
        [
            "📊 Статистика",
            "",
            f"👤 Клиентов зарегистрировано в боте: {payload['bot_registered_clients']}",
            f"👥 Клиентов всего: {payload['clients_total']}",
            f"🧾 Записей всего: {payload['records_total']}",
            f"💰 Выручка за весь период: {_money(payload['revenue'])} ₽",
            f"💳 Средний чек: {_money(payload['avg_check'])} ₽",
            f"✅ Дошли/оплатили: {payload['completed']}",
            f"❌ Отмены: {payload['cancelled']}",
            f"🚫 Не пришли: {payload['no_show']}",
        ]
    )




async def _send_statistics_dev_diag(
    bot,
    *,
    action: str,
    handler: str,
    exc: Exception,
    user_id: int | None = None,
    username: str | None = None,
    callback_data: str | None = None,
) -> None:
    tb_tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-5:])[:700]
    text = "\n".join(
        [
            "🚨 Statistics/YClients diagnostic",
            f"🧩 action: {action}",
            f"🧩 handler: {handler}",
            f"👤 user_id: {user_id if user_id is not None else 'n/a'}",
            f"🔖 username: @{username}" if username else "🔖 username: n/a",
            f"📨 callback_data: {(callback_data or 'n/a')[:120]}",
            f"🕒 timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
            f"🧯 exception: {type(exc).__name__}: {str(exc)[:180]}",
            f"🪵 traceback_last_lines:\n{tb_tail or 'n/a'}",
        ]
    )
    try:
        await bot.send_message(378881880, text[:1800])
    except Exception:
        logger.exception("statistics_dev_diagnostics_send_failed action=%s", action)


async def _build_statistics_text() -> str:
    credentials, _ = await get_yclients_credentials()
    payload = await business_summary_metrics(company_id=credentials.company_id)
    return _render_business_summary(payload)


async def render_statistics_summary_screen(message: Message) -> None:
    try:
        text = await _build_statistics_text()
    except Exception as exc:
        logger.exception("statistics_summary_render_failed user_id=%s", message.from_user.id)
        await _send_statistics_dev_diag(
            message.bot,
            action="statistics_summary_render",
            handler="app.handlers.statistics.render_statistics_summary_screen",
            exc=exc,
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        text = ERROR_TEXT
    await message.answer(text, reply_markup=statistics_menu_kb())


@router.message(F.text == STATISTICS_BTN)
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def open_statistics_menu(message: Message, state: FSMContext) -> None:
    await add_staff_action_log(message.from_user.id, "Открыл раздел статистики")
    await push_screen(state, "statistics")
    await render_statistics_summary_screen(message)


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:"))
@require_roles(ROLE_DEVELOPER, ROLE_MANAGER)
async def statistics_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await push_screen(state, "statistics")
    try:
        text = await _build_statistics_text()
    except Exception as exc:
        logger.exception("statistics_callback_failed user_id=%s callback_data=%s", callback.from_user.id, callback.data)
        await _send_statistics_dev_diag(
            callback.bot,
            action="statistics_callback",
            handler="app.handlers.statistics.statistics_callback",
            exc=exc,
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            callback_data=callback.data,
        )
        text = ERROR_TEXT
    if callback.message:
        await callback.message.edit_text(text, reply_markup=statistics_menu_kb())
    await callback.answer()


@router.message(F.text == "📊 Статистика")
async def open_statistics_denied(message: Message) -> None:
    user = await get_user_by_tg_id(message.from_user.id)
    role = resolve_role(message.from_user.id, user.get("role") if user else None)
    if can_view_statistics(role):
        return
    await message.answer("⛔ Раздел статистики доступен только сотрудникам.")
