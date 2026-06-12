# MAX API transport contract

## Callback payload policy

The MAX API docs describe inline keyboard button types and document link URL length, but do not currently publish an exact callback payload byte limit or charset for `callback` buttons. Until MAX publishes a stricter or different callback-specific rule, this project uses a conservative internal policy:

- callback payloads are limited to 64 UTF-8 bytes;
- callback payloads may contain only ASCII letters, digits, colon `:`, underscore `_`, dash `-`, and dot `.`;
- callback payloads must not contain JSON, raw URLs, raw names/titles, user-generated text, tokens, phones, contacts, or raw YClients payloads;
- dynamic entities must be addressed through short screen-local indexes and resolved from per-user state;
- validation is enforced centrally when `MaxButton(type="callback", payload=...)` is created.
