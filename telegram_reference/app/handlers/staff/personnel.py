from __future__ import annotations

from datetime import datetime, timezone
from html import escape as html_escape
from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.core.navigation import clear_state_preserving_navigation, push_screen
from app.core.staff_permissions import (
    can_assign_role,
    can_manage_roles,
    can_remove_or_change_target,
    can_view_personnel,
    is_protected_developer,
    resolve_role,
)
from app.keyboards.menu import back_reply_kb
from app.keyboards.staff import (
    staff_card_kb,
    staff_list_kb,
    staff_logs_pagination_kb,
    staff_name_confirm_kb,
    staff_remove_confirm_kb,
    staff_remove_list_kb,
    staff_role_select_kb,
)
from app.repositories.staff_action_logs import add_staff_action_log, count_staff_action_logs, get_staff_action_logs, log_role_assigned, log_role_removed
from app.repositories.staff_audit import add_audit, get_audit_last
from app.repositories.users import get_user_by_tg_id, get_users_by_ids, list_staff, set_staff_display_name, set_user_role
from app.services.role_onboarding import maybe_start_role_onboarding
from app.utils.datetime import format_branch_datetime, format_datetime_in_timezone
from app.utils.staff import display_name, format_issuer_name, format_staff_card, normalize_name, role_label, short_name

router = Router()

STAFF_ROLES = {"developer", "manager", "admin"}


class StaffPersonnelStates(StatesGroup):
    awaiting_assign_tg_id = State()
    awaiting_assign_name_confirm = State()
    awaiting_assign_custom_name = State()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _actor_role(user_id: int) -> str:
    actor = await get_user_by_tg_id(user_id)
    return resolve_role(user_id, actor.get("role") if actor else None)


def _parse_tg_id(value: str | None) -> int | None:
    if not value:
        return None
    clean = value.strip()
    return int(clean) if clean.isdigit() else None


async def _deny(callback: CallbackQuery | None = None, message: Message | None = None, text: str = "⛔️ Недостаточно прав.") -> None:
    if callback and callback.message:
        await callback.message.answer(text)
        await callback.answer()
    if message:
        await message.answer(text)


@router.callback_query(F.data == "staff:menu:show_all")
async def handle_show_all(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_view_personnel(role):
        await _deny(callback=callback)
        return
    await push_screen(state, "personnel_menu")
    await push_screen(state, "staff_list")
    await add_staff_action_log(callback.from_user.id, "Просмотрел список персонала")
    staff = await list_staff()
    lines = ["👥 Персонал ресторана", f"Всего: {len(staff)}", ""]
    buttons: list[tuple[int, str]] = []
    for idx, member in enumerate(staff, start=1):
        name = display_name(member)
        member_role = member.get("role")
        assigned_by = normalize_name(member.get("assigned_by_display_name") or member.get("assigned_by_name"))
        lines.append(
            f"{idx}) {role_label(member_role)}\n"
            f"👤 {name}\n"
            f"📅 С {await format_branch_datetime(member.get('role_assigned_at'))}\n"
            f"🛠 Выдал: {assigned_by}\n"
        )
        buttons.append((int(member["user_id"]), f"{short_name(name)} ({role_label(member_role)})"))
    await callback.message.answer("\n".join(lines), reply_markup=staff_list_kb(buttons))
    await callback.answer()


@router.callback_query(F.data == "staff:menu:assign")
async def handle_assign_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_manage_roles(role):
        await add_staff_action_log(callback.from_user.id, "Попытался открыть назначение ролей (запрещено)")
        await _deny(callback=callback, text="⛔️ Только разработчик или управляющий может назначать роли.")
        return
    await push_screen(state, "personnel_menu")
    await push_screen(state, "personnel_waiting_id")
    await state.set_state(StaffPersonnelStates.awaiting_assign_tg_id)
    await callback.message.answer(
        "🧩 Как получить Telegram ID сотрудника:\n"
        "1) Откройте @userinfobot\n2) Нажмите Start\n"
        "3) Пришлите сюда только цифры (например: 123456789)\n\n"
        "✍️ Введите Telegram ID сотрудника:",
        reply_markup=back_reply_kb(),
    )
    await callback.answer()


@router.message(StaffPersonnelStates.awaiting_assign_tg_id)
async def handle_assign_id(message: Message, state: FSMContext) -> None:
    role = await _actor_role(message.from_user.id)
    if not can_manage_roles(role):
        await _deny(message=message)
        return
    target_tg_id = _parse_tg_id(message.text)
    if target_tg_id is None:
        await message.answer("⛔️ Telegram ID должен содержать только цифры.")
        return
    target = await get_user_by_tg_id(target_tg_id)
    if not target:
        await message.answer("❌ Пользователь не найден в базе. Пусть сначала нажмёт /start.")
        return
    await state.update_data(target_tg_id=target_tg_id, target_role=target.get("role") or "user", telegram_name=display_name(target))
    await state.set_state(StaffPersonnelStates.awaiting_assign_name_confirm)
    await message.answer(f"👤 Имя сотрудника: «{display_name(target)}»\nОставить это имя?", reply_markup=staff_name_confirm_kb())


@router.callback_query(F.data == "staff:name:keep")
async def handle_name_keep(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    target_tg_id = int(data.get("target_tg_id") or 0)
    role = await _actor_role(callback.from_user.id)
    if not target_tg_id:
        await callback.answer("Данные потеряны", show_alert=True)
        return
    target = await get_user_by_tg_id(target_tg_id)
    if not target:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return
    await set_staff_display_name(target_tg_id, data.get("telegram_name") or display_name(target))
    await message_role_picker(callback, role, target_tg_id, target.get("role") or "user")


@router.callback_query(F.data == "staff:name:edit")
async def handle_name_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StaffPersonnelStates.awaiting_assign_custom_name)
    if callback.message:
        await callback.message.answer("✍️ Введите имя сотрудника:")
    await callback.answer()


@router.message(StaffPersonnelStates.awaiting_assign_custom_name)
async def handle_custom_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    target_tg_id = int(data.get("target_tg_id") or 0)
    if not target_tg_id:
        await message.answer("❌ Данные сотрудника не найдены.")
        return
    clean_name = (message.text or "").strip()
    if len(clean_name) < 2 or len(clean_name) > 64:
        await message.answer("⛔️ Имя должно быть от 2 до 64 символов.")
        return
    role = await _actor_role(message.from_user.id)
    target = await get_user_by_tg_id(target_tg_id)
    target_role = target.get("role") if target else "user"
    await set_staff_display_name(target_tg_id, clean_name)
    await message.answer(
        "Выберите роль для сотрудника:",
        reply_markup=staff_role_select_kb(message.from_user.id, role, target_tg_id, target_role),
    )


async def message_role_picker(callback: CallbackQuery, actor_role: str, target_tg_id: int, target_role: str) -> None:
    if callback.message:
        await callback.message.answer(
            "Выберите роль для сотрудника:",
            reply_markup=staff_role_select_kb(callback.from_user.id, actor_role, target_tg_id, target_role),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("staff:assign:"))
async def handle_assign_role(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await callback.answer("Некорректная команда", show_alert=True)
        return
    actor_role = await _actor_role(callback.from_user.id)
    target_tg_id = int(parts[2])
    new_role = parts[3]
    target = await get_user_by_tg_id(target_tg_id)
    old_role = target.get("role") if target else "user"
    if not can_assign_role(callback.from_user.id, actor_role, target_tg_id, new_role):
        await add_staff_action_log(callback.from_user.id, f"Попытался назначить роль «{role_label(new_role)}» пользователю {display_name(target or {})} (запрещено)", target_tg_id=target_tg_id, action_type="role_assign_denied")
        await _deny(callback=callback, text="⛔️ Недостаточно прав для назначения этой роли.")
        return
    changed_at = _now_iso()
    await set_user_role(target_tg_id, new_role, callback.from_user.id, changed_at)
    await add_audit(target_tg_id=target_tg_id, old_role=old_role or "user", new_role=new_role, changed_by_tg_id=callback.from_user.id, changed_at_iso=changed_at)
    await log_role_assigned(actor_tg_id=callback.from_user.id, actor_role=actor_role, target_tg_id=target_tg_id, target_name=display_name(target or {}), new_role=new_role, old_role=old_role)
    await clear_state_preserving_navigation(state)
    await callback.message.answer("✅ Роль успешно назначена.")
    try:
        await callback.bot.send_message(
            target_tg_id,
            f"🎉 Поздравляем! Вам выдали роль: {role_label(new_role)}\nТеперь вам доступны новые функции в боте.",
        )
        if new_role in {"manager", "admin"}:
            await maybe_start_role_onboarding(callback.bot, target_tg_id, new_role)
    except Exception:
        await add_staff_action_log(callback.from_user.id, "Не удалось отправить уведомление о назначении роли", target_tg_id=target_tg_id)
    await callback.answer()


@router.callback_query(F.data == "staff:menu:remove")
async def handle_remove_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_manage_roles(role):
        await add_staff_action_log(callback.from_user.id, "Попытался открыть снятие ролей (запрещено)")
        await _deny(callback=callback, text="⛔️ Только разработчик или управляющий может снимать роли.")
        return
    await push_screen(state, "personnel_menu")
    await push_screen(state, "staff_remove_list")
    staff = await list_staff()
    items = [(int(row["user_id"]), f"{short_name(display_name(row))} ({role_label(row.get('role'))})") for row in staff]
    await callback.message.answer("➖ Выберите сотрудника, у которого нужно снять роль:", reply_markup=staff_remove_list_kb(items))
    await callback.answer()


@router.callback_query(F.data.startswith("staff:remove:pick:"))
async def handle_remove_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    parts = callback.data.split(":")
    target_tg_id = int(parts[-1])
    actor_role = await _actor_role(callback.from_user.id)
    target = await get_user_by_tg_id(target_tg_id)
    if not target:
        await callback.message.answer("❌ Сотрудник не найден.")
        await callback.answer()
        return
    await push_screen(state, "staff_remove_list")
    await push_screen(state, "staff_remove_confirm", {"target_tg_id": target_tg_id})
    can_remove = can_remove_or_change_target(callback.from_user.id, actor_role, target_tg_id, target.get("role"))
    card_text = format_staff_card(target)
    if is_protected_developer(target_tg_id):
        card_text += "\n\n⛔ Нельзя снять роль у защищённого разработчика."
    await callback.message.answer(card_text, reply_markup=staff_remove_confirm_kb(target_tg_id, can_remove), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("staff:remove:confirm:"))
async def handle_remove_confirm(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    target_tg_id = int(callback.data.split(":")[-1])
    actor_role = await _actor_role(callback.from_user.id)
    target = await get_user_by_tg_id(target_tg_id)
    if not target:
        await callback.message.answer("❌ Сотрудник не найден.")
        await callback.answer()
        return
    if is_protected_developer(target_tg_id):
        await add_staff_action_log(callback.from_user.id, "Попытался снять роль у защищённого разработчика (запрещено).", target_tg_id=target_tg_id, action_type="protected_developer_remove_denied")
        await _deny(callback=callback, text="⛔ Нельзя снять роль у защищённого разработчика.")
        return
    if not can_remove_or_change_target(callback.from_user.id, actor_role, target_tg_id, target.get("role")):
        await add_staff_action_log(callback.from_user.id, "Попытался снять роль без прав (запрещено).", target_tg_id=target_tg_id, action_type="role_remove_denied")
        await _deny(callback=callback, text="⛔️ Недостаточно прав для снятия роли.")
        return
    old_role = target.get("role") or "user"
    changed_at = _now_iso()
    await set_user_role(target_tg_id, "user", callback.from_user.id, changed_at)
    await add_audit(target_tg_id=target_tg_id, old_role=old_role, new_role="user", changed_by_tg_id=callback.from_user.id, changed_at_iso=changed_at)
    await log_role_removed(actor_tg_id=callback.from_user.id, actor_role=actor_role, target_tg_id=target_tg_id, target_name=display_name(target), old_role=old_role)
    await callback.message.answer("✅ Роль снята. Теперь пользователь обычный клиент.")
    await callback.answer()


@router.callback_query(F.data.startswith("staff:card:open:"))
async def handle_open_card(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_view_personnel(role):
        await _deny(callback=callback)
        return
    target_tg_id = int(callback.data.split(":")[-1])
    target = await get_user_by_tg_id(target_tg_id)
    if not target or target.get("role") not in STAFF_ROLES:
        await callback.message.answer("❌ Сотрудник не найден.")
        await callback.answer()
        return
    if target.get("role_assigned_by_tg_id"):
        by_map = await get_users_by_ids([int(target["role_assigned_by_tg_id"])])
        target.update({
            "assigned_by_name": by_map.get(int(target["role_assigned_by_tg_id"]), {}).get("name"),
            "assigned_by_display_name": by_map.get(int(target["role_assigned_by_tg_id"]), {}).get("display_name"),
        })
    await push_screen(state, "staff_list")
    await push_screen(state, "staff_card", {"target_tg_id": target_tg_id})
    await add_staff_action_log(callback.from_user.id, f"Открыл карточку сотрудника {display_name(target)}", target_tg_id=target_tg_id)
    can_manage_target = can_manage_roles(role) and not is_protected_developer(target_tg_id)
    await callback.message.answer(format_staff_card(target), reply_markup=staff_card_kb(can_manage_target, target_tg_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("staff:card:history:"))
async def handle_history(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_view_personnel(role):
        await _deny(callback=callback)
        return
    target_tg_id = int(callback.data.split(":")[-1])
    await push_screen(state, "staff_card", {"target_tg_id": target_tg_id})
    await push_screen(state, "staff_role_history", {"target_tg_id": target_tg_id})
    history = await get_audit_last(target_tg_id, limit=20)
    if not history:
        await callback.message.answer("📚 История ролей пока пустая.")
        await callback.answer()
        return
    actors = await get_users_by_ids([int(row["changed_by_tg_id"]) for row in history if row.get("changed_by_tg_id")])
    lines = ["📚 <b>История ролей</b>", ""]
    for row in history:
        old_role = row.get("old_role")
        new_role = row.get("new_role")
        if old_role == "user":
            action = f"Назначена роль «{role_label(new_role)}»"
        elif new_role == "user":
            action = f"Снята роль «{role_label(old_role)}»"
        else:
            action = f"Изменена роль «{role_label(old_role)}» → «{role_label(new_role)}»"
        actor_name = format_issuer_name((actors.get(int(row["changed_by_tg_id"]), {}) or {}).get("display_name"))
        lines.append(f"{await format_branch_datetime(row.get('changed_at'))} — {action}. Выполнил: {html_escape(actor_name, quote=False)}")
    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("staff:card:logs:"))
async def handle_logs(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.message:
        return
    role = await _actor_role(callback.from_user.id)
    if not can_view_personnel(role):
        await _deny(callback=callback, text="⛔ Раздел недоступен.")
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5 or not parts[3].isdigit() or not parts[4].isdigit():
        await callback.answer("Некорректная команда", show_alert=True)
        return
    target_tg_id = int(parts[3])
    target = await get_user_by_tg_id(target_tg_id)
    if not target or target.get("role") not in STAFF_ROLES:
        await callback.message.answer("❌ Сотрудник не найден.")
        await callback.answer()
        return
    if parts[4] == "1":
        await push_screen(state, "staff_card", {"target_tg_id": target_tg_id})
        await push_screen(state, "staff_logs", {"target_tg_id": target_tg_id, "page": 1})
    page = max(1, int(parts[4]))
    total = await count_staff_action_logs(target_tg_id)
    per_page = 10
    total_pages = max(1, ceil(total / per_page))
    page = min(page, total_pages)
    logs = await get_staff_action_logs(target_tg_id, limit=per_page, offset=(page - 1) * per_page)
    if not logs:
        await callback.message.answer("📜 Журнал действий пока пустой.")
        await callback.answer()
        return
    lines = ["📜 <b>Журнал действий</b>", ""]
    for row in logs:
        lines.append(f"{format_datetime_in_timezone(row.get('created_at_utc') or row.get('created_at'), row.get('branch_timezone'))} — {html_escape(row.get('human_text') or '-', quote=False)}")
    await callback.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=staff_logs_pagination_kb(target_tg_id, page, total_pages))
    await callback.answer()
