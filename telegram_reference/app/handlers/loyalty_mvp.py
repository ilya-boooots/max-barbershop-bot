from __future__ import annotations

from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.navigation import push_screen
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.repositories.loyalty_operations import count_user_operations, get_last_user_operation, get_user_operations
from app.repositories.users import get_user
from app.services.loyalty_mvp import (
    ensure_referral_code,
    fetch_yclients_loyalty_balance,
    get_referral_link,
    get_referral_stats,
    sync_referral_reward_if_eligible,
)
from app.utils.datetime import format_branch_datetime
from app.core.config import get_settings

router = Router()

LOYALTY_BTN = "🎁 Система лояльности"
CB_MENU = "loy:mvp:menu"
CB_BALANCE = "loy:mvp:balance"
CB_HISTORY = "loy:mvp:history"
CB_HISTORY_PAGE = "loy:mvp:history:page"
CB_REFERRAL = "loy:mvp:referral"
CB_REFERRAL_CODE = "loy:mvp:referral:code"
CB_REFERRAL_LINK = "loy:mvp:referral:link"

PAGE_SIZE = 5


def _is_loyalty_enabled() -> bool:
    return get_settings().loyalty_enabled


async def _send_loyalty_disabled_message(
    target: Message | CallbackQuery,
    *,
    edit_current: bool = False,
) -> None:
    text = "🎁 Система лояльности скоро появится."
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )
    if isinstance(target, CallbackQuery):
        if target.message:
            if edit_current:
                await target.message.edit_text(text, reply_markup=kb)
            else:
                await target.message.answer(text, reply_markup=kb)
        await target.answer()
        return
    await target.answer(text, reply_markup=kb)


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Мой баланс", callback_data=CB_BALANCE)],
            [InlineKeyboardButton(text="📜 История баллов", callback_data=CB_HISTORY)],
            [InlineKeyboardButton(text="👥 Пригласи друга", callback_data=CB_REFERRAL)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def _history_kb(page: int, pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Новее", callback_data=f"{CB_HISTORY_PAGE}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️ Старее", callback_data=f"{CB_HISTORY_PAGE}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.extend([
        [InlineKeyboardButton(text="💎 Мой баланс", callback_data=CB_BALANCE)],
        [InlineKeyboardButton(text="👥 Пригласи друга", callback_data=CB_REFERRAL)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _referral_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Показать код", callback_data=CB_REFERRAL_CODE)],
            [InlineKeyboardButton(text="🔗 Показать ссылку", callback_data=CB_REFERRAL_LINK)],
            [InlineKeyboardButton(text="📜 История баллов", callback_data=CB_HISTORY)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


async def _format_operation(entry: dict) -> str:
    dt = await format_branch_datetime(entry.get("created_at_utc"))
    delta = int(entry.get("points_delta") or 0)
    op_type = str(entry.get("operation_type") or "manual_adjustment")
    source = str(entry.get("source") or "system")
    reason = str(entry.get("reason") or "")
    balance = entry.get("resulting_balance")
    labels = {
        "visit_accrual": "✅ Начислено",
        "writeoff_yclients": "➖ Списано",
        "referral_bonus_inviter": "🎁 Начислено",
        "referral_bonus_invited": "🎉 Начислено",
        "manual_adjustment": "⚙️ Корректировка",
        "welcome_bonus": "🎁 Начислено",
    }
    title = labels.get(op_type, "⚙️ Операция")
    delta_text = f"{delta:+d} баллов"
    lines = [f"{dt}", f"{title}: {delta_text}"]
    if reason:
        lines.append(f"🧾 {reason}")
    lines.append(f"🔎 Источник: {source}")
    if balance is not None:
        lines.append(f"💎 Баланс после операции: {int(balance)}")
    return "\n".join(lines)


async def _render_balance_text(user_id: int) -> str:
    user = await get_user(user_id)
    if not user:
        return "🙂 Сначала завершите регистрацию через /start"
    local_balance = int(user.get("loyalty_balance") or 0)
    yclients_balance: int | None = None
    company_id = (get_settings().yclients_company_id or "").strip()
    yclient_id = str(user.get("yclients_client_id") or "").strip()
    if company_id and yclient_id:
        yclients_balance = await fetch_yclients_loyalty_balance(company_id=company_id, client_id=yclient_id)
    final_balance = yclients_balance if yclients_balance is not None else local_balance
    last_op = await get_last_user_operation(user_id)
    lines = [
        f"💎 Ваш баланс: {final_balance:,} баллов".replace(",", " "),
        "Спасибо, что выбираете нас 🙌",
        "Баллы начисляются и учитываются в системе лояльности.",
    ]
    if last_op and last_op.get("created_at_utc"):
        lines.append(f"🕒 Последняя операция: {await format_branch_datetime(last_op.get('created_at_utc'))}")
    if yclients_balance is not None:
        lines.append("🔗 Статус: синхронизировано с YClients")
    return "\n".join(lines)


@router.message(F.text == LOYALTY_BTN)
async def open_loyalty_menu(message: Message, state: FSMContext) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(message)
        return
    await push_screen(state, "loyalty_menu")
    await sync_referral_reward_if_eligible(invited_tg_id=message.from_user.id, bot=message.bot)
    await message.answer("🎁 Система лояльности\n\nВыберите раздел 👇", reply_markup=_menu_kb())


@router.callback_query(F.data == CB_MENU)
async def open_loyalty_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(callback, edit_current=True)
        return
    if not callback.message:
        await callback.answer()
        return
    await push_screen(state, "loyalty_menu")
    await callback.message.edit_text("🎁 Система лояльности\n\nВыберите раздел 👇", reply_markup=_menu_kb())
    await callback.answer()


@router.callback_query(F.data == CB_BALANCE)
async def show_balance(callback: CallbackQuery) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(callback, edit_current=True)
        return
    if not callback.message:
        await callback.answer()
        return
    text = await _render_balance_text(callback.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 История баллов", callback_data=CB_HISTORY)],
            [InlineKeyboardButton(text="👥 Пригласи друга", callback_data=CB_REFERRAL)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_MENU)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _show_history(callback: CallbackQuery, page: int) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(callback, edit_current=True)
        return
    if not callback.message:
        await callback.answer()
        return
    total = await count_user_operations(callback.from_user.id)
    if total <= 0:
        await callback.message.edit_text(
            "😌 Пока операций по баллам нет\nПосле первого начисления здесь появится история.",
            reply_markup=_history_kb(0, 1),
        )
        await callback.answer()
        return
    pages = max(1, ceil(total / PAGE_SIZE))
    safe_page = max(0, min(page, pages - 1))
    offset = safe_page * PAGE_SIZE
    rows = await get_user_operations(callback.from_user.id, limit=PAGE_SIZE, offset=offset)
    chunks = [await _format_operation(row) for row in rows]
    await callback.message.edit_text("📜 История баллов\n\n" + "\n\n".join(chunks), reply_markup=_history_kb(safe_page, pages))
    await callback.answer()


@router.callback_query(F.data == CB_HISTORY)
async def show_history(callback: CallbackQuery) -> None:
    await _show_history(callback, 0)


@router.callback_query(F.data.startswith(f"{CB_HISTORY_PAGE}:"))
async def show_history_page(callback: CallbackQuery) -> None:
    raw_page = callback.data.split(":")[-1]
    try:
        page = int(raw_page)
    except ValueError:
        page = 0
    await _show_history(callback, page)


async def _render_referral_text(user_id: int, bot_username: str | None) -> str:
    code = await ensure_referral_code(user_id)
    link = await get_referral_link(user_id, bot_username)
    invited, bonuses = await get_referral_stats(user_id)
    return (
        "👥 Пригласи друга\n"
        f"Ваш код: {code}\n"
        f"Ваша ссылка: {link}\n\n"
        "🎁 После первого оплаченного визита друга:\n"
        "— друг получает приветственный бонус\n"
        "— вы получаете бонус за рекомендацию\n\n"
        f"👤 Приглашено друзей: {invited}\n"
        f"💎 Получено бонусов: {bonuses} баллов"
    )


@router.callback_query(F.data == CB_REFERRAL)
async def show_referral(callback: CallbackQuery) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(callback, edit_current=True)
        return
    if not callback.message:
        await callback.answer()
        return
    text = await _render_referral_text(callback.from_user.id, callback.bot.username)
    await callback.message.edit_text(text, reply_markup=_referral_kb())
    await callback.answer()


@router.callback_query(F.data.in_({CB_REFERRAL_CODE, CB_REFERRAL_LINK}))
async def show_referral_copy_friendly(callback: CallbackQuery) -> None:
    if not _is_loyalty_enabled():
        await _send_loyalty_disabled_message(callback)
        return
    if not callback.message:
        await callback.answer()
        return
    code = await ensure_referral_code(callback.from_user.id)
    link = await get_referral_link(callback.from_user.id, callback.bot.username)
    if callback.data == CB_REFERRAL_CODE:
        await callback.answer("Код показан в сообщении 👇")
        await callback.message.answer(f"📋 Ваш реферальный код:\n`{code}`", parse_mode="Markdown")
        return
    await callback.answer("Ссылка показана в сообщении 👇")
    await callback.message.answer(f"🔗 Ваша реферальная ссылка:\n{link}")
