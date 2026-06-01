from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.core.permissions import resolve_role
from app.keyboards.factory import get_main_menu_kb
from app.repositories.users import get_user

NAV_STACK_KEY = "__nav_stack__"
FIRST_LEVEL_SCREENS = {"main_menu"}

SCREEN_MAIN_MENU = "main_menu"

logger = logging.getLogger(__name__)


class EditableMessageProxy:
    def __init__(self, message: Message) -> None:
        self._message = message

    def __getattr__(self, item: str) -> Any:
        return getattr(self._message, item)

    async def answer(self, text: str, **kwargs: Any) -> Message:
        try:
            return await self._message.edit_text(text, **kwargs)
        except Exception:
            return await self._message.answer(text, **kwargs)


async def get_role(user_id: int) -> str:
    role = await resolve_role(user_id)
    return role or "user"


def _sanitize_display_name(raw_value: str | None) -> str | None:
    cleaned = " ".join((raw_value or "").split()).strip()
    return cleaned or None


async def get_user_display_name_for_menu(message_or_callback: Message | CallbackQuery, user_id: int) -> str:
    user = await get_user(user_id)
    if user:
        # Priority: registered profile name, then optional display_name.
        profile_name = _sanitize_display_name(str(user.get("name") or ""))
        if profile_name:
            return profile_name
        display_name = _sanitize_display_name(str(user.get("display_name") or ""))
        if display_name:
            return display_name
    telegram_name = _sanitize_display_name(getattr(message_or_callback.from_user, "first_name", None))
    if telegram_name:
        return telegram_name
    return "гость"


async def build_main_menu_text(message_or_callback: Message | CallbackQuery, user_id: int) -> str:
    name = await get_user_display_name_for_menu(message_or_callback, user_id)
    return f"✨ {name}, выберите действие в меню ниже 👇"


async def render_main_menu_for_user(message_or_callback: Message | CallbackQuery, user_id: int) -> None:
    role = await get_role(user_id)
    target: Message | EditableMessageProxy
    if isinstance(message_or_callback, CallbackQuery):
        if not message_or_callback.message:
            return
        target = EditableMessageProxy(message_or_callback.message)
    else:
        target = message_or_callback
    logger.info("render_main_menu user_id=%s role=%s", user_id, role)
    await target.answer(
        await build_main_menu_text(message_or_callback, user_id),
        reply_markup=await get_main_menu_kb(user_id, role),
    )


async def render_screen(
    screen_id: str,
    message_or_callback: Message | CallbackQuery,
    user_id: int,
    *,
    payload: Mapping[str, Any] | None = None,
    state: FSMContext | None = None,
) -> None:
    if screen_id == SCREEN_MAIN_MENU:
        await render_main_menu_for_user(message_or_callback, user_id)
        return

    target_message = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback
    if target_message is None:
        return

    if screen_id == "personnel_menu":
        from app.core.screens import render_personnel_menu_for_user

        await render_personnel_menu_for_user(target_message, user_id)
        return
    if screen_id == "settings" and state is not None:
        from app.handlers.master_photos_settings import open_settings_menu

        await open_settings_menu(message_or_callback, state)
        return
    if screen_id == "support":
        from app.services.support import render_support_message, resolve_support_settings, support_screen_kb

        _, support_settings, _ = await resolve_support_settings()
        await target_message.answer(render_support_message(support_settings.description), reply_markup=support_screen_kb(username=support_settings.username))
        return
    if screen_id == "messages":
        from app.ui.navigation import nav_inline_kb
        from app.ui.texts import MESSAGES_PLACEHOLDER

        await target_message.answer(MESSAGES_PLACEHOLDER, reply_markup=nav_inline_kb())
        return
    if screen_id == "statistics":
        from app.handlers.statistics import render_statistics_summary_screen

        await render_statistics_summary_screen(target_message)
        return
    if screen_id == "dev_admin_panel":
        from app.ui.navigation import nav_inline_kb
        from app.ui.texts import DEV_ADMIN_PLACEHOLDER

        await target_message.answer(DEV_ADMIN_PLACEHOLDER, reply_markup=nav_inline_kb())
        return
    if screen_id == "broadcast_root":
        from app.handlers.notifications import broadcast_root_kb

        await target_message.answer("📣 Рассылка\n\nВыберите раздел 👇", reply_markup=broadcast_root_kb(user_id))
        return
    if screen_id == "broadcast_history_root":
        from app.handlers.notifications import notification_history_root_kb

        await target_message.answer(
            "📜 История уведомлений\n\n"
            "Здесь видно, какие уведомления бот отправлял клиентам: автоматические воронки, ручные рассылки и результат доставки.",
            reply_markup=notification_history_root_kb(),
        )
        return
    if screen_id == "broadcast_one_time":
        from app.handlers.notifications import broadcast_placeholder_kb

        await target_message.answer(
            "✉️ Разовая рассылка\n\n"
            "Здесь будет ручная отправка сообщений клиентам.\n"
            "В будущем этот раздел позволит отправлять текст, фото и акции выбранной аудитории.",
            reply_markup=broadcast_placeholder_kb(),
        )
        return

    if screen_id == "one_time_broadcast_audience_selection":
        from app.handlers.notifications import BroadcastStates, one_time_audience_kb

        if state is not None:
            await state.set_state(BroadcastStates.waiting_segment)
        await target_message.answer("✉️ Разовая рассылка\n\nВыберите аудиторию 👇", reply_markup=one_time_audience_kb())
        return
    if screen_id == "one_time_broadcast_preview":
        from app.handlers.notifications import preview_kb
        d = await state.get_data() if state is not None else {}
        await target_message.answer("👀 Предпросмотр рассылки\n\nОтправить рассылку?", reply_markup=preview_kb(bool(d.get("photo_file_id"))))
        return
    if screen_id == "one_time_broadcast_empty_audience":
        from app.handlers.notifications import _with_nav

        await target_message.answer('😌 В этой аудитории пока нет клиентов для рассылки.', reply_markup=_with_nav([]))
        return
    if screen_id == "broadcast_settings_root":
        from app.handlers.notifications import automation_root_kb

        await target_message.answer(
            "⚙️ Настройки рассылок\n\n"
            "Здесь настраиваются автоматические уведомления и правила рассылок. "
            "Бот будет сам возвращать клиентов по заданным сценариям.",
            reply_markup=automation_root_kb(),
        )
        return
    if screen_id.startswith("broadcast_settings_"):
        from app.handlers.notifications import _render_automation_module

        module_key = screen_id.removeprefix("broadcast_settings_")
        await _render_automation_module(target_message, module_key)
        return
    if screen_id == "broadcast_segments":
        from app.handlers.notifications import _segment_root_text, segment_root_kb
        await target_message.answer(_segment_root_text(), reply_markup=segment_root_kb())
        return
    if screen_id == "broadcast_segment_detail":
        from app.handlers.notifications import SEGMENTS, _format_segment_detail, _segment_detail_kb, segment_service
        segment_key = str((payload or {}).get("segment_key") or "")
        if segment_key in SEGMENTS:
            summary = await segment_service.get_segment_summary(segment_key)
            await target_message.answer(
                _format_segment_detail(summary.title, summary.description, summary.count, summary.updated_local, summary.warning),
                reply_markup=_segment_detail_kb(f"broadcast:segments:refresh_one:{segment_key}", use_callback=f"broadcast:segments:use:{segment_key}"),
            )
            return
    if screen_id == "broadcast_lost_clients":
        from app.handlers.notifications import broadcast_placeholder_kb

        await target_message.answer(
            "😔 Потерянные клиенты\n\n"
            "Здесь будет автоматический возврат клиентов, которые давно не были в барбершопе и не имеют будущей записи.\n\n"
            "Бот сможет сам находить таких клиентов и отправлять им мягкое приглашение записаться снова.",
            reply_markup=broadcast_placeholder_kb(),
        )
        return
    if screen_id == "broadcast_efficiency":
        from app.handlers.notifications import broadcast_placeholder_kb

        await target_message.answer(
            "📊 Эффективность\n\n"
            "Здесь будет статистика рассылок и автоворонок: отправлено сообщений, сколько клиентов вернулось, "
            "сколько записей создано и какую выручку это принесло.",
            reply_markup=broadcast_placeholder_kb(),
        )
        return

    from app.core.screens import render_screen as render_registered_screen

    await render_registered_screen(target_message, screen_id, dict(payload or {}))


async def _get_stack(state: FSMContext) -> list[dict[str, Any]]:
    data = await state.get_data()
    stack = data.get(NAV_STACK_KEY, [])
    if not isinstance(stack, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in stack:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        payload = item.get("payload")
        normalized.append({"name": item["name"], "payload": dict(payload) if isinstance(payload, Mapping) else {}})
    return normalized


async def push_screen(state: FSMContext, screen_name: str, payload: Mapping[str, Any] | None = None) -> None:
    if screen_name in FIRST_LEVEL_SCREENS:
        stack: list[dict[str, Any]] = []
    else:
        stack = await _get_stack(state)
    next_item = {"name": screen_name, "payload": dict(payload or {})}
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].get("name") == screen_name and stack[index].get("payload") == next_item["payload"]:
            await state.update_data({NAV_STACK_KEY: stack[: index + 1]})
            return
    stack.append(next_item)
    await state.update_data({NAV_STACK_KEY: stack})


async def pop_screen(state: FSMContext) -> tuple[str, dict[str, Any]] | None:
    stack = await _get_stack(state)
    if not stack:
        return None
    item = stack.pop()
    await state.update_data({NAV_STACK_KEY: stack})
    return item["name"], dict(item.get("payload") or {})


async def peek_screen(state: FSMContext) -> tuple[str, dict[str, Any]] | None:
    stack = await _get_stack(state)
    if not stack:
        return None
    item = stack[-1]
    return item["name"], dict(item.get("payload") or {})


async def reset_stack(state: FSMContext) -> None:
    await state.update_data({NAV_STACK_KEY: []})


async def clear_state_preserving_navigation(state: FSMContext) -> None:
    data = await state.get_data()
    stack = data.get(NAV_STACK_KEY, [])
    await state.clear()
    await state.update_data({NAV_STACK_KEY: stack if isinstance(stack, list) else []})


async def _log_navigation_diagnostics(
    *,
    state: FSMContext,
    route: str,
    user_id: int,
    callback_data: str | None = None,
    message_text: str | None = None,
    exc: Exception | None = None,
) -> None:
    current_state = await state.get_state()
    data = await state.get_data()
    stack = data.get(NAV_STACK_KEY, [])
    stack_len = len(stack) if isinstance(stack, list) else 0
    stack_top = stack[-1] if isinstance(stack, list) and stack else None
    keys = sorted([str(key) for key in data.keys()])
    logger_method = logger.exception if exc else logger.warning
    logger_method(
        "nav_issue route=%s tg_id=%s callback=%s message=%s state=%s fsm_keys=%s nav_len=%s nav_top=%s",
        route,
        user_id,
        callback_data,
        message_text,
        current_state,
        keys,
        stack_len,
        stack_top,
        exc_info=exc,
    )


async def back_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_state_preserving_navigation(state)
    await render_previous_screen(callback, state)
    await callback.answer()


async def render_previous_screen(message_or_callback: Message | CallbackQuery, state: FSMContext) -> None:
    user_id = message_or_callback.from_user.id
    await pop_screen(state)
    previous = await peek_screen(state)
    logger.info("nav:back user_id=%s previous=%s", user_id, previous[0] if previous else SCREEN_MAIN_MENU)
    if not previous:
        await _log_navigation_diagnostics(
            state=state,
            route="back_empty_history",
            user_id=user_id,
            callback_data=(message_or_callback.data if isinstance(message_or_callback, CallbackQuery) else None),
            message_text=(message_or_callback.text if isinstance(message_or_callback, Message) else None),
        )
        await render_main_menu_for_user(message_or_callback, user_id)
        return
    screen_name, payload = previous
    try:
        await render_screen(screen_name, message_or_callback, user_id, payload=payload, state=state)
    except Exception as exc:
        await _log_navigation_diagnostics(
            state=state,
            route=f"back_render_failed:{screen_name}",
            user_id=user_id,
            callback_data=(message_or_callback.data if isinstance(message_or_callback, CallbackQuery) else None),
            message_text=(message_or_callback.text if isinstance(message_or_callback, Message) else None),
            exc=exc,
        )
        await render_main_menu_for_user(message_or_callback, user_id)


async def home_handler(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await clear_state_preserving_navigation(state)
    await reset_stack(state)
    logger.info("nav:home user_id=%s", user_id)
    await render_main_menu_for_user(callback, user_id)
    await callback.answer()


# backward compatibility
render_main_menu = render_main_menu_for_user
render_main_by_role = render_main_menu_for_user
go_back = back_handler
go_home = home_handler
