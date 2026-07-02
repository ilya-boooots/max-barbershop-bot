# T2 smoke: 48h reminder

Безопасность: выполняйте только в safe/dev окружении. Не запускайте проверку против реальных клиентов, если отправка им уведомлений явно не планируется.

1. Используйте safe/dev окружение MAX-бота и тестовую компанию/филиал YClients.
2. Выберите или создайте тестового пользователя, который уже открыл MAX-бота.
3. Создайте в YClients запись тестового клиента примерно через 48 часов от текущего времени филиала.
4. Убедитесь, что запись не отменена и не удалена.
5. Убедитесь, что у пользователя включены уведомления (`notifications_enabled=true`).
6. Запустите reminder loop вручную или дождитесь интервала фонового цикла.
7. Ожидаемый результат:
   * отправлено ровно одно 48h-подтверждение записи;
   * в `notification_history` есть одна строка с типом `booking_confirmation_2d`;
   * в `notification_delivery` есть результат доставки со статусом `sent`;
   * повторный запуск loop не отправляет дубль.
8. Тест отменённой записи:
   * отмените тестовую запись в YClients;
   * запустите loop;
   * ожидается: напоминание не отправлено, skip отражён в логах/истории.
9. Тест перенесённой записи:
   * перенесите запись за пределы 48h/6h окна Telegram-логики;
   * запустите loop;
   * ожидается: 48h-подтверждение не отправлено.
10. Контроль диагностики:
    * в логах должна быть строка с префиксом `MAX reminders diagnostic:`;
    * допустимые поля: `reminder_type`, `loop_enabled`, `loop_interval_seconds`, `branch_timezone`, `now_branch_time`, `due_window_start`, `due_window_end`, `yclients_records_checked`, `due_candidates_count`, `skipped_cancelled_count`, `skipped_deleted_count`, `skipped_past_count`, `skipped_duplicate_count`, `skipped_no_platform_mapping_count`, `skipped_notifications_disabled_count`, `skipped_blocked_count`, `sent_count`, `failed_count`, `duration_ms`;
    * в диагностике не должно быть токенов, полных телефонов, raw payload YClients или raw DB rows.
