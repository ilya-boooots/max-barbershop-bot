from __future__ import annotations

import csv
import html
import io
import subprocess
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.config import get_settings
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.navigation import push_screen
from app.core.permissions import ROLE_ADMIN, ROLE_DEVELOPER, has_any_role
from app.core.ui_texts import DEV_DIAGNOSTICS_BTN
from app.integrations.yclients import YClientsError
from app.integrations.yclients.clients_sync import (
    normalize_phone_for_yclients,
    yclients_create_client,
    yclients_find_client_by_phone,
    yclients_update_client,
)
from app.integrations.yclients.service import get_yclients_credentials
from app.repositories.diagnostics import get_recent_bot_logs

router = Router()

LOG_LINES_LIMIT = 200
LOG_CHUNK_LIMIT = 3000
STATE_LOG_PAGES_KEY = "devdiag_log_pages"
STATE_LOG_PAGE_INDEX_KEY = "devdiag_log_page_index"


class DevDiagState(StatesGroup):
    waiting_user_query = State()
    waiting_events_query = State()


def _diag_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧾 Логи бота (последние 200 строк)", callback_data="devdiag:bot_logs")],
            [InlineKeyboardButton(text="📦 Скачать логи бота (CSV)", callback_data="devdiag:bot_logs_csv")],
            [InlineKeyboardButton(text="👤 Логи пользователя", callback_data="devdiag:user_logs")],
            [InlineKeyboardButton(text="🔎 Поиск по событиям", callback_data="devdiag:event_search")],
            [InlineKeyboardButton(text="💡 Статус системы", callback_data="devdiag:status")],
            [InlineKeyboardButton(text="🧪 YClients: client sync smoke test", callback_data="devdiag:yclients_smoke")],
            [InlineKeyboardButton(text="♻️ Перезапустить бота (инструкция)", callback_data="devdiag:restart_help")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=NAV_BACK_CALLBACK)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def _devdiag_nav_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="devdiag:menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


def _logs_nav_kb(current_page: int, pages_count: int) -> InlineKeyboardMarkup:
    prev_button = (
        InlineKeyboardButton(text="◀️ Предыдущая", callback_data="devdiag:logs:prev")
        if current_page > 0
        else InlineKeyboardButton(text="⏺", callback_data="devdiag:noop")
    )
    next_button = (
        InlineKeyboardButton(text="▶️ Следующая", callback_data="devdiag:logs:next")
        if current_page < pages_count - 1
        else InlineKeyboardButton(text="⏺", callback_data="devdiag:noop")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [prev_button, next_button],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="devdiag:menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data=NAV_HOME_CALLBACK)],
        ]
    )


async def _ensure_diagnostics_access_message(message: Message) -> bool:
    if await has_any_role(message.from_user.id, {ROLE_DEVELOPER, ROLE_ADMIN}):
        return True
    await message.answer("⛔️ Доступ только для разработчика.", reply_markup=_devdiag_nav_kb())
    return False


async def _ensure_diagnostics_access_callback(callback: CallbackQuery) -> bool:
    if await has_any_role(callback.from_user.id, {ROLE_DEVELOPER, ROLE_ADMIN}):
        return True
    if callback.message:
        await callback.message.answer("⛔️ Доступ только для разработчика.", reply_markup=_devdiag_nav_kb())
    await callback.answer()
    return False


def _format_log_line(row: dict[str, str]) -> str:
    ts = (row.get("ts_utc") or "—").strip()
    level = (row.get("level") or "INFO").strip()
    source = (row.get("source") or "bot").strip()
    message = (row.get("message") or "").strip()
    return f"{ts} | {level} | {source} | {message}"


def _chunk_lines(lines: list[str], limit: int = LOG_CHUNK_LIMIT) -> list[str]:
    if not lines:
        return ["Логи пока пустые."]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        safe_line = line if line else " "
        line_len = len(safe_line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = [safe_line]
            current_len = line_len
            continue
        if not current and line_len > limit:
            chunks.append(safe_line[:limit])
            current = []
            current_len = 0
            continue
        current.append(safe_line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks or ["Логи пока пустые."]


async def _load_logs_lines(limit: int = LOG_LINES_LIMIT) -> list[str] | None:
    try:
        rows = await get_recent_bot_logs(limit)
    except Exception:
        return None
    if not rows:
        return []
    ordered = list(reversed(rows))
    return [_format_log_line(row) for row in ordered]


async def _send_logs_page(target: Message, state: FSMContext, page_index: int, *, edit: bool = False) -> None:
    data = await state.get_data()
    pages = data.get(STATE_LOG_PAGES_KEY)
    if not isinstance(pages, list) or not pages:
        lines = await _load_logs_lines()
        if lines is None:
            await target.answer("⚠️ Не удалось получить логи. Проверьте права/путь к логам.", reply_markup=_devdiag_nav_kb())
            return
        pages = _chunk_lines(lines)

    total = len(pages)
    safe_page = min(max(page_index, 0), total - 1)
    await state.update_data({STATE_LOG_PAGES_KEY: pages, STATE_LOG_PAGE_INDEX_KEY: safe_page})

    body = html.escape(str(pages[safe_page]))
    text = (
        "🧾 Логи бота\n"
        f"Строки: последние {LOG_LINES_LIMIT}\n"
        f"Страница {safe_page + 1}/{total}\n\n"
        f"<code>{body}</code>"
    )

    if edit:
        try:
            await target.edit_text(text, reply_markup=_logs_nav_kb(safe_page, total), parse_mode="HTML")
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=_logs_nav_kb(safe_page, total), parse_mode="HTML")


def _extract_timestamp_and_level(raw_line: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in raw_line.split("|", 3)]
    if len(parts) >= 4:
        return parts[0], parts[1], parts[3]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return "", "", raw_line


def _build_logs_csv(lines: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "level", "message"])
    for line in lines:
        timestamp, level, message = _extract_timestamp_and_level(line)
        writer.writerow([timestamp, level, message])
    return buffer.getvalue().encode("utf-8")


def _git_short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=1,
        )
        sha = result.stdout.strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


async def _run_yclients_smoke_test() -> list[str]:
    credentials, company_id = await get_yclients_credentials()
    settings = get_settings()

    report: list[str] = []
    test_phone = normalize_phone_for_yclients(settings.yclients_smoke_test_phone)
    if not test_phone:
        return [f"❌ Некорректный YCLIENTS_SMOKE_TEST_PHONE: {settings.yclients_smoke_test_phone}"]

    payload = {
        "name": "Smoke Test Bot",
        "phone": test_phone,
        "birth_date": "1990-01-01",
        "bdate": "1990-01-01",
    }

    report.append(
        "ℹ️ auth headers: "
        f"partner_token={'yes' if credentials.partner_token else 'no'}, "
        f"user_token={'yes' if credentials.user_token else 'no'}"
    )

    try:
        existing_client_id = await yclients_find_client_by_phone(company_id=company_id, phone=test_phone)
        report.append(f"✅ search | GET /api/v1/clients/{company_id}?phone=... | found_id={existing_client_id or 'none'}")
    except YClientsError as exc:
        status = exc.status_code if exc.status_code is not None else "n/a"
        report.append(
            f"❌ search | {exc.method or 'GET'} {exc.endpoint or '/api/v1/clients/{company_id}'} "
            f"| status={status} | snippet={(exc.response_snippet or '—')[:120]}"
        )
        return report

    try:
        if existing_client_id is not None:
            updated_id = await yclients_update_client(company_id=company_id, client_id=existing_client_id, payload=payload)
            report.append(f"✅ update | PUT /api/v1/client/{company_id}/{existing_client_id} | client_id={updated_id}")
        else:
            created_id = await yclients_create_client(company_id=company_id, payload=payload)
            report.append(f"✅ create | POST /api/v1/clients/{company_id} | client_id={created_id}")
            verified_id = await yclients_find_client_by_phone(company_id=company_id, phone=test_phone)
            report.append(f"✅ verify | GET /api/v1/clients/{company_id}?phone=... | found_id={verified_id or 'none'}")
    except YClientsError as exc:
        status = exc.status_code if exc.status_code is not None else "n/a"
        report.append(
            f"❌ upsert | {exc.method or 'n/a'} {exc.endpoint or 'n/a'} "
            f"| status={status} | snippet={(exc.response_snippet or '—')[:120]}"
        )

    return report


@router.callback_query(F.data == "devdiag:yclients_smoke")
async def handle_yclients_smoke(callback: CallbackQuery) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return

    try:
        report_lines = await _run_yclients_smoke_test()
        text = "🧪 YClients: client sync smoke test\n\n" + "\n".join(report_lines)
    except Exception as exc:
        text = f"🧪 YClients: client sync smoke test\n\n❌ Ошибка запуска: {type(exc).__name__}: {str(exc)[:300]}"

    await callback.message.answer(text, reply_markup=_devdiag_nav_kb())
    await callback.answer()


@router.message(F.text == DEV_DIAGNOSTICS_BTN)
async def handle_dev_diagnostics(message: Message, state: FSMContext) -> None:
    # Smoke test: Press main menu button "🛠️ Разработка: Диагностика" -> should open diagnostics, not unknown command.
    if not await _ensure_diagnostics_access_message(message):
        return
    await push_screen(state, "dev_diagnostics")
    await message.answer("🛠️ Разработка: Диагностика\nВыберите действие:", reply_markup=_diag_menu_kb())


@router.callback_query(F.data == "devdiag:menu")
async def handle_back_to_diag_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    await state.set_state(None)
    await callback.message.answer("🛠️ Разработка: Диагностика\nВыберите действие:", reply_markup=_diag_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "devdiag:bot_logs")
async def handle_bot_logs(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    lines = await _load_logs_lines()
    if lines is None:
        await callback.message.answer("⚠️ Не удалось получить логи. Проверьте права/путь к логам.", reply_markup=_devdiag_nav_kb())
        await callback.answer()
        return

    data = await state.get_data()
    previous_index = int(data.get(STATE_LOG_PAGE_INDEX_KEY, 0) or 0)
    pages = _chunk_lines(lines)
    max_index = max(len(pages) - 1, 0)
    await state.update_data({STATE_LOG_PAGES_KEY: pages, STATE_LOG_PAGE_INDEX_KEY: min(previous_index, max_index)})
    await _send_logs_page(callback.message, state, min(previous_index, max_index))
    await callback.answer()


@router.callback_query(F.data.in_({"devdiag:logs:prev", "devdiag:logs:next"}))
async def handle_log_pagination(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    data = await state.get_data()
    current = int(data.get(STATE_LOG_PAGE_INDEX_KEY, 0) or 0)
    if callback.data.endswith("prev"):
        current -= 1
    else:
        current += 1
    await _send_logs_page(callback.message, state, current, edit=True)
    await callback.answer()


@router.callback_query(F.data == "devdiag:bot_logs_csv")
async def handle_bot_logs_csv(callback: CallbackQuery) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    lines = await _load_logs_lines()
    if lines is None:
        await callback.message.answer("⚠️ Не удалось получить логи. Проверьте права/путь к логам.", reply_markup=_devdiag_nav_kb())
        await callback.answer()
        return

    csv_bytes = _build_logs_csv(lines)
    document = BufferedInputFile(csv_bytes, filename="bot_logs_last_200.csv")
    await callback.message.answer_document(document)
    await callback.answer("CSV отправлен")


@router.callback_query(F.data == "devdiag:user_logs")
async def handle_user_logs_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    await state.set_state(DevDiagState.waiting_user_query)
    await callback.message.answer(
        "👤 Логи пользователя\n\nВведите user_id или @username:",
        reply_markup=_devdiag_nav_kb(),
    )
    await callback.answer()


@router.message(DevDiagState.waiting_user_query)
async def handle_user_logs_search(message: Message, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_message(message):
        return
    await state.set_state(None)
    await message.answer(
        "👤 Логи пользователя\n\n🚧 В разработке",
        reply_markup=_devdiag_nav_kb(),
    )


@router.callback_query(F.data == "devdiag:event_search")
async def handle_event_search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    await state.set_state(DevDiagState.waiting_events_query)
    await callback.message.answer(
        "🔎 Поиск по событиям\n\nВведите ключевое слово:",
        reply_markup=_devdiag_nav_kb(),
    )
    await callback.answer()


@router.message(DevDiagState.waiting_events_query)
async def handle_event_search_query(message: Message, state: FSMContext) -> None:
    if not await _ensure_diagnostics_access_message(message):
        return
    await state.set_state(None)
    await message.answer(
        "🔎 Поиск по событиям\n\n🚧 В разработке",
        reply_markup=_devdiag_nav_kb(),
    )


@router.callback_query(F.data == "devdiag:status")
async def handle_status(callback: CallbackQuery) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    now = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
    sha = _git_short_sha()
    await callback.message.answer(
        "💡 Статус системы\n\n"
        "✅ Бот запущен и работает.\n"
        f"🕒 Время сервера: {now}\n"
        f"📦 Версия: {sha}\n",
        reply_markup=_devdiag_nav_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "devdiag:restart_help")
async def handle_restart_help(callback: CallbackQuery) -> None:
    if not await _ensure_diagnostics_access_callback(callback):
        return
    await callback.message.answer(
        "♻️ Перезапуск бота\n\n"
        "1) SSH на сервер\n"
        "2) sudo systemctl restart telegram-bot@barbershop-bot\n"
        "3) sudo systemctl status telegram-bot@barbershop-bot",
        reply_markup=_devdiag_nav_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "devdiag:noop")
async def handle_noop(callback: CallbackQuery) -> None:
    await callback.answer()
