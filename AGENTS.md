# AGENTS.md

> Единый обязательный регламент для Codex и других AI-агентов при работе над `ilya-boooots/max-barbershop-bot`.
> Этот файл является главным набором инструкций проекта. При противоречии с обычным текстом задачи применяются правила этого файла. При конфликте с явным решением владельца проекта агент останавливается и запрашивает подтверждение.

## Как использовать этот файл

- Файл должен находиться в корне репозитория под именем `AGENTS.md`.
- Перед каждой задачей агент обязан прочитать этот файл и соответствующий пункт `docs/max_telegram_parity_plan_v2.md`.
- `telegram_reference/` нельзя изменять.
- Один PR закрывает только один небольшой, проверяемый scope.
- Ни один пункт не считается завершённым без runtime-тестов, реального GitHub PR и проверки фактического `main` после merge.

---

# Часть I. Обязательные краткие правила

## Назначение

Этот файл содержит обязательные правила для Codex при работе над `ilya-boooots/max-barbershop-bot`.

## Источники истины

1. `telegram_reference/` — активный эталон UX и бизнес-логики.
2. Официальная документация MAX API — источник истины для transport, методов, полей и ограничений MAX.
3. Документация YClients и существующий integration-код — источник истины для YClients auth, endpoints, payload и response.
4. `docs/max_telegram_parity_plan_v2.md` — scope, dependencies, target files и acceptance каждого PR.
5. `max_barbershop_bot/` — текущая архитектура и фактическое состояние MAX, но не источник продуктового поведения при расхождении с Telegram.

## Жёсткие правила

- Не изменять `telegram_reference/`.
- Не импортировать `aiogram` в `max_barbershop_bot`.
- Один PR = один маленький экран, handler, service, repository или один шаг flow.
- Перед coding открыть exact Telegram reference files из plan.
- До изменений вывести parity-аудит:
  `требование | Telegram | MAX до | gap | production change | executable test`.
- Не придумывать UX, кнопки, тексты, роли, callbacks, бизнес-правила и архитектуру.
- Не делать “улучшения”, “адаптации”, “оптимизации” и несвязанный refactor.
- Менять только exact allowed files.
- Если нужен другой production file — STOP и доклад.
- Если Telegram reference и текущий MAX конфликтуют — STOP, не выбирать решение самостоятельно.
- `current_screen`, текст и keyboard должны описывать один и тот же экран.
- Back и Home являются частью parity.
- Stale/malformed callback не должен выбирать другую сущность.
- Повторный callback не должен повторять mutation.
- Не показывать пользователю token, Authorization, raw response, raw payload, traceback или внутреннюю диагностику.
- Для бизнес-времени использовать timezone филиала.
- Runtime-тест должен выполнять реальный handler/renderer. Source-string test — только дополнительное доказательство.
- Локальный commit или `make_pr` metadata не доказывают наличие GitHub PR. Нужны реальный URL, number, base, head и SHA.

## Обязательные проверки

```bash
python -m compileall max_barbershop_bot
python -m pytest <focused_test_files> -q
python -m pytest tests -k "<focused expression>" -q
python -m pytest tests -k "<neighbor regression expression>" -q
rg "from aiogram|import aiogram" max_barbershop_bot || true
rg "Authorization|partner_token|user_token|raw_response|response_body|traceback" <changed production files> || true
git diff --check
git diff --name-only
git diff --stat
git status --short --branch
```

## STOP-условия

- dirty working tree до начала;
- missing reference/target file;
- dependency PR отсутствует в текущем HEAD;
- Telegram/MAX product conflict;
- требуется schema migration, но её нет в scope;
- требуется новый framework/dependency;
- требуется forbidden production file;
- MAX/YClients contract неоднозначен;
- обязательные tests падают из-за изменения;
- diff содержит unrelated files;
- обнаружена утечка secret;
- mutation может выполняться дважды;
- реальный PR невозможно создать, хотя задача требует PR.

## Финальный отчёт

Обязательно указать:

- Telegram → MAX parity table;
- production files changed;
- точные test commands и counts;
- что не менялось;
- что не перенесено и почему;
- smoke checklist;
- real PR URL/number/head SHA;
- оставшиеся gaps.

Фразу `No known unreported gaps remain inside scope.` писать только после прохождения всех focused checks и при наличии named executable test для каждой строки parity.

---

# Часть 2. Полные правила проекта

## Статусы правил

- **Подтверждено** — следует из project instructions, plan, Telegram reference, MAX code или зафиксированных решений владельца.
- **Не установлено** — точного ответа в доступных материалах нет.
- **Рекомендация** — безопасное рабочее правило, но оно не зафиксировано как прежнее решение.

---

## 1. Проект

### Цель

**Подтверждено.** Проект — коммерческий бот для барбершопов, интегрированный с YClients и реализованный для Telegram и MAX. Конечный продукт должен быть пригоден для продажи нескольким клиентам как пакет Telegram + MAX.

### Роль Telegram

**Подтверждено.** Telegram-бот — эталон поведения:

- тексты и emoji;
- экраны;
- кнопки;
- callback-семантика;
- роли;
- переходы;
- YClients-бизнес-правила;
- notifications, broadcasts, funnels;
- error handling;
- side effects.

### Роль MAX

**Подтверждено.** MAX-бот — самостоятельная реализация того же продукта на MAX:

- без aiogram;
- со своим transport;
- со своей нормализацией updates;
- со своими MAX buttons/payloads;
- с сохранением Telegram UX и бизнес-логики.

### Функциональный паритет

**Подтверждено.** Паритет — это совпадение:

- доступа по ролям;
- входных callbacks;
- текстов и порядка кнопок;
- выбранных сущностей;
- state transitions;
- Back/Home;
- empty/stale/error behavior;
- YClients requests;
- локальных side effects;
- idempotency;
- результата повторных callbacks.

### Приоритет источников истины

1. Telegram reference — UX и бизнес-логика.
2. Официальная документация MAX API — transport MAX.
3. Документация YClients и существующий integration code — YClients.
4. `docs/max_telegram_parity_plan_v2.md` — scope и dependencies PR.
5. Текущий MAX code — архитектура и уже реализованные контракты.
6. Старый `.docx` — только исторический материал.

При конфликте Telegram и MAX агент останавливается и докладывает.

### Текущий статус

**Подтверждено.** Проект переносится маленькими parity PR. Уже закрывались booking, registration/menu, profile, contacts/support, reschedule, repeat и personnel list/card, включая postmerge fixes. Актуальный процент готовности нельзя определять по памяти — нужно читать текущий plan и main.

---

## 2. Репозитории и план

### Основной репозиторий

**Подтверждено:** `ilya-boooots/max-barbershop-bot`.

В нём:

- `max_barbershop_bot/` — production MAX;
- `telegram_reference/` — Telegram reference;
- `tests/` — tests;
- `docs/max_telegram_parity_plan_v2.md` — active plan.

**Не установлено:** отдельное имя/URL исходного Telegram GitHub repository.

### Как определяется выполненный пункт

Пункт считается выполненным только если:

- открыт exact plan item;
- проверена dependency;
- открыт Telegram reference;
- построен pre-coding audit;
- production gap закрыт;
- есть runtime tests;
- соседние regressions прошли;
- diff ограничен allowed files;
- создан реальный GitHub PR;
- после merge fix присутствует в main;
- независимое review не выявило known gap.

### Важные директории

- `flows/` — handlers и UX-flow;
- `services/` — business orchestration;
- `repositories/` — SQLite access;
- `integrations/yclients/` — YClients;
- `max_api/` — MAX transport;
- `core/` — router, events, state, config, permissions, diagnostics;
- `ui/` — texts/buttons;
- `db/` — SQLite init/schema;
- `tests/` — parity/runtime/regression tests.

### Нельзя менять без необходимости

- `telegram_reference/`;
- старый `.docx`;
- schema/migrations;
- global permissions;
- соседние mutation flows;
- files вне exact allowed list;
- deployment config;
- YClients auth;
- router/state architecture.

---

## 3. Архитектура

### Общая цепочка

`MAX update → normalize_update → Router → RouterContext → flow handler → service/repository/integration → sender → MAX API`

### Слои

#### `max_api/`

MAX HTTP transport, auth, updates, sending, attachments, API errors. Не содержит бизнес-логику.

#### `core/events.py`

Нормализация platform update.

#### `core/router.py`

Регистрация и dispatch routes.

#### `flows/`

Handlers и flow:

- answer callback;
- access gate;
- state;
- service calls;
- screen selection;
- send text/keyboard.

Flow не делает raw SQL и raw HTTP.

#### `services/`

Бизнес-операции:

- booking context;
- ownership;
- YClients workflow;
- reminders/funnels;
- domain errors.

#### `integrations/yclients/`

Auth, client, DTO, endpoints и mapping integration errors.

#### `repositories/`

SQLite CRUD/query. Repository не рендерит UI.

#### `core/state.py`

State scoped по user/chat, current screen, navigation stack, state data.

#### `ui/texts.py`

Переиспользуемые тексты.

#### `ui/buttons.py`

Payload constants и MAX keyboards.

#### `core/config.py`

Environment config и validation.

#### `db/sqlite.py`

SQLite initialization и schema compatibility.

### Допустимые зависимости

- flow → service/repository/ui/core;
- service → integration/repository/core;
- repository → db/model;
- max_api → config/transport model.

### Запрещённые зависимости

- aiogram;
- MAX production → `telegram_reference`;
- repository → flow/UI;
- integration → flow;
- raw SQL из flow;
- raw YClients HTTP из flow.

### MAX API

- источник истины — официальная документация MAX;
- нельзя придумывать method/parameter;
- update сначала normalize;
- callback handler должен answer callback;
- transport errors не должны утекать в UX;
- token только в env;
- cancellation корректно пробрасывается.

### YClients

- источник истины — project YClients doc + active integration code;
- нельзя считать availability, slot, status и capability постоянными;
- перед mutation требуется revalidation, когда она есть в Telegram;
- 401/403, 404, 429, 5xx, timeout/network и partial failure обрабатываются отдельно;
- raw response не показывается пользователю.

### State

- scoped по `(platform_user_id, chat_id)`;
- current screen совпадает с UI;
- очищаются только owned keys;
- source screen сохраняется для Back;
- repeated callback не повторяет mutation;
- Home очищает owned flow state.

### SQLite и migrations

- SQLite — локальный storage;
- default path: `data/max_barbershop_bot.sqlite3`;
- migration только в явно разрешённом scope;
- migration idempotent;
- existing rows остаются читаемыми;
- migration нельзя добавлять ради одного renderer.

### Запрещённые архитектурные изменения без согласования

- новый framework;
- aiogram;
- ORM;
- новая база;
- webhook вместо polling;
- новый router/state contract;
- schema change;
- YClients auth redesign;
- role matrix change;
- final create/cancel/reschedule semantics;
- background worker;
- новый dependency.

---

## 4. Перенос Telegram → MAX

### Переносится 1:1

- бизнес-смысл;
- русские тексты;
- emoji;
- роли и доступ;
- порядок действий;
- кнопки;
- callback-семантика;
- state transitions;
- errors;
- empty/stale;
- Back/Home;
- mutations;
- logs/attribution;
- idempotency.

### Допустимые различия

Только технические:

- Telegram keyboard → MAX keyboard;
- callback_data → payload;
- Telegram update → normalized event;
- Telegram file_id → MAX attachment;
- Telegram ID → MAX ID, если безопасно и эквивалентно;
- unsupported transport → closest equivalent с объяснением.

### Кнопки/callback/state

- сохраняются текст, порядок и смысл;
- payload должен быть stable;
- malformed/stale безопасен;
- нельзя выбирать «первый» или «последний» entity без доказанной semantics;
- state и visible UI не расходятся;
- Back ведёт на exact source;
- Home ведёт в role-aware menu.

### Если MAX не поддерживает Telegram feature

1. Проверить official MAX docs.
2. Описать Telegram behavior.
3. Описать closest MAX equivalent.
4. Перечислить потерю.
5. STOP, если меняется UX/бизнес-смысл.
6. Не придумывать API.

### Проверка паритета

Каждая строка audit table должна иметь:

- production function;
- named executable runtime test;
- status.

Source-string tests недостаточно.

---

## 5. Стиль и качество кода

### Python

**Не установлено:** exact Python version.

**Подтверждено:** используются `from __future__ import annotations`, builtin generics, dataclass и async.

### Типизация

- type hints для public functions;
- `str | None`, `list[...]`;
- DTO/dataclass на structured boundary;
- `Any` только на raw boundary;
- массовый `type: ignore` запрещён.

### Async

- I/O async;
- event loop не блокировать;
- `CancelledError` пробрасывать;
- background task cancel/await;
- mutation защищать lock/idempotency;
- `asyncio.run` внутри production async flow запрещён.

### Именование

- constants — `UPPER_SNAKE_CASE`;
- handlers — `handle_*`;
- show/build/format — `_show_*`, `_build_*`, `format_*`;
- state keys — constants;
- tests — точное ожидаемое поведение.

### Imports

- stdlib / third-party / project;
- no aiogram;
- избегать circular imports;
- local import только при реальной необходимости.

### Комментарии

- объясняют «почему»;
- не пересказывают код;
- не писать AI-manifesto;
- не оставлять закомментированный старый код.

### Размер функций

**Не установлено:** числовой лимит.

Проверяемое правило: handler не смешивает SQL, HTTP, rendering и mutation.

### Рефакторинг

Большой refactor в parity PR запрещён. Один PR — один small scope.

### TODO и временные решения

Known gap внутри scope нельзя заменять TODO. Placeholder flow запрещён.

---

## 6. Проверки

### Обязательные команды

```bash
python -m compileall max_barbershop_bot
python -m pytest <focused tests> -q
python -m pytest tests -k "<focus>" -q
python -m pytest tests -k "<neighbor regressions>" -q
rg "from aiogram|import aiogram" max_barbershop_bot || true
rg "Authorization|partner_token|user_token|raw_response|response_body|traceback" <changed production files> || true
git diff --check
git status --short --branch
```

### Linter/formatter/type-check

**Не установлено:** обязательные команды linter, formatter и type-check.

Нельзя писать, что они пройдены, если конкретные инструменты не настроены и не запускались.

### Когда добавлять tests

При изменении:

- handler;
- screen;
- callback;
- service;
- repository behavior;
- mutation;
- state transition;
- error mapping;
- permission;
- timezone;
- stale behavior;
- Back/Home.

### Обязательные runtime cases

- route;
- access;
- happy path;
- empty;
- stale;
- malformed;
- Back;
- Home;
- repeated callback;
- errors;
- no mutation before confirmation;
- neighboring regression;
- no secret leakage.

### Незавершённая задача

- mandatory test failed;
- production behavior не доказан;
- known gap остался;
- diff unrelated;
- secret leak;
- no real PR;
- main не содержит fix после merge;
- agent не смог проверить required contract.

---

## 7. GitHub-процесс

### Base

**Подтверждено:** `main`.

### Branch naming

**Не установлено:** обязательный convention.

### Commit

Exact message задаётся в prompt:

- `PR-XXX — <name>`;
- `PR-XXX-postmerge-fix — <name>`;
- `PR-XXX-final-fix — <name>`.

### PR scope

Один small plan item = один PR. Несколько пунктов объединять нельзя.

### PR body

Обязательно:

- goal;
- base state;
- Telegram semantics;
- files inspected;
- files changed;
- audit table;
- Telegram → MAX table;
- production changes;
- what not changed;
- tests/counts;
- smoke checklist;
- gaps/limitations;
- real PR metadata.

### Перед merge

- focused tests;
- neighboring regression;
- compileall;
- no aiogram;
- no secrets;
- diff check;
- independent review;
- CI green, если CI настроен.

### Automatic merge

**Не установлено:** разрешён ли autonomous merge и какой merge method.

До отдельного решения owner агент не должен считать automatic merge разрешённым.

### STOP

- conflict;
- schema/dependency/architecture change;
- unsupported MAX feature;
- ambiguous YClients;
- dirty tree;
- missing dependency;
- unrelated diff;
- failing required tests;
- no real PR.

---

## 8. Автономная работа одного пункта

1. Проверить clean tree.
2. Обновить/проверить `main`.
3. Зафиксировать branch, HEAD, log, remotes.
4. Выбрать первый approved незакрытый пункт с выполненными dependencies.
5. Открыть plan item.
6. Открыть exact Telegram reference.
7. Открыть exact MAX target files.
8. Построить audit table.
9. STOP при conflict.
10. Объявить exact files to change.
11. Реализовать minimal production change.
12. Добавить runtime tests.
13. Проверить diff.
14. Запустить compileall/tests/rg/diff checks.
15. Провести self-review.
16. Исправить findings без расширения scope.
17. Commit exact message.
18. Push/create real PR.
19. Прочитать PR обратно из GitHub.
20. Проверить CI.
21. Merge только по установленной policy.
22. После merge проверить main.
23. Не менять plan status без отдельного разрешения.
24. Сформировать отчёт.

---

## 9. Известные ошибки и уроки

1. Happy-path tests выдавались за полное закрытие.
2. `source_screen` hardcoded вместо реального source.
3. State говорил history, UI показывал detail.
4. Repeat target выбирался heuristic first/last.
5. Partial reschedule failure сохранялся как success.
6. Success context очищался до повторного callback.
7. `None`/`0` any-master semantics не были доказаны.
8. Employee fallback использовался для issuer.
9. Telegram card field молча пропускался.
10. Локальный commit/`make_pr` выдавался за real PR.
11. Exact PR title не соблюдался.
12. Source-string tests заменяли runtime proof.

Агенту запрещено повторять эти паттерны.

---

## 10. Противоречия и отсутствующие данные

1. Exact Python version отсутствует.
2. Linter/formatter/type-check commands не закреплены.
3. CI required checks не закреплены.
4. Autonomous merge policy не закреплена.
5. Merge method не закреплён.
6. Branch naming не закреплён.
7. Force-push policy не закреплена.
8. Branch deletion policy не закреплена.
9. Отдельный Telegram repo URL не подтверждён.
10. Exact location YClients PDF в repo не подтверждён.
11. Docker/deployment/rollback policy не подтверждена.
12. `.env.example` может не перечислять все env, которые читает config.

---

# Часть 3. Автономный рабочий процесс

## Preconditions

Владелец должен определить:

- разрешённый диапазон plan items;
- разрешён ли push/create PR;
- разрешён ли merge;
- required CI checks;
- разрешены ли stacked PR.

Без explicit merge permission агент останавливается после создания и проверки PR.

## Workflow одного пункта

### 1. Обновить фактическое состояние

```bash
git status --short --branch
git rev-parse HEAD
git log --oneline -10 --decorate
```

Проверить `main`, не использовать remembered status.

### 2. Выбрать следующий пункт

Выбрать первый approved незакрытый пункт с выполненными dependencies.

### 3. Изучить требования

- plan item;
- exact Telegram files;
- exact MAX files;
- existing tests;
- previous related PRs.

### 4. Сравнить Telegram и MAX

Создать table:

`requirement | Telegram | MAX | gap | production action | runtime test`

При conflict — STOP.

### 5. Реализовать

- один small scope;
- only allowed files;
- no UX invention;
- no broad refactor;
- no Telegram changes.

### 6. Проверить diff

```bash
git diff --name-only
git diff --stat
git diff -- <production files>
git diff --check
```

### 7. Запустить tests

- compileall;
- new focused tests;
- focused selector;
- neighboring regressions;
- no aiogram;
- no secrets.

### 8. Self-review

Проверить:

- text/buttons;
- access;
- identity;
- state/UI;
- Back/Home;
- stale;
- errors;
- timezone;
- repeated callback;
- mutation count;
- cleanup;
- scope.

### 9. Исправить findings

Не расширять scope. После трёх одинаковых неудачных root-cause attempts — STOP.

### 10. Commit/PR

- exact commit;
- push;
- real PR;
- прочитать PR обратно;
- проверить files, base/head/SHA.

### 11. CI

Проверить required checks, если они существуют. Невыполнимую проверку описать явно.

### 12. Merge или STOP

Merge только если owner разрешил autonomous merge и:

- independent verdict MERGEABLE;
- CI green;
- no unresolved review;
- no scope expansion;
- main не изменился конфликтно.

### 13. Status plan

Не менять plan status без explicit permission. Вести отдельный execution journal.

### 14. Отчёт

- plan item;
- start HEAD;
- dependency;
- Telegram findings;
- files changed;
- tests;
- review;
- commit;
- PR;
- CI;
- merge;
- post-merge main verification;
- remaining gaps.

## Работа над несколькими пунктами

Следующий пункт начинается только после merge предыдущего, если owner явно не разрешил stacked PR.

---

# Часть 4. Критерии готовности

Пункт считается DONE только при выполнении всех применимых критериев.

## Scope и sources

- [ ] Открыт exact plan item.
- [ ] Dependency подтверждена в current HEAD.
- [ ] Telegram reference открыт до coding.
- [ ] MAX target files audited.
- [ ] Pre-coding parity table создана.
- [ ] Нет unresolved Telegram/MAX conflict.
- [ ] Реализован один small scope.
- [ ] Новая UX/business logic не придумана.
- [ ] `telegram_reference/` unchanged.

## Production behavior

- [ ] Entry работает.
- [ ] Callback answered.
- [ ] Access matrix совпадает.
- [ ] Text/emoji совпадают.
- [ ] Buttons/order/payload semantics совпадают.
- [ ] Entity identity exact.
- [ ] Current screen = visible UI.
- [ ] State transitions совпадают.
- [ ] Back совпадает.
- [ ] Home совпадает.
- [ ] Empty state совпадает.
- [ ] Stale callback safe.
- [ ] Malformed callback safe.
- [ ] Errors friendly and masked.
- [ ] Branch timezone используется.
- [ ] Side effects выполняются один раз.
- [ ] Repeated callback не повторяет mutation.
- [ ] Partial failure имеет отдельный outcome.
- [ ] Cleanup затрагивает только owned state.
- [ ] Neighboring flows unchanged.

## Architecture/API

- [ ] MAX API contract подтверждён.
- [ ] YClients contract подтверждён.
- [ ] Нет invented methods/parameters.
- [ ] Нет raw SQL в flow.
- [ ] Нет raw transport call в flow.
- [ ] Нет aiogram.
- [ ] Нет unauthorized dependency.
- [ ] Нет unauthorized migration.
- [ ] Нет secret/raw response leakage.

## Tests

- [ ] Real handler исполняется.
- [ ] Real renderer/keyboard исполняется.
- [ ] Happy path.
- [ ] Access.
- [ ] Empty.
- [ ] Stale/malformed.
- [ ] Back/Home.
- [ ] Error matrix.
- [ ] Repeated callback.
- [ ] Mutation count.
- [ ] Timezone.
- [ ] Neighboring regressions.
- [ ] No-network fakes.
- [ ] Каждая parity row имеет named executable test.

## Commands

- [ ] `python -m compileall max_barbershop_bot`
- [ ] focused pytest files
- [ ] focused `-k`
- [ ] neighboring regression suite
- [ ] no-aiogram `rg`
- [ ] secret/raw diagnostic `rg`
- [ ] `git diff --check`
- [ ] clean `git status`

Linter/type-check нельзя отмечать как пройденные, пока exact commands не определены и не выполнены.

## Diff/review

- [ ] Только allowed files.
- [ ] Каждый production diff просмотрен.
- [ ] Нет placeholder/TODO внутри scope.
- [ ] Нет dead code.
- [ ] Нет unrelated refactor.
- [ ] Independent review не выявило blocker/high issue.

## GitHub

- [ ] Exact commit message.
- [ ] Clean tree after commit.
- [ ] Real GitHub PR exists.
- [ ] URL/number/base/head/head SHA verified.
- [ ] PR files verified.
- [ ] Required CI green, если настроен.
- [ ] PR body полный.
- [ ] Merge выполнен по разрешённой policy.
- [ ] Main after merge содержит fix.

## Report

- [ ] Что изменено.
- [ ] Что не изменено.
- [ ] Что не перенесено и почему.
- [ ] Exact test counts.
- [ ] Smoke checklist.
- [ ] Honest remaining gaps.
- [ ] Нет hidden blocker.

---

# Часть 5. Блокирующие условия

Агент обязан остановиться и запросить решение человека в следующих случаях.

## Product/parity

1. Telegram reference и MAX конфликтуют.
2. Два Telegram reference файла противоречат друг другу.
3. Plan acceptance конфликтует с active Telegram behavior.
4. Требуется новая UX/button/role/scenario.
5. Непонятно, какую entity выбирает callback.
6. Exact Back destination нельзя доказать.
7. Telegram behavior выглядит опасным bug.
8. MAX не поддерживает material Telegram capability.

## Architecture

9. Нужен production file вне allowed list.
10. Нужна schema migration.
11. Нужен новый dependency/framework.
12. Нужен router/event/state redesign.
13. Нужен новый background worker.
14. Нужна global permission change.
15. Нужен YClients auth redesign.
16. Нужна смена mutation order.

## API/data

17. MAX method/field отсутствует или неоднозначен.
18. YClients endpoint/payload/auth не подтверждён.
19. Непонятно, безопасно ли показывать identifier.
20. Live test создаст unsafe production mutation.
21. Rate-limit/idempotency semantics неизвестны.
22. Timezone source неизвестен.

## Workspace/Git

23. Dirty tree до начала.
24. Missing reference/target.
25. Missing dependency.
26. Unrelated changes в branch.
27. Merge conflict затрагивает product behavior.
28. Требуется force push без разрешения.
29. Real PR невозможно создать.
30. Required CI не запускается или падает из-за change.
31. Secret обнаружен в diff/log/test.

## Quality

32. Runtime test нельзя написать без замены handler under test.
33. Repeated callback может повторить mutation.
34. State и visible UI расходятся.
35. Mandatory error leaks raw data.
36. Neighbor regression падает из-за change.
37. Три evidence-based attempts не исправили одну root cause.

## Не являются blocker сами по себе

- missing `gh`;
- missing origin;
- generic branch name;
- no live MAX client для no-network task;
- no live YClients credentials для mocked tests;
- remote main unavailable, если owner явно разрешил current clean workspace.

Эти ограничения всё равно указываются в report.

---

# Часть 6. Шаблон задачи

```text
TASK:
PR-<NNN> — <one small scope>

Repository:
ilya-boooots/max-barbershop-bot

EXACT PR TITLE:
PR-<NNN> — <title>

EXACT COMMIT MESSAGE:
PR-<NNN> — <title>

PLAN:
Open:
- docs/max_telegram_parity_plan_v2.md

Selected item:
- PR-<NNN> — <name>
- Priority: <...>
- Area: <...>
- Dependency: <...>
- Telegram files: <exact files>
- MAX target files: <exact files>
- Scope: <verbatim>
- Acceptance: <verbatim>

PRIMARY GOAL:
Port only:
<one screen/handler/service/repository/flow step>

Strict Telegram → MAX 1:1 parity.
No new UX, buttons, scenarios or architecture.

BASE CHECK:
pwd
git status --short --branch
git branch --show-current || true
git rev-parse HEAD || true
git log --oneline -10 --decorate || true
git remote -v || true

STOP if:
- dirty tree;
- dependency absent;
- reference/target missing;
- Telegram/MAX conflict;
- schema/migration needed;
- another production file required;
- MAX/YClients contract ambiguous.

TELEGRAM SOURCE OF TRUTH:
Open before coding:
- <primary files>

Supporting files:
- <supporting files>

Determine exact:
1. entry callback;
2. access matrix;
3. text;
4. buttons/order;
5. callback semantics;
6. state;
7. Back;
8. Home;
9. empty;
10. stale/malformed;
11. error matrix;
12. side effects;
13. idempotency;
14. timezone;
15. logs/notifications.

CURRENT MAX AUDIT:
Open:
- <target files>
- <existing tests>

STRICT SCOPE:
This PR is only:
- ...

Do NOT modify:
- ...
- schema/migrations;
- telegram_reference/;
- global permissions;
- unrelated UX.

ALLOWED PRODUCTION FILES:
- ...

ALLOWED TEST FILES:
- ...

If another production file is required, STOP and report:
1. Telegram behavior;
2. missing MAX capability;
3. exact file;
4. smallest change;
5. why allowed files are insufficient.

MANDATORY PRE-CODING TABLE:
| Requirement | Telegram | MAX before | Gap | Production action | Executable test |
|---|---|---|---|---|---|

Then print:
PRODUCTION FILES PROPOSED TO CHANGE

A test-only completion is forbidden when production gaps exist.

REQUIRED IMPLEMENTATION:
A. <behavior>
B. <fallback>
C. Back/Home
D. stale/malformed
E. error matrix
F. state/idempotency
G. side effects

MANDATORY TEST MATRIX:
1. route registration
2. access matrix
3. happy path
4. empty
5. stale
6. malformed
7. Back
8. Home
9. repeated callback
10. 401/403
11. 404
12. 429
13. 5xx
14. timeout/network
15. mutation count
16. neighboring regression
17. no aiogram
18. no secrets

FORBIDDEN TEST SHORTCUTS:
- do not mock the handler under test;
- do not prove runtime with source strings;
- do not test only constants;
- execute real renderer and keyboard builder;
- use fakes only at network/repository boundaries.

REQUIRED COMMANDS:
python -m compileall max_barbershop_bot
python -m pytest <focused tests> -q
python -m pytest tests -k "<focus>" -q
python -m pytest tests -k "<neighbor regression>" -q
rg "from aiogram|import aiogram" max_barbershop_bot || true
rg "<symbols>" <files> || true
rg "Authorization|partner_token|user_token|raw_response|response_body|traceback" <changed production files> || true
git diff --check
git status --short --branch

MANDATORY DIFF REVIEW:
git diff --name-only
git diff --stat
git diff -- <each production file>

FINAL PR REPORT:
1. Goal
2. Base state
3. Telegram semantics
4. Files inspected
5. Files changed
6. Audit table
7. Telegram → MAX table
8. Production changes
9. What was not changed
10. Tests and counts
11. Smoke checklist
12. Remaining gaps
13. Real PR URL/number/base/head/head SHA/state

FINAL ACCEPTANCE:
Do not declare completion unless:
- every parity row is implemented;
- every row has a named executable test;
- all focused tests pass;
- diff is limited;
- no secrets;
- no aiogram;
- real PR exists;
- no known gap remains.
```

---

# Часть 7. Шаблон исправления

```text
TASK:
PR-<NNN>-<postmerge-fix|final-fix> — <exact root-cause fix>

Repository:
ilya-boooots/max-barbershop-bot

This is a corrective PR after merged PR #<number>.

ROOT CAUSE:
<exact observed defect>

REPRODUCTION:
1. ...
2. ...

Actual:
...

Expected from Telegram:
...

ALREADY WORKING — DO NOT REWRITE:
- ...
- ...

EXACT PR TITLE:
...

EXACT COMMIT MESSAGE:
...

SOURCE OF TRUTH:
- exact Telegram files
- exact MAX files
- previous PR patch/tests

STRICT REMAINING SCOPE:
- ...

Do NOT:
- reimplement completed happy paths;
- refactor neighboring flow;
- add UX;
- modify mutation semantics;
- change schema;
- modify telegram_reference.

ALLOWED FILES:
- ...

STOP if:
- root cause contradicts Telegram;
- another production file is required;
- fix requires schema/architecture/product decision.

MANDATORY PRE-CODING TABLE:
| Failed requirement | Telegram | MAX after prior PR | Root cause | Minimal fix | Runtime regression test |
|---|---|---|---|---|---|

REQUIRED FIX:
- exact production behavior;
- repeated callback;
- state cleanup/persistence;
- Back/Home;
- stale/errors;
- invariants.

RUNTIME TEST:
- execute the real failing handler;
- reproduce the old failure;
- prove corrected output/state/keyboard/mutation count;
- prove already-working paths unchanged.

REQUIRED COMMANDS:
python -m compileall max_barbershop_bot
python -m pytest <focused tests> -q
python -m pytest tests -k "<neighbor regression>" -q
rg "from aiogram|import aiogram" max_barbershop_bot || true
git diff --check

FINAL REPORT:
- root cause;
- production fix;
- regression test;
- unaffected flows;
- real PR metadata;
- remaining gaps.

Do not create another partial fix.
Do not declare completion until the original failure is reproduced and passes.
```

## Resume after a valid STOP

Первая строка:

`You correctly stopped because <reason>.`

После неё указать принятое владельцем решение и exact permission.

---

# Приоритет правил

При противоречии применяется следующий порядок:

1. Явное решение владельца проекта в текущей задаче.
2. STOP-условия и запреты из этого файла.
3. Официальная документация MAX API и YClients для технических контрактов.
4. `telegram_reference/` для UX и бизнес-логики.
5. `docs/max_telegram_parity_plan_v2.md` для scope, dependencies и acceptance.
6. Текущий код MAX для уже принятой архитектуры.

Если конфликт нельзя разрешить этим порядком без продуктового или архитектурного выбора, агент обязан остановиться.
