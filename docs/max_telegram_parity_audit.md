# MAX ↔ Telegram parity plan

Это не отчёт на 2000 строк. Это **человеческий план будущих промтов**, чтобы по шагам довести MAX-бота до полного переноса Telegram reference.

## Как работать по этому файлу

1. Идём сверху вниз по backlog.
2. Один пункт = один маленький PR.
3. В каждом PR сначала открыть Telegram source, потом MAX target.
4. Не придумывать новый UX. MAX повторяет Telegram 1:1, кроме подтверждённых ограничений платформы MAX.
5. Если Telegram и MAX конфликтуют — остановиться и спросить владельца.

## Стандарт будущих Codex-промтов

Future Codex prompts must follow this standard:

1. Only Telegram reference → MAX parity.
2. One PR = one small piece.
3. Concrete Telegram source files.
4. Concrete MAX target files.
5. Do not invent UX/logic.
6. Do not touch unrelated flows.
7. Required Telegram → MAX parity table.
8. Required “Not ported and why”.
9. Required smoke checklist.
10. If Telegram and MAX conflict, report first, do not decide silently.

## Когда MAX-бот считается полностью перенесённым

MAX-бот можно считать перенесённым, когда:

- `/start`, регистрация, главное меню, запись, мои записи, контакты и поддержка визуально повторяют Telegram reference;
- роли `developer/admin/manager/user` видят те же разделы и имеют те же ограничения;
- запись, мои записи, сегменты, статистика, клиентская база и рассылки берут правду из YClients;
- уведомления и автоворонки имеют те же тексты, кнопки, dedup, skip-правила и timezone-логику;
- настройки, персонал, диагностика и YClients setup/check покрывают Telegram UX;
- нет aiogram-импортов в MAX-коде;
- в UI/diagnostics нет токенов, raw YClients payload и полных телефонов;
- все отличия от Telegram явно записаны как решение владельца или ограничение MAX.

## MVP cut line

### Сделать до показа/продажи

1. Booking: double-tap protection, hub, services/categories, dates/slots, create payload/comment/attribution.
2. My bookings: active list, detail, cancellation.
3. Registration/menu: first-run и role-based top menu.
4. Public contacts/support.
5. YClients settings/check.
6. Safe diagnostics/error delivery.
7. Broadcast: либо безопасный self/all flow, либо временно отключить массовую рассылку.

### Можно после первого клиента

- Reschedule/repeat booking.
- Contacts/support editors.
- Notification history polish.
- Advanced segments.
- Statistics/admin bookings/clients directory polish.
- Feedback/recovery/repeat/birthday funnels.
- Staff onboarding, loyalty/referrals, advanced dev tools.

### Можно временно скрыть/отключить

- Broadcast to all/YClients audiences до safety-проверки.
- Advanced segments: cancelled/by master/by service/birthday soon.
- Effectiveness analytics.
- Loyalty/referrals.
- Developer log export/user event search.
- Admin booking quick actions/search filters.

## Backlog будущих маленьких PR

| # | Priority | Prompt title | Scope | Telegram source | MAX target | Acceptance / smoke |
|---|---|---|---|---|---|---|
| 001 | P0 | Booking confirm: защита от дублей | Только final confirm создания записи | `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/core/action_locks.py` | `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/core/action_locks.py` | Double tap создаёт максимум одну запись в YClients; второй tap получает friendly response; smoke: confirm 2–3 раза быстро. |
| 002 | P0 | Booking hub: первый экран записи | Только экран `✂️ Записаться` и branch buttons | `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/keyboards/booking.py` | `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/ui/buttons.py`, `max_barbershop_bot/ui/texts.py` | Текст/кнопки/order как Telegram; Back/Home работают; smoke: открыть запись из меню. |
| 003 | P0 | Booking services/categories | Только услуги/категории, empty state, pagination/back/home | `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/integrations/yclients/service.py` | `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/services/booking.py`, `max_barbershop_bot/ui/buttons.py` | Недоступные услуги скрыты; empty/error UX как Telegram; smoke: category with/without services. |
| 004 | P0 | Booking dates/slots | Только выбор даты/времени | `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/keyboards/booking.py`, `telegram_reference/app/services/company_time.py` | `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/services/booking.py`, `max_barbershop_bot/services/company_time.py` | Timezone филиала, прошлые/занятые слоты скрыты, stale slot friendly; smoke: today slots + old button. |
| 005 | P0 | Booking create payload/comment/attribution | Только YClients create payload, comment marker, local attribution | `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/services/notification_history.py`, `telegram_reference/YCLIENTS REST API.pdf` | `max_barbershop_bot/services/booking.py`, `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/repositories/platform_attribution.py`, `max_barbershop_bot/services/company_time.py` | Record comment/attribution как Telegram; local attribution сохраняется; smoke: создать запись и проверить YClients comment. |
| 006 | P0 | My bookings active list | Только root/active list | `telegram_reference/app/handlers/my_bookings.py` | `max_barbershop_bot/flows/my_bookings.py`, `max_barbershop_bot/services/my_bookings.py`, `max_barbershop_bot/ui/buttons.py` | Ownership по phone/client_id; active/past/cancelled filters; smoke: no bookings, future booking, cancelled booking. |
| 007 | P0 | My booking detail | Только details screen и видимость кнопок | `telegram_reference/app/handlers/my_bookings.py` | `max_barbershop_bot/flows/my_bookings.py`, `max_barbershop_bot/services/my_bookings.py`, `max_barbershop_bot/ui/buttons.py` | Card text/status/date/master/services/buttons как Telegram; smoke: future/past/cancelled detail. |
| 008 | P0 | My booking cancel | Только cancel start/confirm/success/error | `telegram_reference/app/handlers/my_bookings.py` | `max_barbershop_bot/flows/my_bookings.py`, `max_barbershop_bot/services/my_bookings.py` | Confirmation UX и YClients cancellation как Telegram; smoke: cancel → back, cancel → confirm. |
| 009 | P0 | Registration first-run | Только first-run registration steps | `telegram_reference/app/handlers/start.py`, `telegram_reference/app/handlers/onboarding/registration.py` | `max_barbershop_bot/flows/registration.py`, `max_barbershop_bot/services/registration.py`, `max_barbershop_bot/ui/screens.py`, `max_barbershop_bot/ui/buttons.py` | Решить policy-first conflict; validation Russian + emojis; smoke: new user + invalid name/phone/birthdate. |
| 010 | P0 | Main menu user | Только menu для `user` | `telegram_reference/app/keyboards/menu.py`, `telegram_reference/app/keyboards/factory.py` | `max_barbershop_bot/ui/buttons.py`, `max_barbershop_bot/ui/screens.py`, `max_barbershop_bot/flows/menu.py` | Labels/order/visible items как Telegram; no extra top-level без owner decision; smoke: открыть и нажать каждый пункт. |
| 011 | P0 | Main menu roles | Только top-level manager/admin/developer menus | `telegram_reference/app/keyboards/factory.py`, `telegram_reference/app/keyboards/menu.py`, `telegram_reference/app/core/staff_permissions.py`, `telegram_reference/app/handlers/dev.py` | `max_barbershop_bot/ui/buttons.py`, `max_barbershop_bot/core/permissions.py`, `max_barbershop_bot/flows/start.py` | Role matrix как Telegram; protected developer не ломается; smoke: manager/admin/developer menus. |
| 012 | P0 | Safe diagnostics/error delivery | Только error formatting, masking, developer alert | `telegram_reference/app/middlewares/error_notify.py`, `telegram_reference/app/core/error_monitor.py`, `telegram_reference/app/core/security.py` | `max_barbershop_bot/core/error_handler.py`, `max_barbershop_bot/services/diagnostics.py`, `max_barbershop_bot/services/developer_diagnostics.py` | User видит generic Russian error; developer alert masked; smoke: safe test error. |
| 013 | P1 | Contacts public screen | Только публичный экран контактов | `telegram_reference/app/handlers/sections.py`, `telegram_reference/app/services/contacts.py` | `max_barbershop_bot/flows/contacts.py`, `max_barbershop_bot/services/contacts.py`, `max_barbershop_bot/ui/buttons.py` | Адрес/телефон/часы/map buttons как Telegram; smoke: меню → контакты → карты. |
| 014 | P1 | Support public screen | Только публичный экран поддержки | `telegram_reference/app/handlers/sections.py`, `telegram_reference/app/services/support.py` | `max_barbershop_bot/flows/support.py`, `max_barbershop_bot/repositories/support_settings.py`, `max_barbershop_bot/ui/buttons.py` | Text + `🆘 Написать в поддержку`, no raw `link:` line; smoke: меню → поддержка → кнопка. |
| 015 | P1 | YClients settings status | Только entry/status screen интеграции | `telegram_reference/app/handlers/yclients_setup.py` | `max_barbershop_bot/flows/yclients_settings.py`, `max_barbershop_bot/ui/buttons.py`, `max_barbershop_bot/repositories/yclients_settings.py` | Status/masking/buttons как Telegram; smoke: empty/saved settings. |
| 016 | P1 | YClients check errors | Только read-only connection check | `telegram_reference/app/handlers/yclients_setup.py`, `telegram_reference/app/integrations/yclients/errors.py` | `max_barbershop_bot/flows/yclients_settings.py`, `max_barbershop_bot/services/yclients_settings.py`, `max_barbershop_bot/integrations/yclients/exceptions.py` | Auth/rate-limit/server/network messages masked; smoke: valid/invalid credentials. |
| 017 | P1 | Back/Home/stale callbacks | Core screens only: booking entry, contacts, support, settings, my bookings root | `telegram_reference/app/handlers/navigation.py`, `telegram_reference/app/core/navigation.py`, `telegram_reference/app/middlewares/callback_safety.py` | `max_barbershop_bot/services/navigation.py`, `max_barbershop_bot/core/state.py`, `max_barbershop_bot/flows/menu.py` | Back/Home/stale behavior как Telegram; smoke: screen → Back/Home → old button. |
| 018 | P1 | Unknown text vs input states | Registration input states only | `telegram_reference/app/handlers/fallback.py`, `telegram_reference/app/handlers/onboarding/registration.py` | `max_barbershop_bot/flows/fallback.py`, `max_barbershop_bot/flows/registration.py`, `max_barbershop_bot/core/router.py` | Invalid input получает field validation, не generic unknown; smoke: invalid name/phone/birthdate. |
| 019 | P1 | Broadcast self audience safety | One-time broadcast to self only | `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/services/broadcast_sender.py`, `telegram_reference/tests/integration/test_one_time_broadcast_yclients_audiences.py` | `max_barbershop_bot/flows/broadcasts.py`, `max_barbershop_bot/services/broadcasts.py` | Self-send не трогает YClients и не шлёт другим; smoke: broadcast self. |
| 020 | P1 | Broadcast all YClients audience | All-clients estimate/confirm/report | `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/services/broadcast_sender.py`, `telegram_reference/tests/integration/test_one_time_broadcast_yclients_audiences.py` | `max_barbershop_bot/flows/broadcasts.py`, `max_barbershop_bot/services/omnichannel_broadcasts.py`, `max_barbershop_bot/repositories/omnichannel_broadcasts.py` | YClients source, manual ignores notification settings, blocked skip, no duplicates, report; smoke: small test audience. |
| 021 | P1 | Notification history | Recent/failed/detail screens only | `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/services/notification_history.py` | `max_barbershop_bot/flows/notification_history.py`, `max_barbershop_bot/repositories/notification_history.py` | Fields/status/masking/navigation как Telegram; smoke: recent/failed/detail. |
| 022 | P1 | 48h reminder UX/test | 48h reminder test message and callbacks | `telegram_reference/app/handlers/booking_reminders.py`, `telegram_reference/app/services/booking_reminders.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/broadcasts.py`, `max_barbershop_bot/services/reminders.py`, `max_barbershop_bot/services/notifications.py` | Text/buttons/callbacks/history match Telegram; cancelled/deleted skipped; smoke: dev test 48h. |
| 023 | P1 | 2h reminder UX/test | 2h reminder test message | `telegram_reference/app/services/booking_reminders.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/broadcasts.py`, `max_barbershop_bot/services/reminders.py`, `max_barbershop_bot/services/notifications.py` | Text/buttons/history match Telegram; dev-test безопасен; smoke: dev test 2h. |
| 024 | P1 | Staff list | Personnel list screen only | `telegram_reference/app/handlers/staff/personnel.py`, `telegram_reference/app/keyboards/staff.py` | `max_barbershop_bot/flows/staff.py`, `max_barbershop_bot/repositories/staff_roles.py` | Role labels/empty/protected developer; no restaurant wording; smoke: admin/developer staff list. |
| 025 | P1 | Role assignment permissions | Assign-role flow only | `telegram_reference/app/handlers/staff/personnel.py`, `telegram_reference/app/core/staff_permissions.py` | `max_barbershop_bot/flows/staff.py`, `max_barbershop_bot/core/permissions.py` | Manager/admin/developer can assign only allowed roles; smoke: admin tries developer, developer tries admin. |
| 026 | P1 | Settings hub parity | Settings root screen only | `telegram_reference/app/handlers/sections.py`, `telegram_reference/app/handlers/master_photos_settings.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/settings.py`, `max_barbershop_bot/ui/buttons.py`, `max_barbershop_bot/ui/texts.py` | Entries/order/role gates match Telegram or owner decision; smoke: settings as each role. |
| 027 | P2 | My booking reschedule | Reschedule entry and transition | `telegram_reference/app/handlers/my_bookings.py`, `telegram_reference/app/handlers/booking_flow.py` | `max_barbershop_bot/flows/my_bookings.py`, `max_barbershop_bot/flows/booking.py`, `max_barbershop_bot/services/my_bookings.py` | Prefill/fallback/status restrictions match Telegram. |
| 028 | P2 | My booking repeat | Repeat from history/detail | `telegram_reference/app/handlers/my_bookings.py`, `telegram_reference/app/handlers/booking_flow.py` | `max_barbershop_bot/flows/my_bookings.py`, `max_barbershop_bot/flows/booking.py` | Service/master prefill or safe fallback like Telegram. |
| 029 | P2 | Contacts settings editor | Address/phone/hours/maps edit + preview/reset | `telegram_reference/app/handlers/master_photos_settings.py`, `telegram_reference/app/repositories/contacts_override.py` | `max_barbershop_bot/flows/settings.py`, `max_barbershop_bot/services/contacts.py`, `max_barbershop_bot/repositories/settings.py` | Persistence, preview, reset to YClients, map links match Telegram. |
| 030 | P2 | Support settings editor | Username/link/description edit + preview | `telegram_reference/app/handlers/master_photos_settings.py`, `telegram_reference/app/repositories/support_settings.py` | `max_barbershop_bot/flows/settings.py`, `max_barbershop_bot/repositories/support_settings.py` | Username/link normalization and preview match Telegram. |
| 031 | P2 | Master photo settings | List/detail/upload/delete | `telegram_reference/app/handlers/master_photos_settings.py`, `telegram_reference/app/repositories/master_photos.py` | `max_barbershop_bot/flows/master_photos.py`, `max_barbershop_bot/services/master_photos.py`, `max_barbershop_bot/ui/buttons.py` | Visual UX and validation match Telegram. |
| 032 | P2 | Segment active 30 | Active 30 screen + broadcast handoff | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py`, `max_barbershop_bot/flows/broadcasts.py` | Source = YClients history; counts/handoff match Telegram. |
| 033 | P2 | Segment lost 30/60/90 | Lost segment screens | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/services/lost_clients.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/flows/lost_clients.py`, `max_barbershop_bot/services/lost_clients.py` | Periods/reasons/counts match Telegram. |
| 034 | P2 | Segment no future booking | No-future-booking segment | `telegram_reference/app/services/client_segments.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py` | Clients with future bookings excluded using YClients source. |
| 035 | P2 | Segment cancelled booking | Cancelled booking segment | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py` | Cancelled/no-show definition matches Telegram. |
| 036 | P2 | Segment by master | Master picker + selected-master segment | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py` | Master list/counts use YClients and match Telegram. |
| 037 | P2 | Segment by service | Service picker + selected-service segment | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/handlers/notifications.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py` | Service list/counts use YClients and match Telegram. |
| 038 | P2 | Segment birthday soon | Birthday segment + handoff | `telegram_reference/app/services/client_segments.py`, `telegram_reference/app/services/birthday_funnel.py` | `max_barbershop_bot/flows/client_segments.py`, `max_barbershop_bot/services/client_segments.py`, `max_barbershop_bot/services/birthday_funnel.py` | Date window/text/counts match Telegram. |
| 039 | P2 | Feedback after visit | Request/rating/comment | `telegram_reference/app/handlers/feedback.py`, `telegram_reference/app/services/post_visit_feedback.py` | `max_barbershop_bot/flows/feedback.py`, `max_barbershop_bot/services/feedback.py` | Text/buttons/admin negative alert match Telegram. |
| 040 | P2 | Cancellation recovery | Recovery message + skip/dedup | `telegram_reference/app/services/cancellation_recovery.py`, `telegram_reference/app/services/cancellation_recovery_sender.py` | `max_barbershop_bot/services/cancellation_recovery.py` | Future booking skip, text/button/dedup match Telegram. |
| 041 | P2 | Repeat visit funnel | Outgoing message + dedup | `telegram_reference/app/services/repeat_visit.py`, `telegram_reference/app/repositories/repeat_visit_events.py` | `max_barbershop_bot/services/repeat_visit.py`, `max_barbershop_bot/repositories/repeat_visit_events.py` | Due logic, text/button, dedup match Telegram. |
| 042 | P2 | Birthday funnel | Birthday automation + dedup | `telegram_reference/app/services/birthday_funnel.py`, `telegram_reference/app/repositories/birthday_funnel_events.py` | `max_barbershop_bot/services/birthday_funnel.py`, `max_barbershop_bot/repositories/birthday_funnel_events.py` | Due date, text/button, dedup match Telegram. |
| 043 | P2 | Statistics summary | Summary by period | `telegram_reference/app/handlers/statistics.py`, `telegram_reference/app/services/statistics.py`, `telegram_reference/app/keyboards/statistics.py` | `max_barbershop_bot/flows/statistics.py`, `max_barbershop_bot/services/statistics.py` | Periods, metric names, formatting, empty/error states match Telegram. |
| 044 | P2 | Admin bookings today | Today list only | `telegram_reference/app/handlers/admin_bookings.py` | `max_barbershop_bot/flows/admin_bookings.py`, `max_barbershop_bot/services/admin_bookings.py` | Cards/statuses/source from YClients match Telegram. |
| 045 | P2 | Clients directory phone search | Phone search prompt/results/card | `telegram_reference/app/handlers/clients_directory.py` | `max_barbershop_bot/flows/clients_directory.py`, `max_barbershop_bot/integrations/yclients/service.py` | Normalization/masking/result limit/card match Telegram. |
| 046 | P2 | Clients directory name search | Name search prompt/results/card | `telegram_reference/app/handlers/clients_directory.py` | `max_barbershop_bot/flows/clients_directory.py`, `max_barbershop_bot/integrations/yclients/service.py` | Too-many-results, result selection/card match Telegram. |
| 047 | P3 | Loyalty/referrals decision | Только scope decision: port or hide | `telegram_reference/app/handlers/loyalty_mvp.py`, `telegram_reference/app/handlers/loyalty/`, `telegram_reference/app/services/loyalty_mvp.py`, `telegram_reference/app/repositories/referrals.py` | Decide after owner answer | Owner decision documented; no placeholder UX. |
| 048 | P3 | Developer logs/user event search | Advanced dev tools only | `telegram_reference/app/handlers/dev.py`, `telegram_reference/app/handlers/system.py` | `max_barbershop_bot/services/developer_diagnostics.py`, `max_barbershop_bot/flows/settings.py` | Only developer sees logs/search; output masked. |
| 049 | P3 | Broadcast effectiveness | Effectiveness screen only | `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/services/effectiveness.py` | `max_barbershop_bot/flows/broadcasts.py`, `max_barbershop_bot/services/broadcasts.py` | Metrics match Telegram or feature hidden. |

## Следующие 10 промтов

1. `PR-001 — Booking confirm: защита от дублей`
2. `PR-002 — Booking hub: первый экран записи`
3. `PR-003 — Booking services/categories`
4. `PR-004 — Booking dates/slots`
5. `PR-005 — Booking create payload/comment/attribution`
6. `PR-006 — My bookings active list`
7. `PR-007 — My booking detail`
8. `PR-008 — My booking cancel`
9. `PR-009 — Registration first-run`
10. `PR-012 — Safe diagnostics/error delivery`

## Конфликты, которые нельзя решать молча

1. **Policy-first registration in MAX vs Telegram contact-first registration.** Нужно решение владельца.
2. **Reply keyboard in Telegram vs inline buttons in MAX.** Нужно определить, достаточно ли labels/order parity.
3. **Top-level `👥 Мастера` in MAX user menu.** В Telegram parity это не подтверждено.
4. **YClients/notification history top-level placement.** В Telegram часть разделов может быть внутри settings/notifications.
5. **Support/map URL buttons.** Если MAX URL buttons не поддерживает, нужно описать platform limitation.
6. **Broadcast delivery priority Telegram/MAX.** Нужен owner decision, если omnichannel логика отличается.
7. **Local DB audience vs YClients audience.** Для клиентских сегментов и массовых рассылок source of truth должен быть YClients.
8. **Loyalty/referrals.** Есть в Telegram reference, но неочевидно, нужно ли для первого MAX MVP.
9. **GIF/video broadcast support.** Не обещать parity без проверки MAX transport.

## Проверки после каждого PR

```bash
python -m compileall max_barbershop_bot
rg "from aiogram|import aiogram" max_barbershop_bot || true
rg "TODO|pass|placeholder|заглуш|not implemented|unknown command|Я пока не знаю" max_barbershop_bot telegram_reference docs || true
rg "Рассылка|Мои записи|Сегменты|Уведомления|Тест уведомлений|Диагностика|Клиенты|Персонал|Настройки|YClients" max_barbershop_bot telegram_reference docs || true
git diff --check
```

## Что было просмотрено

**Telegram reference:**

- `telegram_reference/app/handlers/`
- `telegram_reference/app/services/`
- `telegram_reference/app/repositories/`
- `telegram_reference/app/ui/`
- `telegram_reference/app/integrations/yclients/`
- `telegram_reference/app/middlewares/`
- `telegram_reference/app/keyboards/`
- найденные Telegram tests/docs/smoke-файлы

**MAX implementation:**

- `max_barbershop_bot/flows/`
- `max_barbershop_bot/services/`
- `max_barbershop_bot/repositories/`
- `max_barbershop_bot/ui/`
- `max_barbershop_bot/core/`
- `max_barbershop_bot/integrations/yclients/`
- `docs/`

## Ограничения

- Это статический plan/audit; live MAX, Telegram и YClients не запускались.
- YClients availability, timezone boundaries и ownership edge cases требуют тестовой компании.
- MAX platform limitations по URL/GIF/video нужно подтверждать отдельно.
