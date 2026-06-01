# CI TESTING GUIDE

## Что запускается в CI
- install dependencies
- `python -m compileall app`
- `pytest -q`
- отдельная проверка callback_data тестом

## Триггеры
- `pull_request`
- `push` в `main`

## Обязательные env (dummy)
- `BOT_TOKEN=dummy`
- `DB_PATH=temp/test.sqlite3`
- `PROTECTED_DEV_TG_ID=378881880`

## Локальный прогон перед PR
1. `python -m compileall app`
2. `pytest -q`
3. `python - <<'PY' ... import app.main ... PY`

## Политика интерпретации
- CI PASS не равен PASS по Telegram/YClients runtime.
- Runtime-сценарии закрываются только ручными кейсами из `MANUAL_TEST_PLAN.md`.
