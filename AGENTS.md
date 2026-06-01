# MAX Barbershop Bot — Codex Rules

This repository contains Telegram bot code in `telegram_reference/`.

Use `telegram_reference/` only as reference implementation.

Do not blindly copy aiogram handlers.
Telegram code is reference only.

Port:
- business logic
- UX flows
- Russian texts
- roles
- YClients logic
- notifications
- broadcasts
- statistics
- error handling

Rewrite:
- transport layer
- handlers
- keyboards/buttons
- update processing

MAX API source of truth:
https://dev.max.ru/docs-api

YClients API source of truth:
`telegram_reference/YCLIENTS REST API.pdf`

Rules:
- UI text must be Russian with emojis.
- Codex prompts must be answered in Russian when reporting to user.
- Work in small PRs.
- One feature / one flow / one PR.
- Do not install dependencies unless necessary.
- Do not write docs unless asked.
- Do not add tests unless asked.
- Do not run full pytest first.
- Start with targeted file search only.
- If root cause/API detail is unclear, stop and report.
