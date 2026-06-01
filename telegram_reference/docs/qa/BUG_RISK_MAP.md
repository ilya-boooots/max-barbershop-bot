# BUG RISK MAP

## Топ-10 риск-зон
1. **Booking end-to-end через Telegram + YClients** (P0)
2. **Cancel/Reschedule booking с пограничными статусами YClients** (P0)
3. **Broadcast сегменты на живой базе клиентов** (P0/P1)
4. **Reminder 48h/2h и нажатия подтверждения (yes/no)** (P0)
5. **Role enforcement в handler-level (не только UI-level)** (P1)
6. **Навигация back/home при сложных FSM-сценариях** (P1)
7. **Fallback/ошибки при пустых выборках YClients** (P1)
8. **Дедупликация notification/reminder событий** (P1)
9. **Callback routing drift (кнопка есть, handler устарел)** (P1)
10. **Деградация прав после fallback/home** (P1)

## Матрица риска
| Зона | Вероятность | Влияние | Приоритет | Тип проверки |
|---|---|---|---|---|
| Booking E2E | Средняя | Критичное | P0 | Ручная Telegram+YClients |
| Cancel/Reschedule | Средняя | Критичное | P0 | Ручная Telegram+YClients |
| Broadcast segments | Средняя | Высокое | P0/P1 | Ручная + выборочные авто |
| Reminder confirmations | Средняя | Критичное | P0 | Ручная + unit templates |
| Role ACL в handlers | Низкая/средняя | Высокое | P1 | Unit + ручная |
| Back/Home/FSM | Средняя | Высокое | P1 | Unit + ручная |
| Empty YClients data | Средняя | Среднее | P1 | Mock + ручная |
| Event dedupe | Средняя | Среднее | P1 | Integration + ручная |
| Callback drift | Низкая/средняя | Среднее | P1 | Unit/static + ручная |
| Role fallback degradation | Низкая | Высокое | P1 | Unit + ручная |
