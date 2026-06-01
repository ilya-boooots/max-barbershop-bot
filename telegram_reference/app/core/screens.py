from __future__ import annotations

from typing import Any
from html import escape as html_escape

from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.core.auth import can_manage, has_role, normalize_role
from app.core.nav_constants import NAV_BACK_CALLBACK, NAV_HOME_CALLBACK
from app.core.staff_permissions import can_manage_roles, can_view_personnel
from app.core.navigation import render_main_by_role
from app.core.loyalty_cards import issue_loyalty_code
from app.keyboards.factory import get_main_menu_kb
from app.core.config import get_settings
from app.core.ui_texts import (
    BACK_BTN,
    BOOKING_PHONE_TEXT,
    BUILD_ROUTE_TEXT,
    MAIN_MENU_BTN,
    OUR_ADDRESS_TEXT,
)
from app.keyboards.menu import back_reply_kb, contacts_inline_kb, details_inline_kb
from app.keyboards.loyalty import balance_inline_kb
from app.keyboards.staff import (
    personnel_menu_inline_kb,
    staff_card_kb,
    staff_list_kb,
    staff_logs_pagination_kb,
    staff_remove_confirm_kb,
    staff_remove_list_kb,
)
from app.repositories.staff_action_logs import count_staff_action_logs, get_staff_action_logs
from app.repositories.staff_audit import get_audit_last
from app.repositories.transactions import get_total_purchase_sum, get_total_spent_bonuses
from app.repositories.users import get_user as get_db_user, get_user_by_tg_id, get_users_by_ids, list_staff
from app.db.reports_repo import get_reports_summary
from app.utils.datetime import format_branch_datetime, format_datetime_in_timezone
from app.utils.staff import display_name, format_issuer_name, format_staff_card, role_label, short_name
from app.utils.qr import generate_qr_png_bytes

CALLBACK_OPEN_PRIVACY = "pol:open:privacy"
CALLBACK_OPEN_PERSONAL = "pol:open:personal"
CALLBACK_TOGGLE_PRIVACY = "pol:toggle:privacy"
CALLBACK_TOGGLE_PERSONAL = "pol:toggle:personal"
CALLBACK_REGISTER = "pol:register"

CALLBACK_CONFIRM_NAME_YES = "reg:name:yes"
CALLBACK_CONFIRM_NAME_NO = "reg:name:no"
CALLBACK_GENDER_MALE = "reg:gender:male"
CALLBACK_GENDER_FEMALE = "reg:gender:female"

MAIN_POLICIES_TEXT = "Перед тем, как продолжить, примите политики:"

PRIVACY_POLICY_TEXT = """Политика конфедециальности
1.Общие положения
Настоящая Политика конфедициальности (далее - Политика) определяет порядок сбора, хранения, использования и защиты информации, предоставляемой организации "ЛарсиСтепанна"(далее - Оператор).
Цель настоящей Политики - информировать пользователей о том, как их данные используются и защищаются.
Контактные данные оператора: voyazhkorchma@yandex.ru
2. Состав собираемой информации
Оператор может собирать следующую информацию
*Персональные данные: ФИО, номер телефона, дата рождения;
*Техническая информация: IP-адрес, данные о браузере и устройстве пользователя (собирается автоматически для анализа работы телеграм-бота).
3.Цели сбора информации
Собираемая информация используется в следующих целях:
*Для обеспечения работы программы лояльности;
*Для анализа и улучшения качества предоставляемых услуг;
*Для обеспечения безопасности пользователей.
4.Способы сбора информации
Информация собирается следующими спосабами:
*Непосредственно от пользователя через телеграм-бот;
*Автомотически через технические инструменты, встроенные в телеграм-бот (например, логирование).
5.Обработка и хранение информации 
*Персональные данные обрабатываются строго в рамках целей, указанных в Политике.
*Данные хранятся в базе данных SQL на защищенном сервере с ограниченным доступом.
*Информация не передается третьим лицам, за исключением случаев предусмотренных законодательством РФ.
6.Меры по обеспечению конфедициальности
Оператор принимает необходимые организационные и технические меры для защиты информации пользователей, включая:
*Ограничение доступа к базе данных;
*Данные, передаваемые через телеграм-бот, шифруются с использованием встроеных механизмов Telegram;
*Регулярное обновление систем безопасности;
*Ведение резервного копирования данных.
7.Права пользователей
Пользователи имеют право:
*Запросить информацию о своих данных, хранящихся у Оператора;
*Требовать их исправления или удаления;
*Ограничить или отозвать согласие на обработку своих данных;
*Направить запросы по вопросам конфедициальности на почту: voyazhkorchma@yandex.ru
8.Использование файлов cookie
для анализа работы телеграм-бота и улучшения пользовательского опыта Оператор не использует cookie-файлы, так как их функционал не применим в рамках работы телеграм-бота.
9.Изменения в политике конфедециальности 
Оператор оставляет за собой право вносить изменения в настоящую Политику. Пользователя уведомляются о значительных изменениях через доступные каналы связи.
10.Контактная информация
По всем вопросам, связанным с Политикой конфедециальности , вы можете обратится на email : voyazhkorchma@yandex.ru
"""

PERSONAL_DATA_POLICY_TEXT = """Политика обработки персональных данных
1 Общие положения
Настоящая Политика обработки персональных данных (далее — Политика) разработана в
соответствии с Федеральным законом №152-ФЗ «О персональных данных» и другими
нормативными актами Российской Федерации в области защиты персональных данных.
Цель настоящей Политики — информировать пользователей о порядке сбора, хранения,
обработки и защиты их персональных данных ООО "Вояж" (далее — Оператор).
Контактные данные Оператора: voyazhkorchma@yandex.ru
2. Персональные данные, которые обрабатываются
В рамках  программы лояльности Оператор ообрабатысуществляет обработку следующих персональных данных пользователей:
*ФИО;
*Номер телефона;
*Дата рождения.
3 Цели обработки персональных данных
Персональные данные обрабатываются исключительно в следующих целях:
*Для участия пользователей в программе лояльности;
*Для предоставления бонусов, скидок и других привилегий в рамках программы.
4 Способы сбора персональных данных
Сбор персональных данных осуществляется с использованием телеграм-бота, созданного для
взаимодействия с пользователями программы лояльности.
5 Условия обработки и передачи данных
*Персональные данные не передаются третьим лицам.
*Оператор принимает все необходимые меры для обеспечения конфиденциальности и
защиты персональных данных.
6 Сроки хранения данных
Персональные данные хранятся до момента достижения целей обработки или отзыва согласия
пользователем.
7 Меры по защите персональных данных
Для защиты персональных данных применяются следующие меры:
*Данные хранятся в базе данных SQL на защищённом сервере с ограниченным доступом;
*Используются пароли для доступа к серверу и базе данных;
*Регулярно проводятся резервные копирования данных;
*Применяются обновления системы безопасности для предотвращения
несанкционированного доступа;
*Данные, передаваемые через телеграм-бот, шифруются с использованием встроенных
механизмов Telegram.
8 Права субъектов персональных данных
Субъекты персональных данных имеют право:
*Получить информацию о своих персональных данных, обрабатываемых Оператором;
*Требовать уточнения, блокировки или уничтожения своих данных;
*Отозвать своё согласие на обработку данных в любой момент, отправив запрос на
электронный адрес: voyazhkorchma@yandex.ru.
9 Изменения в Политике
Оператор оставляет за собой право вносить изменения в настоящую Политику. Актуальная версия
Политики размещается на официальных ресурсах Оператора. Пользователи уведомляются о
значительных изменениях через доступные каналы связи.
10 Контактная информация
По всем вопросам, связанным с обработкой персональных данных, вы можете обратиться по
адресу электронной почты: voyazhkorchma@yandex.ru
"""

_acceptance_state: dict[int, dict[str, bool]] = {}


def get_policy_state(user_id: int) -> dict[str, bool]:
    state = _acceptance_state.get(user_id)
    if state is None:
        state = {"privacy": False, "personal": False}
        _acceptance_state[user_id] = state
    return state


def reset_policy_state(user_id: int) -> None:
    _acceptance_state[user_id] = {"privacy": False, "personal": False}


def build_policies_keyboard(user_id: int) -> InlineKeyboardMarkup:
    state = get_policy_state(user_id)
    privacy_label = (
        "✅ Принять политику конфиденциальности"
        if state["privacy"]
        else "⬜ Принять политику конфиденциальности"
    )
    personal_label = (
        "✅ Принять политику обработки персональных данных"
        if state["personal"]
        else "⬜ Принять политику обработки персональных данных"
    )
    keyboard = [
        [InlineKeyboardButton(text="🔐 Политика конфиденциальности", callback_data=CALLBACK_OPEN_PRIVACY)],
        [InlineKeyboardButton(text="🔐 Политика обработки персональных данных", callback_data=CALLBACK_OPEN_PERSONAL)],
        [InlineKeyboardButton(text=privacy_label, callback_data=CALLBACK_TOGGLE_PRIVACY)],
        [InlineKeyboardButton(text=personal_label, callback_data=CALLBACK_TOGGLE_PERSONAL)],
    ]
    if state["privacy"] and state["personal"]:
        keyboard.append([InlineKeyboardButton(text="Перейти к регистрации.", callback_data=CALLBACK_REGISTER)])
    keyboard.append([InlineKeyboardButton(text=MAIN_MENU_BTN, callback_data=NAV_HOME_CALLBACK)])
    keyboard.append([InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_name_confirmation_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=CALLBACK_CONFIRM_NAME_YES)],
            [InlineKeyboardButton(text="❌ Нет", callback_data=CALLBACK_CONFIRM_NAME_NO)],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
        ]
    )


def build_gender_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👨 Мужской", callback_data=CALLBACK_GENDER_MALE)],
            [InlineKeyboardButton(text="👩 Женский", callback_data=CALLBACK_GENDER_FEMALE)],
            [InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)],
        ]
    )


def build_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Поделиться контактом", request_contact=True)],
            [KeyboardButton(text=BACK_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def render_main_menu(message: Message, user_role: str | None = None) -> None:
    del user_role
    await render_main_by_role(message, message.from_user.id)


async def render_policies_menu(message: Message) -> None:
    await message.answer(MAIN_POLICIES_TEXT, reply_markup=build_policies_keyboard(message.from_user.id))


async def render_policy_privacy(message: Message) -> None:
    await message.answer(PRIVACY_POLICY_TEXT, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)]]
    ))


async def render_policy_personal(message: Message) -> None:
    await message.answer(PERSONAL_DATA_POLICY_TEXT, reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BACK_BTN, callback_data=NAV_BACK_CALLBACK)]]
    ))


async def render_contact_request(message: Message) -> None:
    await message.answer(
        '📞 Для продолжения работы необходим ваш контакт. Нажмите на кнопку "📞 Поделиться контактом".',
        reply_markup=build_contact_keyboard(),
    )


async def render_registration_confirm_name(message: Message, payload: dict[str, Any] | None = None) -> None:
    name = payload.get("name") if payload else None
    if not name:
        name = message.from_user.first_name if message.from_user else None
    await message.answer(
        f"👉 Хотите использовать имя {name or 'Пользователь'} для регистрации в системе?",
        reply_markup=build_name_confirmation_kb(),
    )


async def render_registration_enter_name(message: Message) -> None:
    await message.answer("✍️ Введите ваше имя:", reply_markup=back_reply_kb())


async def render_registration_enter_birth_date(message: Message) -> None:
    await message.answer(
        "📅 Введите дату рождения в формате дд.мм.гггг (Например: 31.01.1999).",
        reply_markup=back_reply_kb(),
    )


async def render_registration_choose_gender(message: Message) -> None:
    await message.answer("👥 Выберите пол.", reply_markup=build_gender_kb())


async def render_personnel_menu_for_user(message: Message, user_id: int) -> None:
    user = await get_db_user(user_id)
    role = normalize_role(user_id, user["role"] if user else None)
    if not can_view_personnel(role):
        await message.answer("⛔️ Недостаточно прав.")
        return
    can_manage_any = can_manage_roles(role)
    await message.answer(
        "👥 Управление персоналом\n\nВыберите действие:",
        reply_markup=personnel_menu_inline_kb(can_manage_any),
    )


async def render_personnel_menu(message: Message) -> None:
    await render_personnel_menu_for_user(message, message.from_user.id)


async def render_personnel_waiting_id(message: Message) -> None:
    await message.answer(
        "📌 Инструкция:\n"
        "1️⃣ Перейдите в @userinfobot  \n"
        "2️⃣ Отправьте ему контакт сотрудника  \n"
        "3️⃣ Скопируйте Telegram ID (только цифры)  \n"
        "4️⃣ Вставьте этот ID сюда",
        reply_markup=back_reply_kb(),
    )
    await message.answer("Введите Telegram ID пользователя (только цифры):", reply_markup=back_reply_kb())


async def render_staff_card(message: Message, payload: dict[str, Any]) -> None:
    target_tg_id = payload.get("target_tg_id")
    if not isinstance(target_tg_id, int):
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    target_user = await get_db_user(target_tg_id)
    if not target_user:
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    assigned_by_id = target_user.get("role_assigned_by_tg_id")
    if assigned_by_id:
        assigned_by = await get_users_by_ids([assigned_by_id])
        assigned_by_user = assigned_by.get(assigned_by_id)
        if assigned_by_user:
            target_user["assigned_by_name"] = assigned_by_user.get("name")
            target_user["assigned_by_display_name"] = assigned_by_user.get("display_name")
    viewer = await get_db_user(message.from_user.id)
    viewer_role = normalize_role(message.from_user.id, viewer["role"] if viewer else None)
    can_manage_target = can_manage(
        message.from_user.id,
        viewer_role,
        target_tg_id,
        target_user.get("role"),
    )
    await message.answer(
        format_staff_card(target_user),
        reply_markup=staff_card_kb(can_manage_target, target_tg_id),
        parse_mode="HTML",
    )


async def render_staff_list(message: Message) -> None:
    staff = await list_staff()
    lines = ["👥 Персонал ресторана", f"Всего: {len(staff)}", ""]
    buttons: list[tuple[int, str]] = []
    for idx, member in enumerate(staff, start=1):
        name = display_name(member)
        member_role = member.get("role")
        lines.append(
            f"{idx}) {role_label(member_role)}\n"
            f"👤 {name}\n"
            f"📅 С {await format_branch_datetime(member.get('role_assigned_at'))}\n"
        )
        buttons.append((int(member["user_id"]), f"{short_name(name)} ({role_label(member_role)})"))
    await message.answer("\n".join(lines), reply_markup=staff_list_kb(buttons))


async def render_staff_role_history(message: Message, payload: dict[str, Any]) -> None:
    target_tg_id = payload.get("target_tg_id")
    if not isinstance(target_tg_id, int):
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    history = await get_audit_last(target_tg_id, limit=20)
    if not history:
        await message.answer("📚 История ролей пока пустая.", reply_markup=back_reply_kb())
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
        lines.append(f"{await format_branch_datetime(row.get('changed_at'))} — {action}. Выполнил: {actor_name}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=back_reply_kb())


async def render_staff_logs(message: Message, payload: dict[str, Any]) -> None:
    target_tg_id = payload.get("target_tg_id")
    page = payload.get("page", 1)
    if not isinstance(target_tg_id, int):
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    if not isinstance(page, int):
        page = 1
    page = max(1, page)
    total = await count_staff_action_logs(target_tg_id)
    per_page = 10
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    logs = await get_staff_action_logs(target_tg_id, limit=per_page, offset=(page - 1) * per_page)
    if not logs:
        await message.answer("📜 Журнал действий пока пустой.", reply_markup=back_reply_kb())
        return
    lines = ["📜 <b>Журнал действий</b>", ""]
    for row in logs:
        lines.append(f"{format_datetime_in_timezone(row.get('created_at_utc') or row.get('created_at'), row.get('branch_timezone'))} — {html_escape(row.get('human_text') or '-', quote=False)}")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=staff_logs_pagination_kb(target_tg_id, page, total_pages))


async def render_staff_remove_list(message: Message) -> None:
    staff = await list_staff()
    items = [(int(row["user_id"]), f"{short_name(display_name(row))} ({role_label(row.get('role'))})") for row in staff]
    await message.answer("➖ Выберите сотрудника, у которого нужно снять роль:", reply_markup=staff_remove_list_kb(items))


async def render_staff_remove_confirm(message: Message, payload: dict[str, Any]) -> None:
    target_tg_id = payload.get("target_tg_id")
    if not isinstance(target_tg_id, int):
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    target = await get_user_by_tg_id(target_tg_id)
    if not target:
        await message.answer("❌ Сотрудник не найден.", reply_markup=back_reply_kb())
        return
    await message.answer(format_staff_card(target), reply_markup=staff_remove_confirm_kb(target_tg_id, True), parse_mode="HTML")


async def render_contacts(message: Message | CallbackQuery) -> None:
    text = "\n".join(
        [
            "📍 Контакты",
            f"{OUR_ADDRESS_TEXT}: {get_settings().contact_address}",
            f"{BOOKING_PHONE_TEXT}: {get_settings().booking_phone}",
            f"{BUILD_ROUTE_TEXT}:",
        ]
    )
    if isinstance(message, CallbackQuery):
        if message.message:
            await message.message.edit_text(text, reply_markup=contacts_inline_kb(address=get_settings().contact_address))
        return
    await message.answer(text, reply_markup=contacts_inline_kb(address=get_settings().contact_address))

async def render_staff_operations(message: Message) -> None:
    await message.answer("Раздел операций появится позже.", reply_markup=back_reply_kb())


async def render_staff_scan_qr(message: Message) -> None:
    await message.answer(
        "📷 Отправьте фото QR-кода карты лояльности\n"
        "или введите:\n"
        "• код карты (например 467-400)\n"
        "• или последние 4 цифры телефона",
        reply_markup=back_reply_kb(),
    )


async def render_staff_reports(message: Message | CallbackQuery) -> None:
    from app.keyboards.reports import reports_menu_kb

    summary = await get_reports_summary()
    text = (
        "📊 Отчёты\n"
        f"Всего клиентов: {summary['total']}\n"
        f"Активных за 7 дней: {summary['active_7']}\n"
        f"Активных за 30 дней: {summary['active_30']}"
    )
    if isinstance(message, CallbackQuery):
        if message.message:
            await message.message.edit_text(text, reply_markup=reports_menu_kb())
        return
    await message.answer(text, reply_markup=reports_menu_kb())

async def render_account_info(message: Message) -> None:
    user = await get_db_user(message.from_user.id)
    available_bonuses = int(user["bonus_balance"]) if user else 0
    total_spent_bonuses = await get_total_spent_bonuses(message.from_user.id)
    total_purchase_sum = await get_total_purchase_sum(message.from_user.id)
    top_rank_threshold = 30001
    base_rank_threshold = 30000
    is_top_rank = total_purchase_sum >= top_rank_threshold
    rank_name = "Почётный гость" if is_top_rank else "Путешественник"
    progress_percent = (
        100
        if is_top_rank
        else min(int((total_purchase_sum / base_rank_threshold) * 100), 100)
    )

    def _format_number(value: int) -> str:
        return f"{value:,}".replace(",", " ")

    status_lines = [
        "🍽 Ресторан «Лариса Степанна»",
        "Ваша программа лояльности",
        "",
        f"🎖 Ваш уровень: {rank_name} ({progress_percent}%)",
        f"💰 Доступно бонусов: {_format_number(available_bonuses)}",
        f"📉 Потрачено бонусов: {_format_number(total_spent_bonuses)}",
    ]

    if not is_top_rank:
        status_lines.extend(
            [
                "",
                "🚀 До уровня «Почётный гость» осталось совсем немного!",
                "",
                "После перехода вы будете получать:",
                "• 10% кешбэка с каждого заказа",
                "• больше бонусов за любимые блюда 💛",
            ]
        )

    status_lines.extend(
        [
            "",
            "✨ Как работает программа:",
            "",
            "• Вы копите бонусы за заказы в ресторане «Лариса Степанна»",
            "• 1 бонус = 1 рубль",
            "• Бонусами можно оплатить до 30% счёта",
            "• Бонусы начисляются автоматически",
            "",
            "🏆 Уровни лояльности:",
            "",
            "🌍 Путешественник",
            "— 7% кешбэка",
            "— сумма покупок до 30 000 ₽",
            "",
            "👑 Почётный гость",
            "— 10% кешбэка",
            "— сумма покупок от 30 001 ₽",
            "",
            "🎂 День рождения",
            "",
            "Мы дарим скидку 20% 🎁",
            "• действует за 7 дней до и 7 дней после дня рождения",
            "• можно воспользоваться 1 раз",
            "• понадобится документ, подтверждающий возраст 18+",
            "",
            "ℹ️ Важно знать:",
            "",
            "• Бонусы не начисляются на бизнес-ланчи",
            "• Если не было визитов 180 дней — бонусы сгорают 🔥",
            "• При этом уровень сбрасывается до 10%",
        ]
    )

    await message.answer("\n".join(status_lines), reply_markup=details_inline_kb())


async def render_balance(message: Message) -> None:
    user = await get_db_user(message.from_user.id)
    balance = user["loyalty_balance"] if user else 0
    await message.answer(
        "💳 Баланс счёта\n"
        f"💰 Текущий баланс: {balance}",
        reply_markup=balance_inline_kb(),
    )


async def render_virtual_card(message: Message) -> None:
    await message.answer("Виртуальная карта формируется, подождите немного. ⏳")
    user = await get_db_user(message.from_user.id)
    code = await issue_loyalty_code(
        message.from_user.id,
        user.get("card_number") if user else None,
    )
    await message.answer(
        "⏱ Код будет действителен 10 минут.\n\n"
        "📸 Этот QR-код вы можете предъявить вместо пластиковой карты лояльности. "
        "Если нет возможности сканировать — продиктуйте цифры (вместе с дефисом, он важен).",
        reply_markup=back_reply_kb(),
    )
    qr_bytes = generate_qr_png_bytes(code)
    qr_file = BufferedInputFile(qr_bytes, filename="loyalty-card.png")
    await message.answer_photo(qr_file)
    await message.answer(f"🔢 Код: {code}", reply_markup=back_reply_kb())


async def render_digital_menu(message: Message) -> None:
    await message.answer("Электронное меню будет здесь.", reply_markup=back_reply_kb())


async def render_review_prompt(message: Message) -> None:
    await message.answer("Напишите ваш отзыв сообщением в чат.", reply_markup=back_reply_kb())


async def render_dev_diagnostics(message: Message) -> None:
    from app.handlers.dev import _diag_menu_kb

    await message.answer(
        "🛠️ Разработка: Диагностика\nВыберите действие:",
        reply_markup=_diag_menu_kb(),
    )




SCREEN_RENDERERS: dict[str, Any] = {
    "main_menu": render_main_menu,
    "policies_menu": render_policies_menu,
    "policy_privacy": render_policy_privacy,
    "policy_personal": render_policy_personal,
    "contact_request": render_contact_request,
    "registration_confirm_name": render_registration_confirm_name,
    "registration_enter_name": render_registration_enter_name,
    "registration_enter_birth_date": render_registration_enter_birth_date,
    "registration_choose_gender": render_registration_choose_gender,
    "personnel_menu": render_personnel_menu,
    "personnel_waiting_id": render_personnel_waiting_id,
    "staff_card": render_staff_card,
    "staff_list": render_staff_list,
    "staff_role_history": render_staff_role_history,
    "staff_logs": render_staff_logs,
    "staff_remove_list": render_staff_remove_list,
    "staff_remove_confirm": render_staff_remove_confirm,
    "staff_operations": render_staff_operations,
    "staff_scan_qr": render_staff_scan_qr,
    "staff_reports": render_staff_reports,
    "account_info": render_account_info,
    "balance": render_balance,
    "virtual_card": render_virtual_card,
    "digital_menu": render_digital_menu,
    "contacts": render_contacts,
    "review_prompt": render_review_prompt,
    "dev_diagnostics": render_dev_diagnostics,
}


async def render_screen(message: Message, screen: str, payload: dict[str, Any] | None = None) -> None:
    renderer = SCREEN_RENDERERS.get(screen)
    if not renderer:
        await render_main_menu(message)
        return
    if payload is None:
        payload = {}
    if renderer in {render_registration_confirm_name, render_staff_card, render_staff_role_history, render_staff_logs, render_staff_remove_confirm}:
        await renderer(message, payload)
        return
    await renderer(message)
