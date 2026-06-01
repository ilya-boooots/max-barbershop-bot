# STEP 1 — Startup / CI / базовая техническая проверка

## Команды локальной проверки

Выполнять из корня репозитория:

```bash
python -m compileall app
pytest -q
python - <<'PY'
import app.main
print("app.main import ok")
PY
```

## Ожидаемый результат

- `python -m compileall app` завершается без ошибок компиляции.
- `pytest -q` завершается успешно.
- import smoke (`import app.main`) печатает `app.main import ok`.

## Что должен показывать GitHub Actions (CI)

Workflow `CI` на Pull Request должен выполнять:

1. Установку зависимостей.
2. `python -m compileall app`.
3. `pytest -q`.
4. import smoke test для `app.main`.
5. отдельный тест валидации callback_data (`tests/unit/test_callback_data_validation.py`).

Без реальных секретов продакшена, с безопасными env-переменными:

- `BOT_TOKEN=dummy`
- `DB_PATH=temp/test.sqlite3`
- `PROTECTED_DEV_TG_ID=378881880`

## Что проверить вручную после merge

1. CI `CI` зелёный на merge-коммите в `main`.
2. Deploy workflow стартовал на `push` в `main` (если деплой ожидается).
3. После деплоя сервис `telegram-bot@barbershop-bot` поднялся без crash-loop.
4. Бот отвечает на `/start` в Telegram.
