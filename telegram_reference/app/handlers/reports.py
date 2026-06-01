from __future__ import annotations

import csv
from datetime import datetime
from io import BytesIO, StringIO
from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.core.auth import has_role, normalize_role
from app.core.navigation import push_screen
from app.core.screens import render_staff_reports
from app.db.reports_repo import (
    find_clients,
    get_all_clients,
    get_client_by_tg_id,
    get_clients_page,
)
from app.keyboards.reports import reports_list_kb, reports_menu_kb, reports_search_results_kb
from app.repositories.users import get_user as get_db_user

router = Router()

ALLOWED_REPORT_ROLES = ["admin", "developer", "manager"]


class ReportsStates(StatesGroup):
    awaiting_search_query = State()
    awaiting_open_query = State()


def _client_label(client: dict) -> str:
    return str(client.get("name") or client.get("username") or client.get("tg_id") or "—")


def _render_client_card(client: dict) -> str:
    username = client.get("username") or "—"
    if username != "—" and not str(username).startswith("@"):
        username = f"@{username}"
    return (
        "👤 Карточка клиента\n\n"
        f"Telegram ID: {client.get('tg_id', '—')}\n"
        f"Username: {username}\n"
        f"Имя / ФИО: {client.get('name') or '—'}\n"
        f"Телефон: {client.get('phone') or '—'}\n"
        f"Дата регистрации: {client.get('registered_at') or '—'} (GMT+4)\n"
        f"Последняя активность: {client.get('last_activity') or '—'} (GMT+4)\n"
        f"Количество визитов: {int(client.get('visits_count') or 0)}\n"
        f"Бонусный баланс: {int(client.get('bonus_balance') or 0)}\n"
        f"Всего броней: {int(client.get('bookings_total') or 0)}\n"
        f"Подтверждённых броней: {int(client.get('bookings_approved') or 0)}\n"
        f"Отменённых броней: {int(client.get('bookings_cancelled') or 0)}\n"
        f"Последняя бронь: {client.get('last_booking') or '—'}\n"
        f"Заметки/теги: {client.get('notes') or '—'}"
    )


async def _ensure_reports_access_message(message: Message) -> bool:
    user = await get_db_user(message.from_user.id)
    role = normalize_role(message.from_user.id, user["role"] if user else None)
    if not has_role(role, ALLOWED_REPORT_ROLES):
        await message.answer("⛔️ Недостаточно прав.")
        return False
    return True


async def _ensure_reports_access_callback(callback: CallbackQuery) -> bool:
    user = await get_db_user(callback.from_user.id)
    role = normalize_role(callback.from_user.id, user["role"] if user else None)
    if not has_role(role, ALLOWED_REPORT_ROLES):
        await callback.answer("⛔️ Недостаточно прав.", show_alert=True)
        return False
    return True


async def _send_clients_list(target: Message | CallbackQuery, page: int) -> None:
    clients, total = await get_clients_page(page=page, page_size=10)
    total_pages = max(1, ceil(total / 10))
    safe_page = min(max(1, page), total_pages)
    if safe_page != page:
        clients, total = await get_clients_page(page=safe_page, page_size=10)

    if not clients:
        text = "📄 Список клиентов пуст."
    else:
        lines = [f"📄 Список клиентов • Стр. {safe_page}/{total_pages}", ""]
        for idx, client in enumerate(clients, start=1):
            lines.append(
                f"{idx}. 👤 {_client_label(client)} | {client.get('phone') or '—'} | "
                f"визитов: {int(client.get('visits_count') or 0)} | броней: {int(client.get('bookings_total') or 0)}"
            )
        text = "\n".join(lines)

    markup = reports_list_kb(clients, safe_page, total_pages)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(F.text == "📊 Отчёты")
async def handle_reports_menu(message: Message, state: FSMContext) -> None:
    if not await _ensure_reports_access_message(message):
        return
    await push_screen(state, "staff_reports")
    await render_staff_reports(message)


@router.callback_query(F.data == "reports:menu")
async def handle_reports_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    await push_screen(state, "staff_reports")
    if callback.message:
        await render_staff_reports(callback)
    await callback.answer()


@router.callback_query(F.data == "reports:csv")
async def handle_reports_csv(callback: CallbackQuery) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    clients = await get_all_clients()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "tg_id",
            "username",
            "name",
            "phone",
            "registered_at",
            "last_activity",
            "visits_count",
            "bonus_balance",
            "bookings_total",
            "bookings_approved",
            "bookings_cancelled",
            "last_booking",
            "notes",
        ]
    )
    for client in clients:
        writer.writerow(
            [
                client.get("tg_id") or "",
                client.get("username") or "",
                client.get("name") or "",
                client.get("phone") or "",
                client.get("registered_at") or "",
                client.get("last_activity") or "",
                int(client.get("visits_count") or 0),
                int(client.get("bonus_balance") or 0),
                int(client.get("bookings_total") or 0),
                int(client.get("bookings_approved") or 0),
                int(client.get("bookings_cancelled") or 0),
                client.get("last_booking") or "",
                client.get("notes") or "",
            ]
        )

    payload = output.getvalue().encode("utf-8-sig")
    file_name = f"clients_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    document = BufferedInputFile(payload, filename=file_name)
    if callback.message:
        await callback.message.answer_document(document=document)
    await callback.answer("CSV сформирован")


@router.callback_query(F.data.startswith("reports:list:"))
async def handle_reports_list(callback: CallbackQuery) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    try:
        page = int((callback.data or "").split(":")[-1])
    except ValueError:
        page = 1
    await _send_clients_list(callback, page)
    await callback.answer()


@router.callback_query(F.data == "reports:search:prompt")
async def handle_reports_search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    await state.set_state(ReportsStates.awaiting_search_query)
    if callback.message:
        await callback.message.answer("Введите Telegram ID / телефон / @username")
    await callback.answer()


@router.callback_query(F.data.startswith("reports:open_prompt:"))
async def handle_reports_open_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    page = int((callback.data or "reports:open_prompt:1").split(":")[-1])
    await state.set_state(ReportsStates.awaiting_open_query)
    await state.update_data(reports_page=page)
    if callback.message:
        await callback.message.answer("Введите Telegram ID / телефон / @username")
    await callback.answer()


@router.message(ReportsStates.awaiting_search_query)
async def handle_reports_search_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_reports_access_message(message):
        return
    query = (message.text or "").strip()
    clients = await find_clients(query, limit=10)
    await state.clear()

    if not clients:
        await message.answer("По запросу ничего не найдено.", reply_markup=reports_menu_kb())
        return

    if len(clients) == 1:
        await message.answer(_render_client_card(clients[0]), reply_markup=reports_menu_kb())
        return

    await message.answer(
        "Найдено несколько клиентов. Выберите:",
        reply_markup=reports_search_results_kb(clients[:10]),
    )


@router.message(ReportsStates.awaiting_open_query)
async def handle_reports_open_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_reports_access_message(message):
        return
    data = await state.get_data()
    page = int(data.get("reports_page") or 1)
    query = (message.text or "").strip()

    client: dict | None = None
    if query.isdigit():
        client = await get_client_by_tg_id(int(query))
    if not client:
        clients = await find_clients(query, limit=10)
        if len(clients) == 1:
            client = clients[0]
        elif len(clients) > 1:
            await state.clear()
            await message.answer(
                "Найдено несколько клиентов. Выберите:",
                reply_markup=reports_search_results_kb(clients[:10]),
            )
            return

    await state.clear()
    if not client:
        await message.answer("По запросу ничего не найдено.")
        await _send_clients_list(message, page)
        return

    await message.answer(_render_client_card(client), reply_markup=reports_menu_kb())


@router.callback_query(F.data.startswith("reports:open:"))
async def handle_reports_open_client(callback: CallbackQuery) -> None:
    if not await _ensure_reports_access_callback(callback):
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    tg_id = int(parts[2])
    client = await get_client_by_tg_id(tg_id)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    if callback.message:
        await callback.message.answer(_render_client_card(client), reply_markup=reports_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "reports:noop")
async def handle_reports_noop(callback: CallbackQuery) -> None:
    await callback.answer()
