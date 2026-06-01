| ID | Priority | Role | Section | Scenario | Steps | Expected result | Manual/Auto | Notes |
|----|----------|------|---------|----------|-------|-----------------|-------------|-------|
| T-001 | P0 | any | Startup | import app.main | Выполнить smoke import | Импорт успешен | Auto | tests/smoke/test_imports_and_compile.py |
| T-002 | P0 | any | DB | init_db twice | Инициализировать БД 2 раза | Нет краша, миграции идемпотентны | Auto | tests/smoke/test_db_startup.py |
| T-003 | P0 | any | Callback | callback_data <=64 | Запустить unit validation | Все callback валидны | Auto | tests/unit/test_callback_data_validation.py |
| T-004 | P0 | developer | Roles | dev menu | Проверить меню dev id | Видит dev секции | Auto+Manual | test_role_menus + MT-002 |
| T-005 | P0 | user | Roles | regular isolation | Проверить обычное меню | Нет admin/dev секций | Auto+Manual | test_role_menus + MT-003 |
| T-006 | P0 | user | Booking | create booking | Пройти flow записи | Успешная запись | Manual | MT-005 |
| T-007 | P0 | user | Booking | cancel booking | Отменить запись | Запись отменена | Manual | MT-008 |
| T-008 | P0 | user | Booking | reschedule booking | Перенести запись | Новое время сохранено | Manual | MT-009 |
| T-009 | P1 | any | Navigation | back/home | Нажать back/home | 1 шаг назад / главное меню | Auto+Manual | test_navigation_stack + MT-034/35 |
| T-010 | P1 | admin/manager | Broadcast | one-time broadcast | Пройти до отправки | Корректная аудитория/отправка | Manual | MT-014/15 |
| T-011 | P1 | admin/manager | Segments | segment filters | Проверить 8 сегментов | Корректный список получателей | Manual | MT-016..023 |
| T-012 | P0 | any | Reminders | 48h/2h templates | Запустить unit reminders | Шаблоны корректны | Auto | tests/unit/test_reminders_templates.py |
| T-013 | P1 | developer | Dev tests | repeatable dev test | Нажимать dev-test многократно | Нет одноразовой блокировки | Manual | MT-027 |
| T-014 | P1 | any | YClients parsing | parser mocked payloads | Запустить unit parsing | Корректный парсинг | Auto | tests/unit/test_yclients_parsing.py |
| T-015 | P1 | any | Error fallback | API empty/error | Проверить fallback | Нет crash, понятный UX | Manual | MT-033 |
