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

BOOKING_CATEGORY_TEXT = "✂️ Выберите категорию услуг 😊"
BOOKING_SERVICE_TEXT = "Выберите услугу ✂️"
BOOKING_EMPTY_TEXT = """Сейчас нет доступных услуг для записи 🙏

Пожалуйста, попробуйте позже."""
BOOKING_CATEGORY_EMPTY_TEXT = "😔 Пока нет доступных категорий услуг."
BOOKING_MASTER_TEXT = "Выберите мастера 💈"
BOOKING_MASTERS_EMPTY_TEXT = """😔 Для этой услуги пока нет мастеров со свободными окнами.
Попробуйте выбрать другую услугу или загляните позже."""
BOOKING_MASTER_SELECTED_TEXT = """Вы выбрали мастера: {master_name}

Следующий шаг — выбор даты и времени. Скоро добавим 🔧"""
BOOKING_DATES_TEXT = """✂️ Запись

Выберите дату:"""
BOOKING_SLOTS_TEXT = """✂️ Запись

Выберите свободное время:"""
BOOKING_SLOTS_EMPTY_TEXT = """На эту дату свободного времени нет 🙏

Выберите другой день."""
BOOKING_CONFIRMATION_MISSING_DATA_TEXT = """Не хватает данных для записи 🙏

Нажмите /start и пройдите регистрацию заново."""
BOOKING_PHONE_TEXT = """📱 Чтобы записать вас, отправьте номер телефона 😊

Можно использовать номер из регистрации:
{registered_phone}"""
BOOKING_PHONE_WITHOUT_REGISTERED_TEXT = "📱 Чтобы записать вас, отправьте номер телефона 😊"
BOOKING_PHONE_INVALID_TEXT = "😔 Номер выглядит неверно. Отправьте телефон в формате +79991234567 🙂"
BOOKING_CONTACT_PHONE_MISSING_TEXT = "😔 Номер выглядит неверно. Отправьте телефон в формате +79991234567 🙂"
BOOKING_REGISTERED_PHONE_MISSING_TEXT = "📱 Чтобы записать вас, отправьте номер телефона 😊"
BOOKING_CREATE_ERROR_TEXT = """Не удалось создать запись 🙏

Возможно, это время уже заняли. Попробуйте выбрать другой слот."""
BOOKING_CREATE_IN_PROGRESS_TEXT = "Запись уже создаётся, подождите немного ⏳"


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

BROADCAST_MENU_TEXT = """📣 Рассылка

Выберите действие:"""
BROADCAST_TEXT_INPUT_TEXT = "Введите текст рассылки 👇"
BROADCAST_EMPTY_TEXT = "Текст рассылки не может быть пустым 🙏"
BROADCAST_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
BROADCAST_NO_RECIPIENTS_TEXT = "Нет получателей для рассылки 🙏"
BROADCAST_SENDING_TEXT = "Рассылка отправляется, подождите немного ⏳"
BROADCAST_ALREADY_SENDING_TEXT = "Рассылка уже отправляется, подождите немного ⏳"

STATISTICS_MENU_TEXT = """📊 Статистика

Выберите период:"""
STATISTICS_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
STATISTICS_NOT_CONFIGURED_TEXT = """YClients пока не настроен 🙏

Сначала добавьте данные подключения."""
STATISTICS_LOAD_ERROR_TEXT = """Не удалось загрузить статистику 🙏

Пожалуйста, попробуйте позже."""

CLIENT_SEGMENTS_MENU_TEXT = """🎯 Сегменты клиентов

Выберите сегмент:"""
CLIENT_SEGMENTS_EMPTY_TEXT = "В этом сегменте пока нет клиентов 🙏"
CLIENT_SEGMENTS_LOAD_ERROR_TEXT = """Не удалось загрузить сегмент клиентов 🙏

Пожалуйста, попробуйте позже."""
CLIENT_SEGMENTS_BROADCAST_LIMIT_TEXT = "Для рассылки доступны только клиенты, которые уже открывали MAX-бота и прошли регистрацию."
LOST_CLIENTS_LOAD_ERROR_TEXT = """Не удалось загрузить потерянных клиентов 🙏

Пожалуйста, попробуйте позже."""
LOST_CLIENTS_BROADCAST_LIMIT_TEXT = "Для рассылки доступны только клиенты, которые уже открывали MAX-бота и прошли регистрацию."
LOST_CLIENTS_ZERO_RECIPIENTS_TEXT = """Нет получателей для рассылки в MAX 🙏

Клиенты есть в YClients, но они ещё не открывали MAX-бота."""


YCLIENTS_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
YCLIENTS_NOT_CONFIGURED_TEXT = """YClients пока не настроен 🙏

Сначала добавьте данные подключения."""
YCLIENTS_SETTINGS_SAVED_TEXT = "Настройки YClients сохранены ✅"
YCLIENTS_CHECK_SUCCESS_TEXT = """Подключение к YClients работает ✅

Филиал: {branch_title_or_company_id}"""
YCLIENTS_CHECK_FAILURE_TEXT = """Не удалось подключиться к YClients 🙏

Проверьте company_id и токены."""
YCLIENTS_COMPANY_ID_TEXT = "Введите company_id филиала YClients 👇"
YCLIENTS_PARTNER_TOKEN_TEXT = "Введите partner_token YClients 👇"
YCLIENTS_USER_TOKEN_TEXT = "Введите user_token YClients 👇"
YCLIENTS_TIMEZONE_TEXT = "Введите часовой пояс филиала, например Europe/Moscow 👇"
YCLIENTS_BRANCH_TITLE_TEXT = "Введите название филиала 👇"
YCLIENTS_INVALID_REQUIRED_TEXT = """Поле не может быть пустым 🙏

Введите значение ещё раз."""
YCLIENTS_INVALID_TIMEZONE_TEXT = """Не удалось распознать часовой пояс 😕

Введите, например: Europe/Moscow"""
YCLIENTS_CONFIRM_TEXT = """Проверьте настройки YClients 🧩

Company ID: {company_id}
Филиал: {branch_title}
Часовой пояс: {branch_timezone}

Токены будут сохранены в скрытом виде 🔐"""

SETTINGS_MENU_TEXT = """⚙️ Настройки

Выберите раздел:"""
SETTINGS_NO_ACCESS_TEXT = "У вас нет доступа к этому разделу 🙏"
SETTINGS_CONTACTS_EDIT_SOON_TEXT = "Редактирование контактов добавим отдельным шагом 🔧"
SETTINGS_NOTIFICATIONS_EDIT_SOON_TEXT = "Управление уведомлениями добавим отдельным шагом 🔧"
