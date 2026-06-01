# STEP 5 — Booking reminders: проверка стабилизации

Дата проверки: 28.05.2026

## Что было проверено

### Хранение reminder events

- Таблица: `booking_reminder_events`.
- Репозиторий: `app/repositories/booking_reminder_events.py`.
- Основные поля: `yclients_record_id`, `yclients_client_id`, `client_tg_id`, `client_phone`, `company_id`, `visit_datetime_utc`, `branch_timezone`, `reminder_type`, `status`, `scheduled_at_utc`, `sent_at_utc`, `clicked_at_utc`, `error`, `created_at_utc`, `updated_at_utc`.
- Жизненный цикл статусов: `pending` → `sent` / `confirmed` / `skipped` / `failed`.
- Защита от дублей для real-событий: `UNIQUE(yclients_record_id, reminder_type)` + `INSERT OR IGNORE`.
- Dev-test повторяемость: каждое тестовое событие создаётся с уникальным `yclients_record_id` формата `dev-test-*`, поэтому не конфликтует с real-дедупликацией.

### 48h confirmation

- Sender: `app/services/booking_reminders.py`, функция `process_due_events` для `reminder_type='confirm_2d'`.
- Шаблон: `{client_name}, здравствуйте! {master_name} ждёт вас {date_label} ({date}) на услугу "{service_name}" к {time}.` + `Подтвердите, пожалуйста, запись 👇`.
- Кнопки: `brc:y:{event_id}` и `brc:n:{event_id}`.
- Real data source: YClients `GET /api/v1/record/{company_id}/{record_id}`.
- Dev-test: безопасные fake-данные, без вызовов YClients, но через тот же финальный шаблон.

### 2h reminder

- Sender: `app/services/booking_reminders.py`, функция `process_due_events` для `reminder_type='reminder_2h'`.
- Шаблон: `{client_name}, вы записаны на услугу «{service_name}», ждём вас {date} к {time}.` + `Ваш мастер: {master_name}` + `📍 Адрес: {branch_address}`.
- Кнопки: `my_bookings:open` и `nav:home`.
- Real data source: YClients `GET /api/v1/record/{company_id}/{record_id}` и `GET /api/v1/company/{company_id}` для адреса/timezone.
- Dev-test: безопасные fake-данные, без вызовов YClients, через тот же финальный шаблон.

### YES / NO callbacks

- YES callback: `brc:y:{event_id}`.
- Handler: `app/handlers/booking_reminders.py`, функция `confirm_yes`.
- YClients flow для real record: загрузить event → взять `yclients_record_id` → получить полную запись через `GET /api/v1/record/{company_id}/{record_id}` → собрать full update payload → отправить `PUT /api/v1/record/{company_id}/{record_id}` → только после успеха отметить local event как `confirmed`.
- Payload keys для confirmation update: `id`, `staff_id`, `services`, `client`, `datetime`, `seance_length`, `save_if_busy`, `attendance` + опциональные сохранённые поля `send_sms`, `comment`, `sms_remain_hours`, `email_remain_hours`, `api_id`, `custom_color`.
- Dev-test YES: YClients не вызывается, local event помечается `confirmed`.
- NO callback: `brc:n:{event_id}`.
- Handler: `app/handlers/booking_reminders.py`, функция `confirm_no`.
- Поведение NO: открывается безопасный экран выбора, немедленной отмены или переноса нет.

## Что было исправлено

1. Отображение даты/времени real reminders переведено на timezone филиала, а не timezone сервера/сырой UTC.
2. При сканировании YClients naive datetime теперь интерпретируется как время филиала и сохраняется в UTC для scheduling.
3. Для 48h confirmation и 2h reminder дата/время форматируются в branch timezone.
4. Dev-test reminders используют branch timezone из event при отображении даты.
5. Dev-test success message теперь показывается только если Telegram send действительно вернул `ok`; `skipped` больше не помечается как `sent`.
6. YES handler для real record использует `company_id` из reminder event, извлекает имя клиента из YClients record для success message и не помечает событие confirmed при ошибке update.
7. Для failed YES добавлена безопасная developer diagnostic без токенов и без полного raw YClients payload: action, event_id, record_id, endpoint/method, payload keys, exception type/message, traceback tail.
8. Добавлены unit-тесты на шаблоны, кнопки, timezone, dev-test bypass, повторяемость `dev-test-*`, real duplicate protection и failure behavior YES.

## Что требует ручной Telegram/YClients проверки

Автотесты не выполняют реальный Telegram runtime и не вызывают настоящий YClients. Поэтому вручную нужно проверить доставку сообщений, реальные callback-и и реальный YClients update в окружении с валидными токенами.

## Manual test steps

### MT-027 — dev-test notifications

1. Войти под developer Telegram ID `378881880`.
2. Открыть раздел `🧪 Тест уведомлений`.
3. Нажать `✅ Тест подтверждения записи (48ч+)` 3–5 раз.
4. Проверить, что пришло 3–5 отдельных сообщений подтверждения записи.
5. Нажать `⏰ Тест напоминания о записи (2ч)` 3–5 раз.
6. Проверить, что пришло 3–5 отдельных 2h reminder messages.
7. Проверить по логам/БД, что у каждого события уникальный `yclients_record_id` с префиксом `dev-test-*`.
8. Убедиться, что dev-test не вызвал YClients.

### MT-028 — 48h confirmation

1. Создать или выбрать real YClients запись, подходящую под 48h confirmation.
2. Дождаться/запустить обработку due reminder event.
3. Проверить текст: имя клиента, мастер, услуга, `DD.MM.YYYY`, `HH:MM`, корректный `сегодня/завтра/послезавтра` при применимости.
4. Проверить, что нет `None`, raw IDs и старой инструкции “пришлите Да/Нет”.
5. Проверить кнопки `✅ Да, запись в силе` и `❌ Нет, отменить или перенести`.
6. Проверить, что дата/время соответствуют timezone филиала.

### MT-029 — yes button

1. Открыть real 48h confirmation reminder.
2. Нажать `✅ Да, запись в силе`.
3. Проверить, что бот ответил `✅ Спасибо за ответ, {client_name}. Ваша запись подтверждена!`.
4. Проверить в YClients, что запись подтверждена/обновлена через `PUT /api/v1/record/{company_id}/{record_id}`.
5. Проверить в БД, что local event стал `confirmed` и заполнен `clicked_at_utc`.
6. Негативный тест: временно смоделировать ошибку YClients update и убедиться, что local event не стал `confirmed`, пользователь получил warning, developer получил diagnostic.

### MT-030 — no button

1. Открыть real или dev-test 48h confirmation reminder.
2. Нажать `❌ Нет, отменить или перенести`.
3. Проверить текст `Поняли. Что хотите сделать?`.
4. Проверить кнопки `❌ Отменить запись`, `🔁 Перенести запись`, `⬅️ Назад`, `🏠 Главное меню`.
5. Проверить, что запись не отменяется и не переносится сразу после нажатия NO.
6. Отдельно проверить существующие cancel/reschedule flows, если они используются дальше.

### MT-031 — 2h reminder

1. Создать или выбрать real YClients запись, подходящую под 2h reminder.
2. Дождаться/запустить обработку due reminder event.
3. Проверить текст: имя клиента, услуга, дата, время, `Ваш мастер`, адрес филиала.
4. Проверить, что нет `Ваш барбер`, promo block, `скидка`, `подарочный сертификат`, `None`, raw IDs.
5. Проверить кнопки `📅 Мои записи` и `🏠 Главное меню`.
6. Проверить, что дата/время соответствуют timezone филиала.
