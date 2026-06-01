# Barbershop Bot + YClients

Telegram-бот для барбершопа: онлайн-запись, управление клиентами и операционные сценарии для команды. Интеграция с YClients — основной источник слотов, мастеров и записей.

> Source of truth for YClients API: `docs/YCLIENTS_REST_API.pdf`.

## What this product is
- Клиентский Telegram-бот для записи в барбершоп.
- Админ-интерфейс внутри Telegram для оператора/управляющего.
- Подключение YClients через UI (`⚙️ Интеграция YClients`) без правки кода.

## Features by readiness

### Core (ready)
- `/start`, главное меню, навигация `⬅️ Назад` и `🏠 Главное меню`.
- Запись клиента по шагам (категория → услуга → мастер → дата → время).
- Экран `📅 Мои записи`.
- Антифлуд и безопасные fallback-сценарии.

### Admin (ready)
- `📋 Записи`: просмотр/карточка записи.
- `👥 Клиенты`: поиск и карточки.
- `⚙️ Интеграция YClients`: мастер подключения + проверка.
- Разграничение ролей (developer/admin/manager).

### Marketing (optional / plan-based)
- `📣 Рассылка` (если включено в вашей сборке).
- Лояльность (если включено в вашей сборке).
- Шаблоны сообщений и follow-up сценарии.


## HTTP lifecycle for YClients
Бот использует общий `aiohttp.ClientSession` для всех запросов в YClients в рамках одного процесса. Сессия создаётся один раз при старте приложения и прокидывается в YClients-клиент, поэтому запросы не открывают новые соединения на каждый шаг записи.

При остановке приложения (systemd shutdown/restart) эта общая сессия закрывается в `finally` блоке `app.main`, что предотвращает предупреждения `Unclosed client session` и нестабильные ошибки в booking flow.

## Quick start (local)
```bash
cd /workspace/barbershop-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.save .env
python -m app.main
```

## Production deploy (Ubuntu + systemd template)

Рекомендуемая структура:
- Repo: `/opt/bots/<bot-name>`
- Venv: `/opt/bots/<bot-name>/.venv`
- Env: `/opt/bots/<bot-name>/.env`
- Unit: `telegram-bot@<bot-name>.service`

Пример unit-файла:
```ini
[Unit]
Description=Telegram Bot (%i)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bots/%i
EnvironmentFile=/opt/bots/%i/.env
ExecStart=/opt/bots/%i/.venv/bin/python -m app.main
Restart=always
RestartSec=3
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot@<bot-name>
sudo systemctl restart telegram-bot@<bot-name>
```

## Configuration

`app/core/config.py` — единая точка конфигурации.

### Required
- `BOT_TOKEN`
- `PROTECTED_DEV_TG_ID`
- `DB_PATH`

### White-label branding (single place)
- `BUSINESS_NAME`
- `SUPPORT_CONTACT` (например, `@your_support`)
- `BUSINESS_ADDRESS`
- `BUSINESS_PHONE`

### Integration / runtime
- `YCLIENTS_PARTNER_TOKEN`
- `YCLIENTS_USER_TOKEN`
- `YCLIENTS_COMPANY_ID`
- `YCLIENTS_BASE_URL`
- `APP_ENV`, `LOG_LEVEL`, `SYSTEMD_UNIT`
- `THROTTLE_MSG_SECONDS`, `THROTTLE_CB_SECONDS`

`.env` in production: `/opt/bots/<bot-name>/.env`.

## Connect YClients via bot UI
1. Откройте бот под админом.
2. Нажмите `⚙️ Интеграция YClients`.
3. Введите/проверьте:
   - Company ID
   - Partner Token
   - User Token
   - Base URL (если требуется нестандартный)
4. Нажмите `🔌 Проверить YClients`.
5. Дождитесь `✅` статуса проверки.

## Logs (journalctl)
```bash
sudo journalctl -u telegram-bot@<bot-name>.service -f --no-pager
sudo journalctl -u telegram-bot@<bot-name>.service -n 200 --no-pager
sudo systemctl status telegram-bot@<bot-name>.service
```

## Troubleshooting (top 10)
1. **Bot не стартует** → проверьте `BOT_TOKEN` и формат `.env` (`KEY=value`).
2. **Ошибка DB path** → `DB_PATH` должен быть доступен пользователю сервиса.
3. **Не подхватывается .env** → путь должен быть `/opt/bots/<bot-name>/.env` в systemd `EnvironmentFile`.
4. **Wrong startup module** → только `python -m app.main`.
5. **403/401 в YClients** → перепроверьте токены и права кабинета.
6. **429 в YClients** → лимит API, повторите позже.
7. **Кнопки “молчат”** → проверьте antiflood и актуальность callback (старые сообщения).
8. **Нет админ-доступа** → назначьте роли через `👥 Персонал`/таблицу ролей.
9. **После деплоя старая версия** → `git pull`, `pip install -r requirements.txt`, `restart service`.
10. **Нет логов в journalctl** → проверьте имя юнита (`telegram-bot@<bot-name>.service`).

## Security notes
- Никогда не коммитьте `.env`, токены, секреты.
- Для скриншотов/демо маскируйте токены и телефоны.
- Ограничивайте доступ к серверу и systemd unit.

## Additional docs
- Onboarding owner flow: `docs/ONBOARDING_BUSINESS.md`
- Ubuntu install playbook: `docs/INSTALL_PLAYBOOK_UBUNTU.md`
- QA checklist: `docs/QA_CHECKLIST.md`
- Sales kit: `docs/SALES_KIT.md`
- YClients API source of truth: `docs/YCLIENTS_REST_API.pdf`

## Booking UX assets
- Загрузите фото в вашего бота один раз и получите `file_id`.
- Укажите `BARBERSHOP_PHOTO_FILE_ID` в `.env`, тогда экран категорий будет отправляться с фото.
- Если переменная не задана, бот автоматически покажет текстовый экран без фото.

## Automated tests

Run all tests:
```bash
pytest
```

Run by layer:
```bash
pytest -m unit
pytest -m integration
pytest -m smoke
```

The suite is fully local/offline for normal runs:
- no live Telegram API calls
- no live YClients requests (mocked/faked in tests)
