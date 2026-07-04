# MAX ↔ Telegram parity plan v2

Статус: audit/planning only. Этот документ не меняет production-код и является новым планом выполнения маленьких PR для 1:1 parity MAX с Telegram reference.

## 0. Жёсткие правила для каждого будущего PR

- Источник истины: `telegram_reference/`; текущая реализация: `max_barbershop_bot/`.
- MAX не копирует aiogram handlers; портируются бизнес-логика, UX-flow, русские тексты с emoji, роли, YClients-логика, уведомления, рассылки, статистика и error handling.
- Переписываются transport layer, handlers, keyboards/buttons и update processing под MAX.
- Если Telegram и MAX конфликтуют, исполнитель PR обязан остановиться, описать конфликт и не принимать продуктовых решений молча.
- Один PR = один экран, handler, service, repository или один шаг flow.
- Каждый PR обязан вернуть: Telegram → MAX parity table; что НЕ портировано и почему; smoke checklist; tests/checks; список файлов.
- Не добавлять новую UX/бизнес-логику, MAX-only улучшения и placeholder-flow.
- MAX transport limitation документировать как closest equivalent + причина.
- UI-текст остаётся русским с emoji.
- `telegram_reference/` и старый `.docx` не редактировать.

## 1. Аудит старого `max_telegram_parity_step_plan.docx`

### Что полезно

- Старый план правильно задаёт принцип «Telegram reference → MAX parity», запрет на придумывание UX и обязательный формат будущих PR.
- Есть полезная MVP cut line: booking, my bookings, registration/menu, contacts/support, YClients settings/check, diagnostics, минимально безопасная broadcast-секция.
- 49 пунктов уже нарезаны достаточно мелко: booking confirm, hub, services/categories, dates/slots, create payload, my bookings, registration/menu, diagnostics, contacts/support, YClients, broadcasts, notification history, reminders, staff, settings, segments, funnels, stats, clients directory, loyalty/dev/effectiveness.
- В старом плане есть правильные smoke-идеи: double tap confirm, stale slot, invalid registration input, masked diagnostics, self broadcast, small audience broadcast, dev reminder tests.

### Что неполно

- План почти не фиксирует полную Telegram feature inventory: отсутствуют точные handler/function names по важным commercial features и auto-funnels.
- Broadcast/funnel/dedup/history/segment parts слишком поздно и слишком общо описаны, хотя это главные selling features.
- Не хватает явного слоя notification delivery foundation: единые статусы, `notification_history`, `notification_delivery`, reserve/mark skipped, disabled/blocked/rate-limit semantics.
- Не хватает omnichannel reality: MAX audience ограничен пользователями, открывшими MAX-бота; Telegram reference может слать Telegram-пользователям. Это надо явно фиксировать в каждом broadcast/funnel PR.
- Не хватает отдельных PR на automation settings UI, tests/previews для каждого funnel и safety around manual broadcast confirmation.
- Не хватает явного gap matrix: где MAX уже implemented/partial/missing.

### Что неправильно приоритизировано

- Старый P0/P1 слишком долго держит booking/menu до коммерческих функций. Booking уже частично реализован в MAX, поэтому новая стратегия оставляет в P0 только надёжность booking/my bookings/YClients и сразу поднимает reminders, notification history, broadcast self/preview/confirmation.
- Auto-funnels были P2; в новом плане они P1, потому что это продаваемая ценность: feedback, cancellation recovery, repeat visit, birthday, lost/inactive clients.
- Segments были P2; в новом плане segment builders и broadcast handoff — P1, потому что без сегментов нет продаваемой рассылки.
- Staff/personnel/settings polish остаются P2, кроме access/safety, необходимых для broadcast/funnel admin UX.

### Что надо переработать

- Ввести sales-oriented priority model: P0 reliability + demo, P1 broadcasts/funnels/segments, P2 admin completeness, P3 polish.
- Для каждого будущего PR указать exact Telegram source files и exact MAX target files.
- Разделить broadcast на self-test, text/media draft, preview, audience estimate, confirmation, delivery, report, history/effectiveness.
- Разделить notification automation на foundation/history/dedup + отдельные reminder/funnel PR.
- Добавить pre-PR audit requirement: исполнитель сначала строит parity table по указанным файлам, потом меняет только target files.

## 2. Telegram reference inventory

### A. Public user flows

| Feature | Role | Telegram files | Handler/function names | Text/buttons/callbacks/business rules/data/side effects |
|---|---|---|---|---|
| `/start`, onboarding entry | user/staff | `telegram_reference/app/handlers/start.py`, `telegram_reference/app/handlers/onboarding/registration.py`, `telegram_reference/app/keyboards/menu.py`, `telegram_reference/app/core/auth.py`, `telegram_reference/app/repositories/users.py`, `telegram_reference/app/repositories/role_onboarding.py` | `start`, registration FSM handlers, role onboarding helpers | First-run registration, Russian validation, phone/name/birthdate policy, menu by role, user persisted locally and linked to Telegram id. |
| Main menu | user/manager/admin/developer | `telegram_reference/app/handlers/menu.py`, `telegram_reference/app/keyboards/menu.py`, `telegram_reference/app/keyboards/factory.py`, `telegram_reference/app/core/permissions.py`, `telegram_reference/app/core/staff_permissions.py` | menu open handlers, `user_main_menu_kb`, `admin_main_menu_kb`, `developer_main_menu_kb` | Labels/order/visibility by role; broadcast/settings/YClients/dev diagnostics gated. |
| Booking entry | user | `telegram_reference/app/handlers/booking.py`, `telegram_reference/app/handlers/booking_flow.py`, `telegram_reference/app/keyboards/booking.py` | `open_booking`, `_show_booking_hub`, `_build_booking_hub_kb` | `✂️ Записаться`; service-first/staff-first/datetime-first; Back/Home. |
| My bookings | user | `telegram_reference/app/handlers/my_bookings.py`, `telegram_reference/app/db/booking_links_repo.py` | open/list/detail/cancel/reschedule/repeat handlers | Active/history, details, cancel, repeat/reschedule if allowed; ownership by phone/client mapping. |
| Contacts/support | user | `telegram_reference/app/handlers/sections.py`, `telegram_reference/app/repositories/contacts_override.py`, `telegram_reference/app/repositories/support_settings.py`, `telegram_reference/app/core/screens.py` | contacts/support render helpers | Public address/phone/hours/maps/support button; settings override if present. |
| Masters/staff public | user | `telegram_reference/app/handlers/sections.py`, `telegram_reference/app/handlers/master_photos_settings.py`, `telegram_reference/app/repositories/master_photos.py` | master list/photo helpers | Public staff/master info if exposed; master photos used in booking. |
| Notifications opt in/out | user | `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/repositories/marketing_preferences.py` | `open_notifications`, `set_notifications`, `notifications_kb` | `✅ Включить`, `❌ Выключить`, callbacks `notifications:on/off`; affects automation, not necessarily manual broadcasts. |

### B. Booking

| Feature | Telegram files/functions | Business rules |
|---|---|---|
| Hub and branches | `booking_flow.py`: `_show_booking_hub`, `_build_booking_hub_kb`, `handle_booking_hub_service`, `handle_booking_hub_staff`, `handle_booking_hub_datetime` | Three entry modes: service-first, staff-first, datetime-first. Preserve selected context and navigation. |
| Categories/services | `booking_flow.py`: `_load_services`, `_load_categories`, `_show_categories`, `_show_services`, `_service_visibility_decision`; `keyboards/booking.py` | Hide offline/unbookable services; category pagination; empty/error text from Telegram. |
| Staff-first path | `booking_flow.py`: `_load_staff`, `_show_staff`, `_filter_staff_with_future_availability`, `_ensure_filtered_staff_source` | Staff filtered by active/online bookable service compatibility and future slots. |
| Date/slot path | `booking_flow.py`: `_load_available_dates`, `_load_slots`, `_show_date_picker`, `_show_slots`, `_slot_is_future_for_company_day` | Branch timezone; past/busy slots hidden; stale callback refresh/friendly text. |
| Final confirmation | `booking_flow.py`: `_build_summary_text`, `_send_confirmation_with_optional_photo`, `_revalidate_before_final_step`, create handlers; `core/action_locks.py` | Confirm card text/button parity, revalidate before create, action lock/double tap protection. |
| Create payload/comment/source | `booking_flow.py`: `BOOKING_COMMENT_PREFIX`, `_build_booking_bot_comment_tag`, `_append_lost_client_discount_comment`; `integrations/yclients/endpoints.py`; `db/telegram_attribution_repo.py` | Comment marker says Telegram; attribution saved; lost-client discount marker appended if origin says so. |
| Errors/rate limits/stale | `booking_flow.py`: `_send_yclients_error`, `_reply_booking_step_error`, diagnostics helpers; `integrations/yclients/errors.py` | Auth/rate-limit/server/network messages masked; developer diagnostics where Telegram sends them. |
| Cancel/reschedule/repeat from booking context | `my_bookings.py`, `integrations/yclients/endpoints.py` | Only available when record status/time/source allows. |

### C. My bookings

Telegram source: `telegram_reference/app/handlers/my_bookings.py`, `telegram_reference/app/integrations/yclients/endpoints.py`, `telegram_reference/app/db/booking_links_repo.py`, `telegram_reference/app/repositories/users.py`, `telegram_reference/app/core/action_locks.py`.

- Active list/history/detail use YClients records, local booking links and phone matching.
- Detail card formats status/date/time/master/services/price/address and buttons.
- Cancellation uses confirmation screen, YClients cancel endpoint, local log marker and duplicate protection.
- Reschedule uses valid dates/slots, YClients reschedule payload, success card, and restrictions by status/capability.
- Repeat uses old service/master where safe; fallback to booking hub where Telegram falls back.
- Empty states are explicit Russian+emoji screens, not generic errors.

### D. Notifications and auto-funnels

Telegram source: `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/core/booking_reminders.py`, repositories `booking_reminder_events.py`, `notification_attributions.py`, `notification_test_events.py`, `post_visit_feedback_events.py`, `cancellation_recovery_events.py`, `repeat_visit_events.py`, `birthday_funnel_events.py`, `lost_client_events.py`, `automation_settings.py`, `marketing_preferences.py`, `messaging.py`.

- Notification history screens: `notification_history_root_kb`, `history_list_kb`, search by phone/client, failed/recent/detail filters.
- Reminder tests: `dev_tests_kb`, `run_dev_test`; 48h and 2h messages are safe dev/admin tests and logged.
- Booking reminders: skip cancelled/deleted, branch timezone, dedup event rows, delivery error handling.
- Post-visit feedback: request/rating/comment/public review links, negative feedback admin alert and admin actions.
- Cancellation recovery: delayed recovery message, skip if future booking exists, dedup per record/client.
- Repeat visit: due scan, booking CTA, dedup and attribution.
- Birthday: birthday window, claim/book CTA, dedup and attribution.
- Lost clients: threshold scan, discount/booking CTA, dedup and attribution.
- Automation settings: module enable/disable/templates/delays where Telegram exposes them.

### E. Broadcasts / рассылки

Telegram source: `telegram_reference/app/handlers/notifications.py`, `telegram_reference/app/keyboards/staff.py`, `telegram_reference/app/repositories/broadcasts.py`, `telegram_reference/app/repositories/messaging.py`, `telegram_reference/app/core/navigation.py`, `telegram_reference/tests/integration/test_one_time_broadcast_yclients_audiences.py`, `telegram_reference/tests/integration/test_broadcast_sender_logic.py`.

- Broadcast root: `broadcast_root_kb`, access via developer/admin/manager.
- One-time broadcast: `open_one_time`, `pick_aud`, `input_text`, `photo_choice`, `photo_upload`, `_show_preview`, `send_confirmed`.
- Audiences: self, all users/YClients audience, and segment-derived audiences.
- Safety: preview + confirm, cancel/abort, sending-in-progress guard, no duplicate recipients, blocked/failed skipped with report.
- History/effectiveness: broadcast history root/list and effectiveness screen if available.
- Media: photo step exists in Telegram; MAX must document closest supported attachment behavior.

### F. Segments

Telegram source: `telegram_reference/app/handlers/notifications.py` segment section around `segment_root_kb`, `_render_live_yclients_segment_detail`, `open_segments`, `refresh_segments`, `open_master_segment_picker`, `open_service_category_segment_picker`, `open_segment_detail`, `segment_use_for_broadcast`.

Segments to port 1:1: all clients, active 7/30/90, lost 30/60/90, no future booking, cancelled/no-show, by master, by service/category, birthday soon, bad rating segment unavailable/hidden if Telegram does so.

### G. Admin / manager / developer

- Role menus/permissions: `handlers/menu.py`, `keyboards/menu.py`, `keyboards/factory.py`, `core/permissions.py`, `core/staff_permissions.py`, `repositories/staff_roles.py`.
- Staff/personnel/role assignment: `handlers/staff/personnel.py`, `handlers/staff/panel.py`, `handlers/staff/role_onboarding.py`, `repositories/staff_audit.py`, `repositories/staff_action_logs.py`.
- YClients settings/check: `handlers/yclients_setup.py`, `repositories/yclients_settings.py`, `integrations/yclients/*`.
- Contacts/support editors: `repositories/contacts_override.py`, `repositories/support_settings.py`, settings handlers/screens.
- Master photos: `handlers/master_photos_settings.py`, `repositories/master_photos.py`.
- Statistics/reports/bookings today/clients directory: `handlers/statistics.py`, `handlers/reports.py`, `handlers/admin_bookings.py`, `handlers/clients_directory.py`, `db/reports_repo.py`.
- Developer diagnostics/tools: `handlers/dev.py`, `core/diagnostics_runtime.py`, `core/error_monitor.py`, `core/crash_monitor.py`, `repositories/diagnostics.py`.

### H. Technical safety

Telegram files: `core/action_locks.py`, `middlewares/antiflood.py`, `middlewares/callback_safety.py`, `middlewares/error_notify.py`, `core/safe_telegram.py`, `core/security.py`, `core/navigation.py`, `integrations/yclients/errors.py`.

Safety rules: action locks/idempotency, callback_data validation and length, retry/dedup, rate limits, notification history, platform attribution, token/payload/phone masking, branch timezone, no aiogram in MAX.

## 3. MAX current-state inventory

### A–C. Public flows, booking, my bookings

MAX has substantial partial/implemented flows in `max_barbershop_bot/flows/start.py`, `registration.py`, `menu.py`, `booking.py`, `my_bookings.py`, `contacts.py`, `support.py`, `masters.py`; services in `services/registration.py`, `services/booking.py`, `services/my_bookings.py`, `services/contacts.py`, `services/navigation.py`; UI in `ui/buttons.py`, `ui/texts.py`, `ui/screens.py`; tests include booking pagination/confirm payload/comment/my bookings runtime.

Known gaps: needs final Telegram parity verification of exact text/buttons/callback meaning, edge/stale behavior, role gates, history/repeat/reschedule completeness, and source marker wording (`MAX` instead of Telegram where platform attribution must differ).

### D–F. Notifications, broadcasts, segments/funnels

MAX has partial-to-advanced implementation:

- Notifications foundation: `services/notifications.py`, `repositories/notification_history.py`, `flows/notification_history.py`, `max_api/sender.py`, `tests/test_reminders_48h.py`.
- Reminders: `flows/booking_reminders.py`, `services/reminders.py`, `services/reminder_lifecycle.py`.
- Broadcasts: `flows/broadcasts.py`, `services/broadcasts.py`, `services/omnichannel_broadcasts.py`, `repositories/omnichannel_broadcasts.py`, `tests/test_max_broadcast_parity.py`, `tests/test_telegram_omnichannel_matching.py`, `tests/test_telegram_runtime_status.py`.
- Segments: `flows/client_segments.py`, `flows/lost_clients.py`, `services/client_segments.py`, `services/lost_clients.py`.
- Funnels: `flows/feedback.py`, `services/feedback.py`, `repositories/feedback.py`, `services/cancellation_recovery.py`, `repositories/cancellation_recovery_events.py`, `services/repeat_visit.py`, `repositories/repeat_visit_events.py`, `services/birthday_funnel.py`, `repositories/birthday_funnel_events.py`.

Known gaps: because implementation exists, future work should first audit exact parity versus Telegram. Highest-risk gaps are audience source, media support, delivery status semantics, automation settings UI, admin test UX, Telegram-runtime/omnichannel bridging, and exact segment count definitions.

### G–H. Admin and safety

MAX has current implementations in `flows/settings.py`, `flows/yclients_settings.py`, `flows/staff.py`, `flows/master_photos.py`, `flows/statistics.py`, `flows/admin_bookings.py`, `flows/clients_directory.py`, `services/developer_diagnostics.py`, `core/error_handler.py`, `core/permissions.py`, `core/action_locks.py`, `repositories/staff_roles.py`, `repositories/settings.py`, `repositories/settings_audit.py`, `repositories/audit_log.py`, `repositories/diagnostics.py`.

Known gaps: exact role matrix, protected developer behavior, contacts/support editors, master photo screen parity, diagnostics masking parity, and statistics/bookings/clients directory formatting need targeted audit PRs.

## 4. Gap matrix summary

| Area | Telegram feature | Telegram files | MAX status | MAX files | Gap type | Business importance | Priority | PR |
|---|---|---|---|---|---|---|---|---|
| Notifications | delivery/history/dedup foundation | `handlers/notifications.py`, `repositories/*events.py` | partial | `services/notifications.py`, `repositories/notification_history.py` | missing parity proof/status semantics | critical selling feature | P0 | PR-001 |
| Notifications | 48h/2h reminders | `core/booking_reminders.py`, `handlers/notifications.py` | partial | `services/reminders.py`, `flows/booking_reminders.py` | wrong text/buttons/timezone possible | critical selling feature | P0 | PR-002/003 |
| Broadcasts | self-test and preview/confirm | `handlers/notifications.py` | partial | `flows/broadcasts.py`, `services/broadcasts.py` | safety/admin UX parity | critical selling feature | P0 | PR-004/005 |
| Booking | confirm/dedup/create attribution | `handlers/booking_flow.py` | partial/implemented | `flows/booking.py`, `services/booking.py` | parity proof, platform marker | core booking | P0 | PR-006 |
| My bookings | active/detail/cancel | `handlers/my_bookings.py` | partial/implemented | `flows/my_bookings.py`, `services/my_bookings.py` | text/buttons/status/ownership | core booking | P0 | PR-007/008/009 |
| Settings | YClients status/check | `handlers/yclients_setup.py` | partial | `flows/yclients_settings.py` | masking/error wording | admin support | P0 | PR-010/011 |
| Broadcasts | all YClients audience | `handlers/notifications.py`, integration tests | partial | `services/omnichannel_broadcasts.py` | audience source/dedup/delivery | critical selling feature | P1 | PR-012 |
| Segments | segment builders | `handlers/notifications.py` | partial | `services/client_segments.py`, `flows/client_segments.py` | definitions/counts/handoff | critical selling feature | P1 | PR-013..019 |
| Funnels | feedback/recovery/repeat/birthday/lost | `handlers/notifications.py`, event repos | partial | corresponding MAX services/repos/flows | missing admin UX/tests/settings | critical selling feature | P1 | PR-020..029 |
| Admin | funnel/broadcast settings/tests | `handlers/notifications.py` | partial/missing | `flows/settings.py`, `flows/broadcasts.py` | missing tests/previews | critical selling feature | P1 | PR-030..033 |
| Public UX | start/registration/menu/contacts/support | public handlers | partial/implemented | MAX public flows | text/buttons/role gates | core/admin support | P2 | PR-034..039 |
| Admin completeness | staff/settings/master photos/stats/clients | admin handlers | partial | MAX admin flows/services | formatting/permissions | admin support | P2 | PR-040..048 |
| Polish | loyalty/referrals/dev logs/effectiveness/media edge | loyalty/dev/broadcast files | missing/partial | MAX mixed | optional/transport limits | polish | P3 | PR-049..052 |

## 5. New priority strategy

### P0 — Demo/sales critical and core reliability

Finish only what blocks a convincing demo: notification delivery/history/dedup; 48h/2h reminders; broadcast self-test and preview/confirmation safety; booking create/dedup parity; my bookings active/detail/cancel; YClients status/check; safe diagnostics.

### P1 — Commercial selling features

Broadcast to all eligible audience; segmented broadcasts; segment builders; delivery/dedup/skip/retry; feedback funnel; cancellation recovery; repeat visit; birthday; lost clients; admin tests/previews/settings around each automation.

### P2 — Admin completeness

Registration/menu/contacts/support exact polish; staff/personnel; role management; editors; master photos; YClients diagnostics; statistics; bookings today; clients directory.

### P3 — Polish/optional only if Telegram has it

Loyalty/referrals, developer logs/search, broadcast effectiveness analytics, media/GIF/video support, rare edge cases. Hide rather than invent if parity cannot be supported.

## 6. Prompt-ready backlog

> Universal acceptance for every PR below: PR output must include Telegram → MAX parity table, files changed, not ported and why, smoke checklist, commands/tests, risks/follow-up, and the conflict rule.

### PR-001 — Notification delivery/history/dedup foundation parity

Priority: P0  
Area: Notifications / Safety  
Business value: без единой истории и dedup нельзя продавать напоминания, рассылки и воронки.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/booking_reminder_events.py`
- `telegram_reference/app/repositories/notification_attributions.py`
- `telegram_reference/app/repositories/notification_test_events.py`
- `telegram_reference/app/repositories/marketing_preferences.py`
MAX target files:
- `max_barbershop_bot/services/notifications.py`
- `max_barbershop_bot/repositories/notification_history.py`
- `max_barbershop_bot/flows/notification_history.py`
- `max_barbershop_bot/db/sqlite.py`
- `tests/test_reminders_48h.py`

Scope:
- Audit and align status names, reserve/mark/skipped semantics, dedup keys, disabled/blocked/rate-limit handling, metadata masking.
Telegram behavior to port:
- texts: history/root/detail/filter texts from Telegram.
- buttons: recent/failed/detail/back/home.
- callbacks: same meaning; MAX payload names may differ but must map 1:1.
- business logic: reserve before send, mark result after send, skip duplicates.
- data source: local notification/event repositories.
- side effects: history rows and delivery rows.
- role access: admin/manager/developer only where Telegram restricts.
- timezone/dedup/safety: branch timezone, phone/token masking, no duplicate business notification.
Do NOT touch:
- Booking/funnel message templates except fields required for history.
Acceptance criteria:
- MAX history/delivery table semantics are documented in PR parity table.
- Duplicate automatic notification for same business key is skipped exactly like Telegram.
Tests:
- Targeted notification/reminder tests only.
Manual smoke checklist:
1. Open history root as admin.
2. Open recent, failed, detail.
3. Attempt duplicate reserve/send in a dev test and confirm one active row.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-002 — Booking reminder 48h parity

Priority: P0  
Area: Notifications / Reminders  
Business value: ключевая selling-функция — клиент получает напоминание заранее.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/core/booking_reminders.py`
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/booking_reminder_events.py`
- `telegram_reference/tests/unit/test_reminders_templates.py`
MAX target files:
- `max_barbershop_bot/services/reminders.py`
- `max_barbershop_bot/flows/booking_reminders.py`
- `max_barbershop_bot/services/reminder_lifecycle.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_reminders_48h.py`

Scope:
- Align 48h schedule, text, CTA buttons, skipped cancelled/deleted records, dev/admin test.
Telegram behavior to port:
- texts/buttons/callbacks: 48h template and booking CTA meaning.
- business logic: timezone-aware due calculation, skip inactive records, dedup per record/type.
- data source: YClients records + notification history.
- side effects: history/delivery/test event.
- role access: dev/admin test access as Telegram.
Do NOT touch:
- 2h reminder except shared helper if unavoidable.
Acceptance criteria:
- Test 48h message text/buttons match Telegram except platform wording where required.
Tests:
- `pytest tests/test_reminders_48h.py -k 48h`
Manual smoke checklist:
1. Run safe 48h dev test.
2. Check history row.
3. Verify cancelled record is skipped.
Dependencies:
- PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-003 — Booking reminder 2h parity

Priority: P0  
Area: Notifications / Reminders  
Business value: день-в-день напоминание снижает no-show.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/core/booking_reminders.py`
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/booking_reminder_events.py`
MAX target files:
- `max_barbershop_bot/services/reminders.py`
- `max_barbershop_bot/flows/booking_reminders.py`
- `max_barbershop_bot/services/reminder_lifecycle.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_reminders_48h.py`

Scope:
- Align 2h schedule, text, CTA, skip/dedup, safe test.
Telegram behavior to port:
- Same as PR-002 for `reminder_2h`/2h Telegram semantics.
Do NOT touch:
- Broadcast/funnels.
Acceptance criteria:
- 2h safe test logs delivery and history exactly once.
Tests:
- `pytest tests/test_reminders_48h.py -k 2h`
Manual smoke checklist:
1. Run safe 2h dev test.
2. Check message text and history.
Dependencies:
- PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-004 — Broadcast self-test safety parity

Priority: P0  
Area: Broadcasts / Safety  
Business value: владелец может безопасно показать рассылку без риска отправки клиентам.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/broadcasts.py`
- `telegram_reference/app/keyboards/staff.py`
MAX target files:
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/services/broadcasts.py`
- `max_barbershop_bot/repositories/omnichannel_broadcasts.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_max_broadcast_parity.py`

Scope:
- Self audience only: draft text, optional supported attachment note, preview, confirm, send to actor.
Telegram behavior to port:
- texts/buttons/callbacks: one-time broadcast root, self audience, preview/confirm/cancel/report.
- business logic: self-send never touches YClients audience and never sends to others.
- role access: developer/admin/manager same as Telegram.
- safety: sending-in-progress guard and cancellation.
Do NOT touch:
- all-client broadcast.
Acceptance criteria:
- Self broadcast creates one run and one delivery to actor only.
Tests:
- `pytest tests/test_max_broadcast_parity.py -k self`
Manual smoke checklist:
1. Open broadcast root.
2. Choose self audience.
3. Enter text → preview → confirm.
4. Verify report says one recipient.
Dependencies:
- PR-001 recommended.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-005 — Broadcast preview/confirmation guard parity

Priority: P0  
Area: Broadcasts / Safety  
Business value: массовые отправки продаются только если есть защита от случайной отправки.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/core/navigation.py`
MAX target files:
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `max_barbershop_bot/services/navigation.py`
- `tests/test_max_broadcast_parity.py`

Scope:
- Align draft validation, preview, cancel/abort, back/home/stale behavior before any non-self sending.
Telegram behavior to port:
- text cannot be empty; preview shows exact message; confirm button required; old/stale buttons friendly.
- media: if MAX cannot match Telegram photo support, document closest equivalent and why.
Do NOT touch:
- recipient builders.
Acceptance criteria:
- No send path exists without preview + explicit confirm.
Tests:
- Targeted broadcast parity tests.
Manual smoke checklist:
1. Empty text rejected.
2. Preview opens.
3. Cancel returns to broadcast root.
4. Old confirm does not double-send.
Dependencies:
- PR-004.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-006 — Booking create/dedup/attribution parity check

Priority: P0  
Area: Booking / Safety  
Business value: запись должна создаваться ровно один раз и быть видна в YClients с правильным источником.  
Current status in MAX: implemented but needs parity check.  
Telegram source files:
- `telegram_reference/app/handlers/booking_flow.py`
- `telegram_reference/app/core/action_locks.py`
- `telegram_reference/app/db/telegram_attribution_repo.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/flows/booking.py`
- `max_barbershop_bot/services/booking.py`
- `max_barbershop_bot/core/action_locks.py`
- `max_barbershop_bot/repositories/platform_attribution.py`
- `tests/test_booking_confirm_duplicate.py`
- `tests/test_booking_create_payload_comment_attribution.py`

Scope:
- Confirm double-tap lock, final revalidation, create payload, comment/source marker, local attribution.
Telegram behavior to port:
- text/buttons/callbacks: final confirm and success screen meaning.
- business logic: only one YClients record, friendly duplicate response.
- side effects: attribution/link rows.
- safety: platform marker should be MAX equivalent, not Telegram lie.
Do NOT touch:
- service/date selection screens.
Acceptance criteria:
- Double confirm creates max one record; attribution row stored; payload fields match Telegram semantics.
Tests:
- `pytest tests/test_booking_confirm_duplicate.py tests/test_booking_create_payload_comment_attribution.py`
Manual smoke checklist:
1. Create booking and tap confirm 2–3 times quickly.
2. Verify YClients comment/source marker.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-007 — My bookings active list parity

Priority: P0  
Area: My bookings  
Business value: клиент и владелец видят, что запись реально управляется ботом.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/my_bookings.py`
- `telegram_reference/app/db/booking_links_repo.py`
- `telegram_reference/app/repositories/users.py`
MAX target files:
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/services/my_bookings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_my_bookings_runtime.py`

Scope:
- Active list root only: ownership, empty state, list/card pagination.
Telegram behavior to port:
- texts/buttons/status filters; data source YClients + local links + phone/client id.
Do NOT touch:
- detail/cancel/reschedule/repeat.
Acceptance criteria:
- No bookings, future booking, cancelled booking display same decisions as Telegram.
Tests:
- `pytest tests/test_my_bookings_runtime.py -k active`
Manual smoke checklist:
1. Open my bookings with no bookings.
2. Open with one future booking.
3. Cancelled booking absent/marked as Telegram.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-008 — My booking detail parity

Priority: P0  
Area: My bookings  
Business value: карточка записи должна быть доверительной и полной.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/my_bookings.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/services/my_bookings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_my_bookings_runtime.py`

Scope:
- Detail screen and visible buttons only.
Telegram behavior to port:
- card text/status/date/master/services/price/address and cancel/reschedule/repeat visibility.
Do NOT touch:
- action implementations.
Acceptance criteria:
- Future/past/cancelled detail card matches Telegram behavior.
Tests:
- `pytest tests/test_my_bookings_runtime.py -k detail`
Manual smoke checklist:
1. Open active booking detail.
2. Open past/cancelled if available.
Dependencies:
- PR-007.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-009 — My booking cancellation parity

Priority: P0  
Area: My bookings / Safety  
Business value: клиент может отменить запись без звонка, безопасно и без дублей.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/my_bookings.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
- `telegram_reference/app/core/action_locks.py`
MAX target files:
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/services/my_bookings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_my_bookings_runtime.py`

Scope:
- Cancel start/confirm/success/error and duplicate protection.
Telegram behavior to port:
- confirmation UX, YClients cancellation, marker/comment equivalent, friendly errors.
Do NOT touch:
- reschedule/repeat.
Acceptance criteria:
- Confirm once cancels; second confirm is harmless/friendly.
Tests:
- targeted my bookings cancellation tests.
Manual smoke checklist:
1. Cancel → back.
2. Cancel → confirm.
3. Confirm again.
Dependencies:
- PR-008.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-010 — YClients settings status parity

Priority: P0  
Area: Settings / YClients  
Business value: владелец должен видеть, что интеграция подключена и безопасно замаскирована.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/yclients_setup.py`
- `telegram_reference/app/repositories/yclients_settings.py`
- `telegram_reference/app/integrations/yclients/auth.py`
MAX target files:
- `max_barbershop_bot/flows/yclients_settings.py`
- `max_barbershop_bot/services/yclients_settings.py`
- `max_barbershop_bot/repositories/yclients_settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Entry/status screen only: saved/empty/masked state and navigation.
Telegram behavior to port:
- texts/buttons/status labels; role access; no secrets in UI/logs.
Do NOT touch:
- credential edit flow unless needed for status parity.
Acceptance criteria:
- Empty and saved settings screens match Telegram meaning.
Tests:
- targeted import/flow unit if present.
Manual smoke checklist:
1. Open as admin with empty settings.
2. Open with saved settings.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-011 — YClients connection check errors parity

Priority: P0  
Area: Settings / YClients / Diagnostics  
Business value: support can diagnose integration without leaking tokens.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/yclients_setup.py`
- `telegram_reference/app/integrations/yclients/errors.py`
- `telegram_reference/app/core/security.py`
MAX target files:
- `max_barbershop_bot/flows/yclients_settings.py`
- `max_barbershop_bot/services/yclients_settings.py`
- `max_barbershop_bot/integrations/yclients/exceptions.py`
- `max_barbershop_bot/services/developer_diagnostics.py`

Scope:
- Read-only check and error formatting: auth/rate-limit/server/network.
Telegram behavior to port:
- masked diagnostics, user-friendly Russian errors, developer details masked.
Do NOT touch:
- booking endpoints.
Acceptance criteria:
- Invalid credentials, rate limit and network errors render as Telegram equivalent.
Tests:
- targeted service tests or dry-run mocks.
Manual smoke checklist:
1. Run check with valid settings.
2. Run with invalid token/company.
Dependencies:
- PR-010.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-012 — Broadcast to all eligible YClients/MAX audience

Priority: P1  
Area: Broadcasts  
Business value: главная продаваемая функция — массовая рассылка клиентской базе.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/broadcasts.py`
- `telegram_reference/tests/integration/test_one_time_broadcast_yclients_audiences.py`
- `telegram_reference/tests/integration/test_broadcast_sender_logic.py`
MAX target files:
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/services/omnichannel_broadcasts.py`
- `max_barbershop_bot/repositories/omnichannel_broadcasts.py`
- `max_barbershop_bot/services/client_segments.py`
- `tests/test_max_broadcast_parity.py`
- `tests/test_telegram_omnichannel_matching.py`

Scope:
- All audience estimate/confirm/send/report using safe eligible recipients.
Telegram behavior to port:
- texts/buttons/callbacks: audience choice, estimate, confirm, report.
- business logic: YClients/local mapping audience, manual broadcast settings semantics, blocked skip, no duplicates.
- MAX limitation: send only to MAX-opened/mapped users unless omnichannel Telegram adapter is explicitly configured; document in not-ported.
Do NOT touch:
- segment-specific audiences.
Acceptance criteria:
- Estimate count, sent/skipped/failed report and dedup match Telegram semantics.
Tests:
- broadcast parity + omnichannel matching tests.
Manual smoke checklist:
1. Select all audience.
2. Preview estimate.
3. Confirm on small safe audience.
Dependencies:
- PR-004, PR-005.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-013 — Segment overview and refresh parity

Priority: P1  
Area: Segments / Broadcasts  
Business value: владелец видит готовые аудитории для продаж.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
MAX target files:
- `max_barbershop_bot/flows/client_segments.py`
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Segment root/overview/refresh and broadcast handoff placeholder.
Telegram behavior to port:
- `segment_root_kb`, overview text, refresh callbacks, stale/error/access denied texts.
Do NOT touch:
- Individual segment algorithms except summary fields needed for overview.
Acceptance criteria:
- Segment root mirrors Telegram ordering and role access.
Tests:
- targeted segment service/flow checks.
Manual smoke checklist:
1. Open segments as manager/admin/developer.
2. Refresh.
3. Open unknown/stale callback.
Dependencies:
- PR-012 can happen before/after; handoff final after PR-020.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-014 — Active clients segments 7/30/90 parity

Priority: P1  
Area: Segments  
Business value: быстрые продажи активной базе.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/flows/client_segments.py`

Scope:
- Active 7/30/90 calculations, detail screen, broadcast handoff.
Telegram behavior to port:
- source = YClients history; active windows/counts/list formatting exactly as Telegram.
Do NOT touch:
- lost/cancelled segments.
Acceptance criteria:
- Counts match Telegram definition on same mocked records.
Tests:
- targeted client segment tests.
Manual smoke checklist:
1. Open active 7.
2. Open active 30/90.
3. Start broadcast from segment to preview only.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-015 — Lost clients 30/60/90 segments parity

Priority: P1  
Area: Segments / Lost clients  
Business value: возврат ушедших клиентов — коммерчески важная воронка.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/lost_client_events.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/services/lost_clients.py`
- `max_barbershop_bot/flows/lost_clients.py`
- `max_barbershop_bot/repositories/birthday_funnel_events.py`

Scope:
- Lost thresholds 30/60/90 segment detail and broadcast handoff.
Telegram behavior to port:
- lost definition, future booking exclusion if Telegram excludes, reason/count formatting.
Do NOT touch:
- lost automation send; that is PR-027.
Acceptance criteria:
- Segment count/list matches Telegram.
Tests:
- targeted lost clients tests.
Manual smoke checklist:
1. Open lost 30/60/90.
2. Verify empty state.
3. Handoff to broadcast preview.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-016 — No future booking segment parity

Priority: P1  
Area: Segments  
Business value: простая аудитория для дозаписи.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/flows/client_segments.py`

Scope:
- Clients without future active booking.
Telegram behavior to port:
- future booking exclusion using YClients source and statuses exactly as Telegram.
Do NOT touch:
- broadcast sending.
Acceptance criteria:
- Clients with future bookings excluded.
Tests:
- targeted segment tests.
Manual smoke checklist:
1. Open segment.
2. Verify known future-booking client excluded.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-017 — Cancelled/no-show segment parity

Priority: P1  
Area: Segments  
Business value: возврат отменивших запись клиентов.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/tests/test_cancellation_segment_tracking.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/flows/client_segments.py`

Scope:
- Cancelled/no-show segment definition and detail.
Telegram behavior to port:
- status/deleted/no-show matching and count/list formatting.
Do NOT touch:
- cancellation recovery automation.
Acceptance criteria:
- Same records fall into segment as Telegram tests expect.
Tests:
- targeted cancellation segment tests if present/add minimal only if asked in PR.
Manual smoke checklist:
1. Open cancelled segment.
2. Verify cancelled known client present.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-018 — By master segment parity

Priority: P1  
Area: Segments  
Business value: персональные рассылки по мастерам.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/flows/client_segments.py`

Scope:
- Master picker, selected-master segment detail and broadcast handoff.
Telegram behavior to port:
- master list source, callbacks, count definition, empty states.
Do NOT touch:
- staff settings.
Acceptance criteria:
- Master picker and detail match Telegram.
Tests:
- targeted segment tests.
Manual smoke checklist:
1. Open master picker.
2. Pick master.
3. Handoff to preview.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-019 — By service/category and birthday-soon segment parity

Priority: P1  
Area: Segments  
Business value: продающие рассылки по интересам и датам рождения.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/flows/client_segments.py`

Scope:
- Service/category picker/detail and birthday-soon segment.
Telegram behavior to port:
- service/category source, callback meaning, birthday window/count/list formatting.
Do NOT touch:
- birthday automation send.
Acceptance criteria:
- Segment definitions match Telegram on same fixtures.
Tests:
- targeted segment tests.
Manual smoke checklist:
1. Open service/category picker.
2. Open birthday soon.
3. Verify empty/list states.
Dependencies:
- PR-013.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-020 — Segment → broadcast handoff parity

Priority: P1  
Area: Segments / Broadcasts  
Business value: сегменты становятся продаваемым инструментом рассылки.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/broadcasts.py`
MAX target files:
- `max_barbershop_bot/flows/client_segments.py`
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/services/omnichannel_broadcasts.py`

Scope:
- Use selected segment as broadcast audience through preview/confirm/report.
Telegram behavior to port:
- callbacks and state handoff from `segment_use_for_broadcast`/`_start_one_time_from_audience`.
Do NOT touch:
- segment algorithms.
Acceptance criteria:
- Segment recipients passed to broadcast without recalculating wrong audience.
Tests:
- broadcast parity tests with segment audience.
Manual smoke checklist:
1. Open any segment.
2. Press broadcast.
3. Preview shows segment audience estimate.
Dependencies:
- PR-012, PR-014..019 as relevant.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-021 — Broadcast delivery skip/retry/report parity

Priority: P1  
Area: Broadcasts / Notifications  
Business value: владелец видит честный результат, а бот не спамит дублями.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/broadcasts.py`
- `telegram_reference/tests/integration/test_broadcast_sender_logic.py`
MAX target files:
- `max_barbershop_bot/services/omnichannel_broadcasts.py`
- `max_barbershop_bot/repositories/omnichannel_broadcasts.py`
- `max_barbershop_bot/flows/broadcasts.py`

Scope:
- Delivery result statuses, blocked/rate-limited/failed skip, retry policy if Telegram has it, final report.
Telegram behavior to port:
- no duplicate recipient sends, report fields/status labels, failure reasons masked.
Do NOT touch:
- audience builders.
Acceptance criteria:
- Report counts and stored delivery rows match Telegram semantics.
Tests:
- broadcast sender logic parity tests.
Manual smoke checklist:
1. Simulate blocked user.
2. Simulate delivery error.
3. Confirm report counts.
Dependencies:
- PR-012.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-022 — Post-visit feedback request parity

Priority: P1  
Area: Funnels / Feedback  
Business value: сбор отзывов — продаваемая автоматизация после визита.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/feedback.py`
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/keyboards/feedback.py`
- `telegram_reference/app/repositories/post_visit_feedback_events.py`
- `telegram_reference/app/db/feedback_repo.py`
MAX target files:
- `max_barbershop_bot/flows/feedback.py`
- `max_barbershop_bot/services/feedback.py`
- `max_barbershop_bot/repositories/feedback.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Request, stars, comment, public review links, dedup.
Telegram behavior to port:
- exact Russian texts/buttons/callback meaning; skip/dedup per visit; history row.
Do NOT touch:
- admin negative alert reply/close if separate in PR-023.
Acceptance criteria:
- User feedback flow mirrors Telegram.
Tests:
- targeted feedback tests if present.
Manual smoke checklist:
1. Send feedback test.
2. Rate positive.
3. Open public review links.
Dependencies:
- PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-023 — Negative feedback admin alert parity

Priority: P1  
Area: Funnels / Feedback / Admin  
Business value: владелец быстро реагирует на негатив.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/keyboards/feedback.py`
- `telegram_reference/app/repositories/post_visit_feedback_events.py`
MAX target files:
- `max_barbershop_bot/flows/feedback.py`
- `max_barbershop_bot/services/feedback.py`
- `max_barbershop_bot/repositories/feedback.py`

Scope:
- Admin alert text/actions for negative comment and close/reply behavior if supported.
Telegram behavior to port:
- `_render_post_visit_admin_alert`, admin buttons, role access, dev test marker.
Do NOT touch:
- positive feedback request flow.
Acceptance criteria:
- Negative feedback triggers one masked admin alert.
Tests:
- targeted feedback/admin tests.
Manual smoke checklist:
1. Rate low.
2. Enter comment.
3. Verify admin alert and action buttons.
Dependencies:
- PR-022.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-024 — Cancellation recovery funnel parity

Priority: P1  
Area: Funnels / Recovery  
Business value: возвращает отменивших клиентов.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/cancellation_recovery_events.py`
MAX target files:
- `max_barbershop_bot/services/cancellation_recovery.py`
- `max_barbershop_bot/repositories/cancellation_recovery_events.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Delayed recovery message, booking CTA, skip/dedup.
Telegram behavior to port:
- skip if future booking exists, dedup per cancellation/client, attribution callback to booking.
Do NOT touch:
- cancelled segment screen.
Acceptance criteria:
- One cancellation produces max one recovery message when due.
Tests:
- targeted service tests.
Manual smoke checklist:
1. Create cancellation event.
2. Run scan/test.
3. Verify skip with future booking.
Dependencies:
- PR-001, PR-009.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-025 — Repeat visit funnel parity

Priority: P1  
Area: Funnels / Repeat visit  
Business value: автоматическая дозапись постоянных клиентов.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/repeat_visit_events.py`
- `telegram_reference/tests/unit/test_repeat_visit_text_selection.py`
MAX target files:
- `max_barbershop_bot/services/repeat_visit.py`
- `max_barbershop_bot/repositories/repeat_visit_events.py`
- `max_barbershop_bot/flows/booking.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Due scan, message text selection, booking CTA, dedup.
Telegram behavior to port:
- timing, text variants, future-booking skip, attribution to booking flow.
Do NOT touch:
- my booking repeat action.
Acceptance criteria:
- Same due clients and message variant as Telegram.
Tests:
- targeted repeat visit tests.
Manual smoke checklist:
1. Run repeat visit scan test.
2. Click booking CTA.
3. Verify dedup on second scan.
Dependencies:
- PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-026 — Birthday funnel parity

Priority: P1  
Area: Funnels / Birthday  
Business value: персональная акция ко дню рождения.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/birthday_funnel_events.py`
- `telegram_reference/tests/unit/test_birthday_funnel.py`
MAX target files:
- `max_barbershop_bot/services/birthday_funnel.py`
- `max_barbershop_bot/repositories/birthday_funnel_events.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Birthday due logic, claim/book CTA, dedup and test.
Telegram behavior to port:
- birthday window, message, callbacks, attribution, skip/dedup.
Do NOT touch:
- birthday segment detail.
Acceptance criteria:
- Due/skip behavior matches Telegram tests.
Tests:
- targeted birthday tests.
Manual smoke checklist:
1. Run birthday scan test.
2. Click claim/book.
3. Verify no duplicate.
Dependencies:
- PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-027 — Lost/inactive client funnel parity

Priority: P1  
Area: Funnels / Lost clients  
Business value: возврат спящих клиентов — важнейшая selling-фича.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/lost_client_events.py`
MAX target files:
- `max_barbershop_bot/services/lost_clients.py`
- `max_barbershop_bot/flows/lost_clients.py`
- `max_barbershop_bot/services/client_segments.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Lost-client automation send/test, discount/booking CTA, dedup, attribution.
Telegram behavior to port:
- threshold logic, future booking skip, message text, booking callback origin.
Do NOT touch:
- segment list definitions except shared helper.
Acceptance criteria:
- Scan sends only due lost clients once.
Tests:
- targeted lost client tests.
Manual smoke checklist:
1. Run lost scan.
2. Click CTA.
3. Verify booking comment includes lost-client marker equivalent if Telegram does.
Dependencies:
- PR-015, PR-001.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-028 — Funnel admin test hub parity

Priority: P1  
Area: Funnels / Admin  
Business value: на демо владелец запускает безопасные тесты каждой автоматизации.  
Current status in MAX: partial/missing.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/notification_test_events.py`
MAX target files:
- `max_barbershop_bot/flows/settings.py`
- `max_barbershop_bot/flows/booking_reminders.py`
- `max_barbershop_bot/flows/feedback.py`
- `max_barbershop_bot/flows/lost_clients.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Admin/dev safe test buttons for reminders and funnels, no real mass sends.
Telegram behavior to port:
- `dev_tests_kb`, `run_dev_test`, test markers, role access, history logging.
Do NOT touch:
- actual funnel due algorithms.
Acceptance criteria:
- Each commercial automation has safe test path like Telegram.
Tests:
- targeted tests around safe test events.
Manual smoke checklist:
1. Open tests hub as developer/admin if Telegram allows.
2. Run each safe test.
3. Verify messages/history/test rows.
Dependencies:
- PR-002..027 as relevant.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-029 — Automation settings modules parity

Priority: P1  
Area: Funnels / Settings  
Business value: owner can sell/manage automations, not just rely on hidden jobs.  
Current status in MAX: partial/missing.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/automation_settings.py`
- `telegram_reference/tests/unit/test_automation_settings_defaults.py`
- `telegram_reference/tests/unit/test_automation_booking_cta.py`
MAX target files:
- `max_barbershop_bot/flows/settings.py`
- `max_barbershop_bot/repositories/settings.py`
- `max_barbershop_bot/repositories/settings_audit.py`
- `max_barbershop_bot/services/settings_audit.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Automation root/module screens: enable/disable/status/templates/delay fields that Telegram exposes.
Telegram behavior to port:
- module names, defaults, edit prompts, audit logging, role access.
Do NOT touch:
- delivery engine.
Acceptance criteria:
- Settings shown and persisted like Telegram.
Tests:
- targeted settings tests.
Manual smoke checklist:
1. Open automation root.
2. Toggle one module.
3. Edit one allowed field and cancel.
Dependencies:
- PR-028 recommended.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-030 — Broadcast history/effectiveness parity baseline

Priority: P1  
Area: Broadcasts / Analytics  
Business value: владелец видит результат рассылок.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
- `telegram_reference/app/repositories/broadcasts.py`
MAX target files:
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/repositories/omnichannel_broadcasts.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Broadcast history root/list/detail; effectiveness only if Telegram has implemented metrics and MAX data supports them.
Telegram behavior to port:
- history filters/list/report labels; if effectiveness cannot be exact, hide or document closest equivalent.
Do NOT touch:
- sender logic.
Acceptance criteria:
- Recent broadcasts can be inspected with sent/skipped/failed counts.
Tests:
- targeted broadcast history tests.
Manual smoke checklist:
1. Send self test.
2. Open broadcast history.
3. Open detail/report.
Dependencies:
- PR-021.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-031 — Registration first-run parity

Priority: P2  
Area: Public user flows  
Business value: снижает потери на первом запуске, но не блокирует sales demo если уже работает.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/onboarding/registration.py`
- `telegram_reference/app/handlers/start.py`
- `telegram_reference/app/repositories/users.py`
- `telegram_reference/tests/unit/test_registration_and_role_guardrails.py`
MAX target files:
- `max_barbershop_bot/flows/registration.py`
- `max_barbershop_bot/flows/start.py`
- `max_barbershop_bot/services/registration.py`
- `max_barbershop_bot/repositories/users.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- First-run registration steps and validation.
Telegram behavior to port:
- name/phone/birthdate prompts, invalid input texts, consent/policy order if present.
Do NOT touch:
- role assignment.
Acceptance criteria:
- New user and invalid inputs match Telegram.
Tests:
- targeted registration tests.
Manual smoke checklist:
1. New user start.
2. Invalid name/phone/birthdate.
3. Complete registration.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-032 — Main menu role matrix parity

Priority: P2  
Area: Public/Admin menu  
Business value: operators see exactly the expected product surface.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/menu.py`
- `telegram_reference/app/keyboards/menu.py`
- `telegram_reference/app/keyboards/factory.py`
- `telegram_reference/app/core/permissions.py`
MAX target files:
- `max_barbershop_bot/flows/menu.py`
- `max_barbershop_bot/core/permissions.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- User/manager/admin/developer top menus, labels/order/visibility.
Telegram behavior to port:
- role gates for broadcast/settings/personnel/statistics/YClients/dev diagnostics.
Do NOT touch:
- inner feature screens.
Acceptance criteria:
- Menu matrix matches Telegram.
Tests:
- targeted menu/permission checks.
Manual smoke checklist:
1. Open menu as user.
2. Open as manager/admin/developer.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-033 — Public contacts screen parity

Priority: P2  
Area: Public user flows  
Business value: клиент может найти салон и связаться.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/sections.py`
- `telegram_reference/app/repositories/contacts_override.py`
- `telegram_reference/app/core/screens.py`
MAX target files:
- `max_barbershop_bot/flows/contacts.py`
- `max_barbershop_bot/services/contacts.py`
- `max_barbershop_bot/repositories/app_settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Public contacts only.
Telegram behavior to port:
- address/phone/hours/map buttons, override fallback, Back/Home.
Do NOT touch:
- contacts editor.
Acceptance criteria:
- Public contacts text/buttons match Telegram.
Tests:
- targeted flow smoke if available.
Manual smoke checklist:
1. Menu → contacts.
2. Press map/phone buttons.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-034 — Public support screen parity

Priority: P2  
Area: Public user flows  
Business value: пользователь получает помощь без ручного поиска контакта.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/sections.py`
- `telegram_reference/app/repositories/support_settings.py`
MAX target files:
- `max_barbershop_bot/flows/support.py`
- `max_barbershop_bot/repositories/support_settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Public support only.
Telegram behavior to port:
- text + support button, no raw duplicate link if Telegram removed it, Back/Home.
Do NOT touch:
- support editor.
Acceptance criteria:
- Support screen matches Telegram.
Tests:
- targeted flow smoke.
Manual smoke checklist:
1. Menu → support.
2. Press support button.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-035 — Booking hub/services/dates parity audit

Priority: P2  
Area: Booking  
Business value: core booking should remain exact after commercial work.  
Current status in MAX: implemented but needs parity check.  
Telegram source files:
- `telegram_reference/app/handlers/booking_flow.py`
- `telegram_reference/app/keyboards/booking.py`
MAX target files:
- `max_barbershop_bot/flows/booking.py`
- `max_barbershop_bot/services/booking.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- `tests/test_booking_pagination.py`
- `tests/test_yclients_rate_limit_handling.py`

Scope:
- Hub, service/category, staff, dates/slots, stale/back/home parity.
Telegram behavior to port:
- exact texts, buttons, callbacks meaning, YClients error/rate-limit handling.
Do NOT touch:
- final confirm/create; covered by PR-006.
Acceptance criteria:
- Flow parity table has no unreported gaps.
Tests:
- booking pagination/rate-limit targeted tests.
Manual smoke checklist:
1. Open each branch.
2. Select category/service/master/date/slot.
3. Press old slot callback.
Dependencies:
- PR-006 can happen before/after.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-036 — My booking reschedule parity

Priority: P2  
Area: My bookings  
Business value: convenience feature after cancellation/list reliability.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/my_bookings.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/services/my_bookings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Reschedule entry, valid dates/slots, confirmation, success/error.
Telegram behavior to port:
- status restrictions, prefill/fallback, YClients reschedule payload.
Do NOT touch:
- repeat booking.
Acceptance criteria:
- Reschedule mirrors Telegram restrictions and texts.
Tests:
- targeted my bookings reschedule tests.
Manual smoke checklist:
1. Detail → reschedule.
2. Pick date/slot.
3. Confirm.
Dependencies:
- PR-008.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-037 — My booking repeat parity

Priority: P2  
Area: My bookings / Booking  
Business value: fast repeat visit booking from history.  
Current status in MAX: partial/implemented.  
Telegram source files:
- `telegram_reference/app/handlers/my_bookings.py`
- `telegram_reference/app/handlers/booking_flow.py`
MAX target files:
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/flows/booking.py`
- `max_barbershop_bot/services/my_bookings.py`

Scope:
- Repeat from detail/history and prefill handoff.
Telegram behavior to port:
- service/master prefill or safe fallback exactly as Telegram.
Do NOT touch:
- repeat visit automation.
Acceptance criteria:
- Repeat starts booking with same prefill/fallback behavior.
Tests:
- targeted my bookings repeat tests.
Manual smoke checklist:
1. Open past booking.
2. Press repeat.
3. Verify preselected flow/fallback.
Dependencies:
- PR-035.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-038 — Staff/personnel list parity

Priority: P2  
Area: Admin / Staff  
Business value: owner manages operators confidently.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/staff/personnel.py`
- `telegram_reference/app/keyboards/staff.py`
- `telegram_reference/app/repositories/staff_roles.py`
MAX target files:
- `max_barbershop_bot/flows/staff.py`
- `max_barbershop_bot/repositories/staff_roles.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Personnel list only.
Telegram behavior to port:
- role labels, empty state, protected developer label, access gates.
Do NOT touch:
- role assignment.
Acceptance criteria:
- Staff list matches Telegram for admin/developer.
Tests:
- targeted staff tests.
Manual smoke checklist:
1. Open staff list as admin.
2. Open as developer.
Dependencies:
- PR-032.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-039 — Role assignment permissions parity

Priority: P2  
Area: Admin / Staff  
Business value: prevents accidental privilege escalation.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/staff/personnel.py`
- `telegram_reference/app/core/staff_permissions.py`
- `telegram_reference/app/repositories/staff_audit.py`
MAX target files:
- `max_barbershop_bot/flows/staff.py`
- `max_barbershop_bot/core/permissions.py`
- `max_barbershop_bot/repositories/staff_roles.py`
- `max_barbershop_bot/repositories/audit_log.py`

Scope:
- Assign-role flow and permission matrix.
Telegram behavior to port:
- who can assign manager/admin/developer, protected developer guard, audit log.
Do NOT touch:
- personnel list except integration.
Acceptance criteria:
- Admin cannot silently assign developer if Telegram forbids; developer protections match.
Tests:
- targeted staff permission tests.
Manual smoke checklist:
1. Admin tries developer role.
2. Developer assigns allowed role.
Dependencies:
- PR-038.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-040 — Contacts settings editor parity

Priority: P2  
Area: Admin / Settings  
Business value: owner can maintain public contact content.  
Current status in MAX: partial/missing.  
Telegram source files:
- `telegram_reference/app/repositories/contacts_override.py`
- `telegram_reference/tests/unit/test_step4_contacts_support_settings_personnel.py`
MAX target files:
- `max_barbershop_bot/flows/settings.py`
- `max_barbershop_bot/flows/contacts.py`
- `max_barbershop_bot/repositories/app_settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Address/phone/hours/maps edit, preview, reset.
Telegram behavior to port:
- validation, persistence, preview/reset to YClients/default, role gates.
Do NOT touch:
- public contacts except preview integration.
Acceptance criteria:
- Editor screens match Telegram.
Tests:
- targeted settings tests.
Manual smoke checklist:
1. Edit phone/address.
2. Preview.
3. Reset.
Dependencies:
- PR-033.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-041 — Support settings editor parity

Priority: P2  
Area: Admin / Settings  
Business value: owner controls support CTA.  
Current status in MAX: partial/missing.  
Telegram source files:
- `telegram_reference/app/repositories/support_settings.py`
- `telegram_reference/tests/unit/test_support_settings_keyboard.py`
- `telegram_reference/tests/unit/test_support_settings_home_fsm.py`
MAX target files:
- `max_barbershop_bot/flows/settings.py`
- `max_barbershop_bot/flows/support.py`
- `max_barbershop_bot/repositories/support_settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Username/link/description edit, preview, reset/cancel.
Telegram behavior to port:
- normalization, validation, FSM/back/home behavior, role gates.
Do NOT touch:
- public support except preview integration.
Acceptance criteria:
- Support editor matches Telegram tests/UX.
Tests:
- targeted support settings tests.
Manual smoke checklist:
1. Edit support link.
2. Preview.
3. Back/Home from input state.
Dependencies:
- PR-034.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-042 — Master photo settings parity

Priority: P2  
Area: Admin / Settings / Masters  
Business value: booking looks complete and visual.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/master_photos_settings.py`
- `telegram_reference/app/repositories/master_photos.py`
- `telegram_reference/tests/unit/test_master_photo_settings_home_callback.py`
MAX target files:
- `max_barbershop_bot/flows/master_photos.py`
- `max_barbershop_bot/services/master_photos.py`
- `max_barbershop_bot/repositories/master_photos.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- List/detail/upload/delete and booking photo usage parity.
Telegram behavior to port:
- validation, navigation, role gates, empty states.
Do NOT touch:
- booking core except photo attachment integration if required.
Acceptance criteria:
- Master photo management mirrors Telegram.
Tests:
- targeted master photo tests.
Manual smoke checklist:
1. Open list.
2. Upload/replace/delete photo.
3. Open booking with selected master.
Dependencies:
- PR-035.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-043 — Statistics summary parity

Priority: P2  
Area: Admin / Statistics  
Business value: owner sees operational value.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/statistics.py`
- `telegram_reference/app/keyboards/statistics.py`
- `telegram_reference/app/db/reports_repo.py`
MAX target files:
- `max_barbershop_bot/flows/statistics.py`
- `max_barbershop_bot/services/statistics.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Summary by period and empty/error states.
Telegram behavior to port:
- period buttons, metric names, formatting, role access, timezone.
Do NOT touch:
- broadcast effectiveness.
Acceptance criteria:
- Summary output matches Telegram on same data.
Tests:
- targeted statistics tests.
Manual smoke checklist:
1. Open statistics.
2. Switch periods.
3. Empty data state.
Dependencies:
- PR-032.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-044 — Admin bookings today parity

Priority: P2  
Area: Admin / Bookings  
Business value: operator can quickly view today’s load.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/admin_bookings.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/flows/admin_bookings.py`
- `max_barbershop_bot/services/admin_bookings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Today list only.
Telegram behavior to port:
- cards/status/source from YClients, role gate, timezone, empty/errors.
Do NOT touch:
- admin quick actions/search filters.
Acceptance criteria:
- Today list matches Telegram.
Tests:
- targeted admin bookings tests.
Manual smoke checklist:
1. Open bookings today.
2. Verify status/card formatting.
Dependencies:
- PR-011.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-045 — Clients directory phone search parity

Priority: P2  
Area: Admin / Clients directory  
Business value: staff can find client quickly.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/clients_directory.py`
- `telegram_reference/app/integrations/yclients/endpoints.py`
MAX target files:
- `max_barbershop_bot/flows/clients_directory.py`
- `max_barbershop_bot/services/yclients_context.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Phone search prompt/results/card.
Telegram behavior to port:
- normalization/masking/result limit/card formatting/access.
Do NOT touch:
- name search.
Acceptance criteria:
- Phone search matches Telegram.
Tests:
- targeted clients directory tests.
Manual smoke checklist:
1. Search valid phone.
2. Search invalid/empty phone.
Dependencies:
- PR-011.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-046 — Clients directory name search parity

Priority: P2  
Area: Admin / Clients directory  
Business value: staff can find client when phone is unknown.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/handlers/clients_directory.py`
MAX target files:
- `max_barbershop_bot/flows/clients_directory.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Name search prompt/results/card and too-many-results behavior.
Telegram behavior to port:
- result selection, masking, limits, errors.
Do NOT touch:
- phone search.
Acceptance criteria:
- Name search matches Telegram.
Tests:
- targeted clients directory tests.
Manual smoke checklist:
1. Search common name.
2. Search exact name.
3. Open result card.
Dependencies:
- PR-045.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-047 — Safe diagnostics/error delivery parity

Priority: P2  
Area: Safety / Developer  
Business value: production issues are debuggable without leaking data.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/core/error_monitor.py`
- `telegram_reference/app/core/crash_monitor.py`
- `telegram_reference/app/core/diagnostics_runtime.py`
- `telegram_reference/app/middlewares/error_notify.py`
- `telegram_reference/app/core/security.py`
MAX target files:
- `max_barbershop_bot/core/error_handler.py`
- `max_barbershop_bot/services/developer_diagnostics.py`
- `max_barbershop_bot/repositories/diagnostics.py`
- `max_barbershop_bot/core/logging.py`

Scope:
- Generic user error, masked developer alert, repeated error suppression.
Telegram behavior to port:
- masked token/phone/payload, role-gated diagnostics, Russian generic user text.
Do NOT touch:
- feature-specific errors except formatting helpers.
Acceptance criteria:
- Safe test error shows generic user text and masked dev alert.
Tests:
- targeted diagnostics tests.
Manual smoke checklist:
1. Trigger safe test error.
2. Confirm no token/phone leak.
Dependencies:
- none.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-048 — Back/Home/stale callback global parity

Priority: P2  
Area: Safety / Navigation  
Business value: predictable UX reduces support and broken demos.  
Current status in MAX: partial.  
Telegram source files:
- `telegram_reference/app/core/navigation.py`
- `telegram_reference/app/middlewares/callback_safety.py`
- `telegram_reference/tests/unit/test_navigation_back_home_consistency.py`
MAX target files:
- `max_barbershop_bot/services/navigation.py`
- `max_barbershop_bot/core/router.py`
- `max_barbershop_bot/core/payloads.py`
- `max_barbershop_bot/flows/booking.py`
- `max_barbershop_bot/flows/my_bookings.py`
- `max_barbershop_bot/flows/broadcasts.py`

Scope:
- Core screens only: booking entry, contacts, support, settings, my bookings root, broadcast root.
Telegram behavior to port:
- Back/Home stack behavior, unknown/stale callback friendly response, callback payload validation.
Do NOT touch:
- Deep feature logic.
Acceptance criteria:
- Navigation behavior matches Telegram for core screens.
Tests:
- targeted navigation tests.
Manual smoke checklist:
1. Open each core screen → Back/Home.
2. Press old callback.
Dependencies:
- after most P0/P1 flow screens exist.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-049 — Loyalty/referrals parity decision

Priority: P3  
Area: Loyalty / Referrals  
Business value: optional unless owner sells loyalty module.  
Current status in MAX: unknown/missing.  
Telegram source files:
- `telegram_reference/app/handlers/loyalty_mvp.py`
- `telegram_reference/app/handlers/loyalty/operations.py`
- `telegram_reference/app/handlers/loyalty/history.py`
- `telegram_reference/app/core/loyalty.py`
- `telegram_reference/app/repositories/referrals.py`
- `telegram_reference/app/repositories/loyalty_*`
MAX target files:
- `max_barbershop_bot/flows/menu.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`
- future MAX loyalty files only after owner decision.

Scope:
- Audit only: port or hide. No placeholder UX.
Telegram behavior to port:
- If approved, split into separate small PRs for balance, operations, history, referrals.
Do NOT touch:
- Existing menu unless hiding/showing per owner decision.
Acceptance criteria:
- Decision documented with Telegram feature list and MAX targets.
Tests:
- none unless code changed in future PR.
Manual smoke checklist:
1. Verify menu does not show broken loyalty placeholder.
Dependencies:
- PR-032.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-050 — Developer logs/user event search parity

Priority: P3  
Area: Developer tools  
Business value: useful for support, not first sales blocker.  
Current status in MAX: partial/unknown.  
Telegram source files:
- `telegram_reference/app/handlers/dev.py`
- `telegram_reference/app/repositories/diagnostics.py`
- `telegram_reference/app/core/diagnostics_runtime.py`
MAX target files:
- `max_barbershop_bot/services/developer_diagnostics.py`
- `max_barbershop_bot/repositories/diagnostics.py`
- `max_barbershop_bot/flows/settings.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Developer-only logs/search/export if Telegram has it.
Telegram behavior to port:
- masked output, role-gated developer only, pagination/search behavior.
Do NOT touch:
- normal admin diagnostics.
Acceptance criteria:
- Developer tools hidden from non-developers.
Tests:
- targeted diagnostics permission tests.
Manual smoke checklist:
1. Open as developer.
2. Try as admin/manager.
Dependencies:
- PR-047.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-051 — Media support for broadcasts parity decision

Priority: P3  
Area: Broadcasts / Media  
Business value: richer campaigns if MAX transport supports it.  
Current status in MAX: partial/unknown.  
Telegram source files:
- `telegram_reference/app/handlers/notifications.py`
MAX target files:
- `max_barbershop_bot/flows/broadcasts.py`
- `max_barbershop_bot/max_api/client.py`
- `max_barbershop_bot/max_api/models.py`
- `max_barbershop_bot/ui/buttons.py`
- `max_barbershop_bot/ui/texts.py`

Scope:
- Audit photo/media support parity; implement only closest equivalent if MAX API supports it.
Telegram behavior to port:
- photo add/skip/upload/preview/send; document unsupported GIF/video if any.
Do NOT touch:
- text-only broadcast engine unless attachment metadata needs alignment.
Acceptance criteria:
- Either exact supported media flow or documented hidden/disabled media path.
Tests:
- targeted media payload tests if code changes.
Manual smoke checklist:
1. Try photo broadcast self-test.
2. Verify preview and report.
Dependencies:
- PR-004, PR-005, PR-012.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.

### PR-052 — Rare edge cases final parity sweep

Priority: P3  
Area: Cross-cutting polish  
Business value: final QA before claiming 1:1 parity.  
Current status in MAX: unknown.  
Telegram source files:
- `telegram_reference/app/handlers/`
- `telegram_reference/app/keyboards/`
- `telegram_reference/app/services/`
- `telegram_reference/app/repositories/`
- `telegram_reference/app/core/`
- `telegram_reference/app/integrations/yclients/`
MAX target files:
- `max_barbershop_bot/flows/`
- `max_barbershop_bot/services/`
- `max_barbershop_bot/repositories/`
- `max_barbershop_bot/ui/`
- `max_barbershop_bot/core/`
- `max_barbershop_bot/integrations/yclients/`
- `tests/`

Scope:
- Final targeted audit after PR-001..051, not a broad rewrite.
Telegram behavior to port:
- Any remaining text/button/callback/safety mismatch found by code inventory.
Do NOT touch:
- New features not present in Telegram.
Acceptance criteria:
- Updated gap matrix has no unknowns for commercial/core/admin flows.
Tests:
- Targeted tests for changed files only.
Manual smoke checklist:
1. Run manual QA priority checklist.
2. Execute core booking + reminder + broadcast + funnel demo path.
Dependencies:
- after all prior PRs selected by owner.
Conflict rule:
If Telegram and current MAX conflict, report first and do not decide silently.
