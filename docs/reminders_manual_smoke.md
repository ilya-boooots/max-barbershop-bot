# T2 — 48h and 2h booking reminders

Безопасность: выполняйте только в safe/dev окружении. Не запускайте проверку против реальных клиентов, если отправка им уведомлений явно не планируется.

## 48h smoke

1. Используйте только safe/dev окружение MAX-бота и тестовую компанию/филиал YClients.
2. Создайте тестового MAX-пользователя, который уже открыл бота.
3. Создайте в YClients запись примерно через 48 часов от текущего времени филиала.
4. Убедитесь, что запись активна: не отменена и не удалена.
5. Убедитесь, что у пользователя включены уведомления (`notifications_enabled=true`).
6. Запустите reminder loop вручную или дождитесь интервала фонового цикла.
7. Ожидаемый результат:
   * отправлено ровно одно 48h-напоминание/подтверждение;
   * в `notification_history` есть одна строка с типом `booking_confirmation_2d`;
   * в `notification_delivery` есть результат доставки со статусом `sent`;
   * второй запуск loop не отправляет дубль.

## 2h smoke

1. Используйте только safe/dev окружение MAX-бота и тестовую компанию/филиал YClients.
2. Создайте тестового MAX-пользователя, который уже открыл бота.
3. Создайте в YClients запись примерно через 2 часа от текущего времени филиала.
4. Убедитесь, что запись активна: не отменена и не удалена.
5. Убедитесь, что у пользователя включены уведомления (`notifications_enabled=true`).
6. Запустите reminder loop вручную или дождитесь интервала фонового цикла.
7. Ожидаемый результат:
   * отправлено ровно одно 2h-напоминание;
   * текст содержит услугу, мастера и адрес как в Telegram reference;
   * в `notification_history` есть одна строка с типом `booking_reminder_2h`;
   * в `notification_delivery` есть результат доставки со статусом `sent`;
   * второй запуск loop не отправляет дубль.

## Cancelled booking smoke

1. Создайте тестовую запись в YClients и отмените её.
2. Запустите loop.
3. Ожидаемый результат: 48h/2h напоминание не отправлено.

## Moved booking smoke

1. Перенесите тестовую запись за пределы due window нужного напоминания.
2. Запустите loop.
3. Ожидаемый результат: напоминание не отправлено.

## Diagnostics

1. В логах должна быть строка с префиксом `MAX reminders diagnostic:`.
2. Разрешённые поля диагностики: `reminder_type`, `loop_enabled`, `loop_interval_seconds`, `branch_timezone`, `now_branch_time`, `due_window_start`, `due_window_end`, `yclients_records_checked`, `due_candidates_count`, `skipped_cancelled_count`, `skipped_deleted_count`, `skipped_past_count`, `skipped_duplicate_count`, `skipped_no_platform_mapping_count`, `skipped_notifications_disabled_count`, `skipped_blocked_count`, `sent_count`, `failed_count`, `duration_ms`.
3. В диагностике не должно быть токенов, полных телефонов, raw payload YClients или raw DB rows.

## Safe test buttons: 48h and 2h

1. Откройте в MAX: `📣 Рассылка → 🧪 Тест уведомлений`.
2. Нажмите `✅ Тест подтверждения записи (48ч+)`.
3. Ожидается: текущему MAX-пользователю/чату сразу приходит тестовое сообщение с Telegram-текстом 48h confirmation и кнопками `✅ Да, запись в силе` / `❌ Нет, отменить или перенести`; в `notification_history` и `notification_delivery` создаются dev/test строки.
4. Нажмите `⏰ Тест напоминания о записи (2ч)`.
5. Ожидается: текущему MAX-пользователю/чату сразу приходит тестовое сообщение с Telegram-текстом 2h reminder и кнопками `📅 Мои записи` / `🏠 Главное меню`; в `notification_history` и `notification_delivery` создаются dev/test строки.
6. Тестовые записи имеют `yclients_record_id` с префиксом `dev-test-` и не создают/не меняют реальные записи YClients.
