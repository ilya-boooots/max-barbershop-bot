"""Russian UI texts for the MAX barbershop bot."""

from __future__ import annotations

START_GREETING_TEXT = """Привет! 👋 Это MAX-версия бота барбершопа.

Скоро здесь появится запись, мои визиты, уведомления и связь с администратором."""
MAIN_MENU_TEXT = """Главное меню 🏠

Выберите нужный раздел:"""
SECTION_SOON_TEXT = "Раздел скоро появится 🔧"

SUPPORT_TEXT = """🆘 Поддержка

Если у вас возник вопрос, напишите администратору: {support_username}"""
UNKNOWN_TEXT = """Я пока не знаю такую команду 🤔

Нажмите /start, чтобы открыть главное меню."""

REGISTRATION_WELCOME_TEXT = """Добро пожаловать! 👋

Чтобы записываться в барбершоп и получать напоминания о визитах, нужно пройти короткую регистрацию.

Мы сохраним только данные, которые нужны для записи: имя и телефон."""
REGISTRATION_DECLINED_TEXT = """Без согласия мы не сможем продолжить регистрацию 🙏

Если передумаете, нажмите /start."""
REGISTRATION_PHONE_TEXT = """Отправьте ваш номер телефона 📱

Можно нажать кнопку ниже или ввести номер вручную в формате +79991234567."""
REGISTRATION_PHONE_INVALID_TEXT = "Не получилось распознать номер 😕  \nВведите телефон в формате +79991234567."
REGISTRATION_CONTACT_PHONE_MISSING_TEXT = """Не получилось получить номер телефона 😕

Введите телефон вручную в формате +79991234567."""
REGISTRATION_NAME_TEXT = "Как вас зовут? 🙂"
REGISTRATION_NAME_INVALID_TEXT = "Введите имя текстом, минимум 2 символа 🙂"
REGISTRATION_COMPLETE_TEXT = "Готово! Вы зарегистрированы ✅"
REGISTRATION_REQUIRED_TEXT = "Сначала нужно завершить регистрацию 🙏"

BOOKING_CATEGORY_TEXT = """✂️ Запись

Выберите категорию услуг:"""
BOOKING_SERVICE_TEXT = """✂️ Запись

Выберите услугу:"""
BOOKING_EMPTY_TEXT = """Сейчас нет доступных услуг для записи 🙏

Пожалуйста, попробуйте позже."""
BOOKING_CATEGORY_EMPTY_TEXT = "😔 Пока нет доступных категорий услуг."
BOOKING_MASTER_TEXT = "Выберите мастера 💈"
BOOKING_MASTERS_EMPTY_TEXT = """😔 Для этой услуги пока нет мастеров со свободными окнами.
Попробуйте выбрать другую услугу или загляните позже."""
BOOKING_MASTER_SELECTED_TEXT = """Вы выбрали мастера: {master_name}

Следующий шаг — выбор даты и времени. Скоро добавим 🔧"""


STAFF_MENU_TEXT = """👥 Персонал

Выберите действие:"""
STAFF_LIST_EMPTY_TEXT = "Сотрудники пока не добавлены 👥"
STAFF_ASSIGN_IDENTIFIER_TEXT = "Введите MAX user_id или телефон пользователя, которому нужно выдать роль 👇"
STAFF_REMOVE_IDENTIFIER_TEXT = "Введите MAX user_id или телефон пользователя, у которого нужно снять роль 👇"
STAFF_USER_NOT_FOUND_TEXT = """Пользователь не найден 😕

Сначала он должен открыть бота и пройти регистрацию."""
STAFF_ASSIGN_ROLE_TEXT = "Какую роль выдать пользователю?"
STAFF_NO_EXTRA_ROLES_TEXT = "У пользователя нет дополнительных ролей 👥"
STAFF_ROLE_ASSIGNED_TEXT = "Роль успешно выдана ✅"
STAFF_ROLE_REMOVED_TEXT = "Роль успешно снята ✅"
STAFF_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
