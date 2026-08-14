# Приоритетный manual QA checklist для MAX-бота

Цель: дать владельцу понятный маршрут ручной приёмки без запуска live MAX/YClients из разработки. Чеклист проверяет текущую MAX-реализацию: кнопки, экраны, ввод текста, роли, YClients-флоу, уведомления и устойчивость к ошибкам.

## Как пользоваться

- Тестировать на безопасном тестовом филиале YClients и тестовых MAX-пользователях.
- Для T0 использовать реальные валидные тестовые услуги/мастеров/слоты, чтобы пройти запись до конца.
- После каждого критичного бага сверять, не нарушены ли кнопки `⬅️ Назад` и `🏠 Главное меню`.
- Если срабатывает одно из правил остановки — прекратить прогон и сразу заводить баг.
- В нормальных пользовательских кнопках не должно быть всплывающих popup/toast, если это не отдельная ошибка/ограничение.

## Тестовые данные перед прогоном

| Данные | Что подготовить |
| --- | --- |
| MAX-пользователи | Обычный клиент, менеджер, админ, protected developer. |
| YClients | Тестовый филиал, partner token, user token, timezone, название филиала. |
| Услуги | Минимум 2 активные услуги в разных категориях, одна услуга с доступными мастерами. |
| Мастера | Минимум 2 активных мастера, один с доступными слотами, один без слотов/с ограничениями. |
| Слоты | Сегодня/завтра и ближайшие дни, минимум один свободный слот. |
| Записи | Будущая запись для отмены, будущая запись для переноса, завершённая запись для повтора/feedback. |
| Контакты | Адрес, телефон, режим работы, ссылки Яндекс/2GIS/Google. |
| Поддержка | Валидный username поддержки и текст описания. |
| Уведомления | Пользователь с включёнными уведомлениями, пользователь blocked/stopped или симуляция такого результата. |

## T0 — Critical / must work before demo

| Priority | Section | Scenario | Steps to test manually | Expected result | Watch for | Test data | Notes / related file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T0 | Start | Первый вход `/start` | Новый клиент → отправить `/start` | Показан старт/регистрация или главное меню без ошибки | пустой ответ, generic error, неверный экран | Новый MAX-пользователь | `flows/start.py`, `flows/registration.py` |
| T0 | Start | Повторный `/start` | Зарегистрированный клиент → `/start` | Открывается роль-aware главное меню | сброс регистрации, потеря роли | Зарегистрированный клиент | `flows/start.py`, `ui/screens.py` |
| T0 | Registration | Согласия с политиками | `/start` → открыть обе политики → отметить оба согласия → продолжить | Нельзя продолжить без обоих согласий; после принятия переход к имени | кнопка продолжает без согласий, зацикливание | Новый клиент | `flows/registration.py` |
| T0 | Registration | Подтверждение имени из MAX | На шаге имени выбрать «Да» | Имя сохраняется, следующий шаг корректный | имя не сохраняется, пустое имя | MAX-профиль с display name | `services/registration.py` |
| T0 | Registration | Ручной ввод имени | На шаге имени выбрать «Нет» → ввести имя | Бот принимает имя и переходит дальше | unknown fallback, обрезка текста, техническая ошибка | Имя кириллицей | `flows/registration.py`, `core/router.py` |
| T0 | Registration | Дата рождения валидная/пустая | Ввести валидную дату; отдельно проверить пропуск/пустой ввод, если доступен | Валидная дата сохраняется, некорректная просит повторить | неверный формат принимается, зависание | `01.01.1990` | `flows/registration.py` |
| T0 | Registration | Телефон через контакт | Нажать кнопку отправки контакта | Телефон распознан, регистрация завершается | контакт попадает в unknown fallback, chat_id теряется | Реальный тестовый контакт | `core/router.py`, `services/registration.py` |
| T0 | Registration | Телефон текстом | Ввести валидный телефон текстом | Телефон нормализуется, регистрация завершается | принимает мусор, неверная маска | `+79991234567` | `flows/registration.py` |
| T0 | Menu | Главное меню клиента | Открыть меню обычным пользователем | Видны только клиентские кнопки: запись, мои записи, контакты, поддержка | админские кнопки у клиента | Обычный клиент | `ui/buttons.py`, `core/permissions.py` |
| T0 | Menu | Главное меню ролей | Открыть меню менеджером/админом/developer | Видимость кнопок соответствует роли | менеджер/админ видит лишнее или не видит нужное | 3 тестовые роли | `core/permissions.py` |
| T0 | YClients | Экран YClients | Роль с доступом → Главное меню → YClients | Видны настройка, проверка подключения, навигация | доступ у клиента, generic error | Manager/developer | `flows/yclients_settings.py` |
| T0 | YClients | Настройка валидных данных | YClients → настроить → company id → partner token → user token → timezone → branch title → сохранить | Настройки сохраняются, токены не показываются целиком | токены видны в UI/логах, шаги путаются | Тестовые YClients credentials | `flows/yclients_settings.py`, `services/yclients_settings.py` |
| T0 | YClients | Health check | YClients → Проверить подключение | Показан понятный статус филиала/ошибки | raw exception, зависание, неверная маскировка | Активные настройки | `flows/yclients_settings.py` |
| T0 | Booking | Вход в запись | Главное меню → ✨ Записаться | Появляется hub выбора сценария: услуга/специалист/дата-время | пустой список, нет Back/Home | Активный YClients | `flows/booking.py` |
| T0 | Booking | Service-first booking | Записаться → выбрать услугу/категорию → мастера → дату → слот → телефон → подтвердить | Запись создана в YClients, показана success card | duplicate booking, неверный мастер/услуга/время | Валидная услуга/мастер/слот | `flows/booking.py`, `services/booking.py` |
| T0 | Booking | Specialist-first booking | Записаться → выбрать специалиста → услугу → дату → слот → телефон → подтвердить | Запись создана на выбранного мастера | услуга не относится к мастеру, пропал мастер | Мастер с услугой | `flows/booking.py` |
| T0 | Booking | Date/time-first booking | Записаться → выбрать дату/время → услугу/мастера по доступности → подтвердить | Доступны только валидные комбинации | показываются недоступные услуги/мастера | Свободный слот | `flows/booking.py` |
| T0 | Booking | Валидность дат | В записи пролистать даты, выбрать ближайшие даты | Нет прошедших дат, даты соответствуют timezone филиала | вчерашние даты, UTC-сдвиг | Timezone филиала | `services/company_time.py`, `services/booking.py` |
| T0 | Booking | Валидность слотов | Выбрать дату с ограниченными слотами | Показаны только реально доступные слоты | занятый слот показан доступным | Дата с одним свободным слотом | `services/booking.py` |
| T0 | Booking | Телефон записи: зарегистрированный | На шаге телефона нажать «использовать зарегистрированный» | Переход к подтверждению без повторного ввода | кнопка не работает, берёт чужой телефон | Клиент с телефоном | `flows/booking.py` |
| T0 | Booking | Телефон записи: новый | На шаге телефона ввести другой валидный номер | Номер принят для записи | неверная валидация, unknown fallback | `+79990000001` | `flows/booking.py` |
| T0 | Booking | Confirmation card | На экране подтверждения проверить услугу, мастера, дату, время, телефон | Данные совпадают с выбранными | raw IDs, неверная цена/длительность | Выбранный набор | `ui/texts.py`, `services/booking.py` |
| T0 | Booking | Double tap confirm | На подтверждении быстро нажать «Подтвердить» 2–3 раза | Создаётся только одна запись, второй тап блокируется/игнорируется | дубль в YClients | Свободный слот | `core/action_locks.py`, `flows/booking.py` |
| T0 | Booking | Cancel draft | В процессе записи нажать отмену черновика | Возврат в меню/хаб без создания записи | запись всё равно создана | Незавершённая запись | `flows/booking.py` |
| T0 | My bookings | Список будущих записей | Главное меню → 📅 Мои записи | Список будущих записей, понятные карточки | пусто при наличии записей, прошлые записи | Клиент с future booking | `flows/my_bookings.py` |
| T0 | My bookings | Детали записи | Открыть запись из списка | Детали: услуга, мастер, дата/время, адрес, статус | raw record id в клиентском UI | Future booking | `services/my_bookings.py` |
| T0 | My bookings | Cancel | Детали → отменить → подтвердить | Запись отменена в YClients, экран успеха | отмена не дошла, нет подтверждения | Future booking для отмены | `flows/my_bookings.py` |
| T0 | My bookings | Reschedule | Детали → перенести → дата → слот → подтвердить | Запись перенесена, старое время заменено | создаётся дубль вместо переноса | Future booking для переноса | `flows/my_bookings.py` |
| T0 | My bookings | Repeat booking | Детали/прошлая запись → повторить | Открывается запись с предзаполненной услугой/мастером, можно выбрать новый слот | потеря услуги/мастера, недоступные старые данные | Запись с активной услугой/мастером | `flows/booking.py`, `flows/my_bookings.py` |
| T0 | Contacts | Экран контактов | Главное меню → 📍 Контакты | Адрес, телефон, режим работы, кнопки карт | пустой экран, неверные override/YClients данные | Настроенные контакты | `flows/contacts.py`, `services/contacts.py` |
| T0 | Contacts | Кнопки карт | На контактах нажать Яндекс/2GIS/Google | Открываются корректные ссылки | сломанный URL, лишний toast | Валидные URL | `flows/contacts.py`, `ui/buttons.py` |
| T0 | Support | Экран поддержки | Главное меню → 🆘 Поддержка | Показан текст поддержки и кнопка написать | неверный username/url, нет Back/Home | Настроенный support | `flows/support.py` |
| T0 | Navigation | Back/Home в T0 | На каждом T0-экране нажать `⬅️ Назад`, затем `🏠 Главное меню` | Возврат логичный, состояние сбрасывается безопасно | старые кнопки ведут не туда, застревание | Любой клиент | `services/navigation.py` |
| T0 | Text routing | Текст на ожидаемом экране | На экранах телефона/имени/настроек ввести текст | Обрабатывается нужным handler, не fallback | текст уходит в unknown | Активный input screen | `core/router.py`, `core/state.py` |
| T0 | Fallback | Неизвестный текст в меню | В главном меню отправить случайный текст | Понятная подсказка/меню, без crash | generic error, потеря state | `абракадабра` | `flows/fallback.py` |
| T0 | Errors | Generic handler error | Спровоцировать безопасную ошибку настройки/YClients недоступен | Пользователь видит русское понятное сообщение, dev получает диагностику без секретов | traceback/token в UI | Неверный тестовый token | `core/error_handler.py`, `services/developer_diagnostics.py` |
| T0 | Diagnostics | Developer diagnostics | Protected developer → Настройки → Диагностика → обновить | Показан статус loop/ошибок, без raw token | клиент видит диагностику, секреты в UI | Protected developer | `flows/settings.py` |

## T1 — Important admin/business flows

| Priority | Section | Scenario | Steps to test manually | Expected result | Watch for | Test data | Notes / related file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | Settings | Contacts override address | Настройки → контакты → изменить адрес → ввести адрес → предпросмотр | Публичные контакты показывают новый адрес | перетёрлись телефон/карты | Новый адрес | `flows/settings.py` |
| T1 | Settings | Contacts override phone | Изменить телефон → ввести телефон → предпросмотр | Телефон обновлён, формат понятный | принимает мусор, стирает адрес | `+7...` | `repositories/support_settings.py` |
| T1 | Settings | Contacts schedule | Изменить режим работы → ввести текст | Режим обновлён, переносы строк читаемы | пустой режим, сломанная карточка | `Пн-Пт 10:00–21:00` | `flows/settings.py` |
| T1 | Settings | Map links edit/hide/show/delete | По очереди открыть Яндекс/2GIS/Google → изменить ссылку → скрыть → показать → удалить | Состояние каждой карты меняется независимо | невалидный URL принят, удалились все карты | Валидные/невалидные URL | `flows/settings.py` |
| T1 | Settings | Contacts reset | Контакты → сбросить к YClients → контакты | Override сброшен, данные берутся из YClients | wipe unrelated fields | Активный YClients | `services/contacts.py` |
| T1 | Settings | Support username | Настройки → поддержка → изменить username | Публичная кнопка поддержки обновлена | принимает username без @/опасный URL | `@test_support` | `flows/settings.py` |
| T1 | Settings | Support description | Изменить текст поддержки → предпросмотр | Текст виден клиенту, кнопки на месте | пустое описание ломает экран | Русский текст | `flows/settings.py` |
| T1 | Settings | Notification settings | Настройки → 🔔 Уведомления → история | Открывается история уведомлений/статус | нет доступа, crash | Manager/admin/dev | `flows/settings.py`, `flows/notification_history.py` |
| T1 | Settings | Roles entry | Настройки → роли | Только admin/developer видят управление ролями | manager управляет ролями | Роли | `core/permissions.py`, `flows/staff.py` |
| T1 | Staff | Personnel list | Главное меню → Персонал → список | Список ролей/сотрудников отображается | raw user IDs без нужды, пустой список | Несколько ролей | `flows/staff.py` |
| T1 | Staff | Assign manager/admin/developer | Персонал → назначить → ввести идентификатор → выбрать роль | Разрешённые роли назначаются согласно правам | admin назначает developer, неверный user | Тестовые IDs | `core/permissions.py` |
| T1 | Staff | Remove role | Персонал → снять роль → identifier → роль | Роль снята, меню обновлено | protected developer снят, снята не та роль | Тестовый manager/admin | `flows/staff.py` |
| T1 | Clients | Search by phone | Клиенты → по телефону → ввести телефон | До 8 результатов, маскировка телефона, карточка клиента | raw персональные данные сверх нужного | Клиент YClients | `flows/clients_directory.py` |
| T1 | Clients | Search by name | Клиенты → по имени → ввести имя | Результаты по имени, уточнение при >8 | не та карточка после пагинации | Имя клиента | `services/client_segments.py` |
| T1 | Broadcast | One-time self broadcast | Рассылка → разовая → текст → предпросмотр → себе → отправить | Сообщение отправлено только себе, отчёт | отправка всем вместо себя | Тестовый текст | `flows/broadcasts.py` |
| T1 | Broadcast | One-time all users | Рассылка → разовая → текст → всем → подтверждение | Чёткое подтверждение, отчёт delivery | случайная отправка без confirm | Малый тестовый сегмент | `services/broadcasts.py` |
| T1 | Segments | All/active segments | Рассылка → сегменты → все/активные 7/30/90 | Показаны размеры сегментов и кнопка рассылки | неверные counts, таймаут | YClients история | `flows/client_segments.py` |
| T1 | Segments | No future bookings | Сегменты → без будущих записей | Сегмент корректен, можно перейти в рассылку | включает клиентов с future booking | Клиенты разных статусов | `services/client_segments.py` |
| T1 | Lost clients | Lost clients list | Рассылка/сегменты → потерянные клиенты | Список/кол-во потерянных, refresh, broadcast | неверный период, raw IDs | Клиенты без визитов | `flows/lost_clients.py` |
| T1 | Notification history | Recent history | Уведомления/диагностика → история | Последние записи с типом/status/time | нет failed/blocked, утечка текста токенов | История уведомлений | `flows/notification_history.py` |
| T1 | Notification history | Failed details | История → ошибки → открыть деталь | Видны причина, маскированные данные, навигация | traceback/token в UI | Failed notification | `repositories/notification_history.py` |
| T1 | Statistics | Periods | Статистика → Сегодня/7/30/90 | Метрики показываются для периода | неверный timezone/пустые метрики | Записи YClients | `flows/statistics.py` |
| T1 | Admin bookings | Today/tomorrow | Записи → сегодня/завтра | Список записей с фильтрами | клиент видит раздел, неверная дата | Manager/admin/dev | `flows/admin_bookings.py` |
| T1 | Admin bookings | Filters/detail | Записи → фильтр мастер/статус → деталь записи | Фильтры применяются, детали читаемы | raw sensitive data, неверный record | Несколько записей | `services/admin_bookings.py` |
| T1 | Master photos | List/detail/upload/delete | Настройки → фото мастеров → мастер → загрузить/удалить | Фото меняется только у выбранного мастера | принимает не-фото, удаляет чужое | Тестовое изображение | `flows/master_photos.py` |

## T2 — Automation / background flows

| Priority | Section | Scenario | Steps to test manually | Expected result | Watch for | Test data | Notes / related file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T2 | Notifications | Immediate booking confirmation | Создать запись через T0 booking | После успеха приходит подтверждение, история записана | нет уведомления, дубль | Свежая запись | `services/reminders.py`, `services/notifications.py` |
| T2 | Reminders | 48h reminder | В safe env подвести запись к окну 48h и запустить loop | 48h напоминание отправлено один раз | дубль, отправка отменённой записи | Запись через ~48h | `services/reminders.py`, `docs/reminders_manual_smoke.md` |
| T2 | Reminders | 2h reminder | Подвести запись к окну 2h | 2h reminder один раз, текст с услугой/мастером/адресом | неверный адрес/timezone | Запись через ~2h | `services/reminders.py` |
| T2 | Feedback | Post-visit feedback | Завершённая запись → loop feedback | Запрос оценки отправлен один раз | отправка до визита/после отмены | Completed record | `flows/feedback.py`, `services/feedback.py` |
| T2 | Feedback | Negative feedback | Нажать 1–3 звезды → ввести комментарий | Комментарий принят, админам отправлен alert | комментарий уходит в fallback | Test admin | `services/feedback.py` |
| T2 | Birthday | Birthday funnel | Пользователь с ДР сегодня → birthday loop | Поздравление/кнопка записи отправлены один раз за год | повтор каждый запуск | Birthdate today | `services/birthday_funnel.py` |
| T2 | Cancellation recovery | Recovery after cancel | Отменить запись → дождаться/запустить recovery | Клиент получает кнопку подобрать новое время, если нет future booking | отправка при наличии новой записи | Cancelled record | `services/cancellation_recovery.py` |
| T2 | Repeat visit | Repeat visit funnel | Завершённый визит без future booking → loop | Получает приглашение записаться снова | отправка клиенту с future booking | Completed old record | `services/repeat_visit.py` |
| T2 | Notifications | Failed notification handling | Симулировать ошибку отправки | Статус failed в истории, loop живой | crash loop, потеря истории | Invalid recipient | `services/notifications.py` |
| T2 | Notifications | Blocked/stopped users | Симулировать blocked/stopped result | Уведомления пользователю отключены, будущие отправки skipped | повторные попытки каждый loop | Blocked/stopped user | `services/notifications.py` |
| T2 | Loop health | Reminder loop diagnostics | Developer diagnostics после loop | Видны last success/error, loop не падает | silent dead loop | Protected developer | `services/reminders.py`, `services/developer_diagnostics.py` |
| T2 | History | Automation history linkage | Открыть историю после reminders/feedback/repeat | Тип, record id и status связаны с событием | неверный notification_type | Generated events | `repositories/notification_history.py` |

## T3 — Edge cases / polish

| Priority | Section | Scenario | Steps to test manually | Expected result | Watch for | Test data | Notes / related file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T3 | Stale callbacks | Старые кнопки | Открыть запись → Home → нажать старую кнопку из записи | Понятное сообщение/безопасный возврат | создаёт действие из старого state | Старое сообщение | `services/navigation.py` |
| T3 | Double taps | Спам кнопок | Быстро нажать любую кнопку 5–10 раз | Нет дублей, бот не завален сообщениями | ACTION lock не сработал | Любой экран | `core/antiflood.py`, `core/action_locks.py` |
| T3 | Text wrong place | Текст в меню/карточке | Ввести текст на не-input экране | Fallback с подсказкой, state не ломается | текст применился к старой настройке | `тест` | `core/router.py` |
| T3 | Phone invalid | Неверный телефон | В регистрации/booking ввести `123`, `abc`, пусто | Просит повторить, не продолжает | принимает invalid | Invalid strings | `services/registration.py` |
| T3 | Birthdate invalid | Неверная дата | В регистрации ввести `31.02.2020`, будущую дату | Просит корректную дату | сохраняет future/invalid | Invalid dates | `flows/registration.py` |
| T3 | Map URL invalid | Неверная карта | В настройках карты ввести `hello`, `javascript:` | Понятная ошибка, старая ссылка сохранена | XSS/сломанный link button | Invalid URL | `flows/settings.py` |
| T3 | Support invalid | Неверный support username | Ввести `test`, URL, пусто | Ошибка или безопасная нормализация | сломанная кнопка | Invalid usernames | `flows/settings.py` |
| T3 | Empty input | Пустой текст | На input screen отправить пустое/пробелы | Не сохраняет пустое, просит повторить | wipe fields | Spaces | `core/router.py` |
| T3 | YClients unavailable | API недоступен | Подставить wrong token/отключить доступ в test env → открыть booking/check | Русская ошибка, Back/Home работают | traceback, вечный spinner | Wrong credentials | `integrations/yclients/` |
| T3 | No available dates | Нет дат | Выбрать услугу/мастера без дат | Понятное «нет доступных дат» | пустой keyboard | Услуга без расписания | `flows/booking.py` |
| T3 | No available slots | Нет слотов на дату | Выбрать занятую дату | Понятное «нет слотов» | показывает занятые | Полностью занятый день | `services/booking.py` |
| T3 | No services | Нет услуг | Test филиал без активных услуг | Понятное сообщение, Back/Home | crash index | Empty services | `flows/booking.py` |
| T3 | No masters | Нет мастеров | Услуга без мастеров | Понятное сообщение | пустая страница | Service without staff | `flows/booking.py` |
| T3 | Permissions | User without permissions | Обычный клиент нажимает старую admin callback | Доступ запрещён/безопасный fallback | админский экран открыт | Старое admin message | `core/permissions.py` |
| T3 | Role boundaries | Manager/admin/developer | Проверить каждый раздел старой кнопкой и меню | Границы ролей соблюдены | role escalation | 3 роли | `core/permissions.py` |
| T3 | Protected developer | Remove protected dev | Админ/developer пытается снять protected developer | Нельзя удалить/сломать protected access | владелец потерял доступ | Protected dev id | `core/permissions.py`, `flows/staff.py` |
| T3 | No popup | Normal buttons | Нажимать обычные кнопки меню/booking/settings | Нет popup/toast, только смена экрана | лишние уведомления | Любой экран | `core/router.py` |
| T3 | Raw IDs | User UI cleanliness | Просмотреть клиентские экраны | Нет raw technical IDs, callback payload, token | ID/token на карточке | Клиентский аккаунт | `ui/texts.py` |
| T3 | Diagnostics secrecy | Sensitive data | Открыть diagnostics/history/YClients errors | Токены, телефоны и персональные данные маскированы | token leakage | Wrong token + failed notif | `services/developer_diagnostics.py` |
| T3 | Pagination | Lists pagination/indexing | Booking services/masters, clients results, admin bookings | Кнопки открывают правильный элемент после обновления | stale index открывает другой объект | >8 элементов | `core/payloads.py`, `ui/buttons.py` |

## Fast smoke route — 20 minutes

1. `/start` новым или уже зарегистрированным тестовым пользователем.
2. Открыть главное меню и проверить состав кнопок по роли.
3. Пройти booking service-first до success card на тестовом слоте.
4. Пройти booking datetime-first до экрана подтверждения и отменить черновик.
5. Открыть «Мои записи», детали созданной записи, проверить cancel/reschedule только на отдельной тестовой записи.
6. Открыть «Контакты», проверить текст и одну ссылку карты.
7. Открыть «Поддержка», проверить текст и кнопку связи.
8. Manager/developer: Настройки → контакты → изменить адрес → предпросмотр → при необходимости вернуть значение.
9. Manager/developer: YClients → health check.
10. Protected developer: Настройки → Диагностика → обновить, убедиться, что нет секретов.

## Full regression route — 2–3 hours

1. Подготовить тестовых пользователей: client, manager, admin, protected developer.
2. T0: полностью пройти регистрацию нового клиента, включая политики, имя, дату рождения, телефон контактом и текстом.
3. T0: проверить главное меню для всех ролей.
4. T0: проверить YClients setup/check на валидных и невалидных credentials.
5. T0: пройти все 3 сценария записи: service-first, specialist-first, datetime-first.
6. T0: проверить телефонный шаг, confirmation card, double tap confirm, cancel draft.
7. T0: проверить «Мои записи»: список, детали, отмена, перенос, повтор.
8. T0: проверить контакты, поддержку, Back/Home, unknown fallback, developer diagnostics.
9. T1: пройти настройки контактов, карт, поддержки, уведомлений, ролей.
10. T1: пройти персонал: список, назначение, снятие ролей, protected developer.
11. T1: пройти клиенты: поиск по телефону/имени, карточка, refresh/back/home.
12. T1: пройти рассылку себе, затем безопасную рассылку на тестовую аудиторию.
13. T1: пройти сегменты: all, active 7/30/90, no future bookings, lost clients, handoff в рассылку.
14. T1: проверить историю уведомлений: recent, failed, detail, masking.
15. T1: проверить статистику за все периоды.
16. T1: проверить admin bookings: today/tomorrow, filters, detail.
17. T1: проверить фото мастеров: список, деталь, загрузка/удаление тестового фото.
18. T2: в safe env прогнать immediate confirmation, 48h reminder, 2h reminder.
19. T2: прогнать feedback, negative feedback alert, birthday, cancellation recovery, repeat visit.
20. T2: симулировать failed/blocked/stopped delivery и проверить историю/отключение уведомлений.
21. T3: пройти stale callbacks, old buttons after Home, double taps/spam clicks, invalid inputs, no available services/masters/dates/slots.
22. В конце проверить `git diff --name-only`/релизный diff вручную, что тестирование не внесло неожиданных изменений в код/настройки репозитория.

## Top 10 highest-risk manual checks

1. Double tap на подтверждении записи — не должно быть дублей в YClients.
2. Date/time-first booking — не должны появляться недоступные комбинации услуга/мастер/слот.
3. Reschedule — должен переносить существующую запись, а не создавать новую.
4. YClients setup/check — токены не должны отображаться в UI/диагностике.
5. Text input routing — телефон/настройки/feedback не должны попадать в unknown fallback.
6. Role-aware menu — клиент не должен видеть admin/business разделы.
7. Protected developer — доступ владельца нельзя снять настройками ролей.
8. Contacts settings reset/edit — изменение одного поля не должно стирать остальные.
9. Blocked/stopped notification — будущие уведомления должны отключаться/skipped.
10. Старые кнопки после Home — не должны выполнять опасные действия из устаревшего state.

## Flows with uncertain mapping from current code

- В инвентаре UX отмечено, что MAX reminder UX/callback parity для 48h/2h подтверждений и dev-test reminder экранов может быть неполной; в текущем чеклисте это вынесено в T2 как background-проверка через loop/историю.
- Сегменты клиентов в MAX выглядят уже, чем Telegram reference: all/active/lost/no-future-bookings покрыты, но сегменты по мастеру/услуге/категории/дню рождения требуют отдельного аудита перед добавлением в обязательную регрессию.
- Birthday/cancellation recovery/repeat visit в основном выглядят как сервисные background flows без полноценного пользовательского меню; проверять через safe env, историю уведомлений и фактическое сообщение пользователю.

## Bug report template

```md
### Bug
Section:
Scenario:
Steps:
Expected:
Actual:
Screenshot/video:
Error id:
Callback payload:
Current screen/state:
Priority:
```

## Stop rules

Остановить ручной прогон и сразу фиксить баг, если найдено:

- бот падает или перестаёт отвечать;
- пользователь видит generic error без понятного восстановления;
- запись создаётся с неверной услугой, мастером, датой, временем или телефоном;
- создаётся duplicate booking после double tap/повторного callback;
- пользователю показан недоступный слот;
- текстовый ввод на активном input screen уходит в unknown fallback;
- настройки стирают unrelated fields;
- обычный пользователь видит admin-only section;
- protected developer теряет доступ или может быть удалён;
- в UI/диагностике видны raw token, callback payload, traceback или чувствительные данные.
