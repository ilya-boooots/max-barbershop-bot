# MAX API transport contract

## Official source

* `https://dev.max.ru/docs-api`

## Supported capabilities

| Capability | Current support | Code location | Notes |
| ---------- | --------------- | ------------- | ----- |
| get updates | ✅ Supported for development Long Polling | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/polling.py` | Uses `GET /updates` with `limit`, `timeout`, `marker`, `types`; official docs recommend Webhook for production. |
| send text message | ✅ Supported | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py` | Uses `POST /messages` with exactly one `user_id` or `chat_id` query parameter and `NewMessageBody` JSON. |
| answer callback | ✅ Supported | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py` | Uses `POST /answers?callback_id=...` with optional `notification` and/or `message`. |
| inline keyboard | ✅ Supported | `max_barbershop_bot/max_api/models.py` | Sent as an `inline_keyboard` attachment with `payload.buttons` rows. |
| callback button | ✅ Supported | `max_barbershop_bot/max_api/models.py` | Requires non-empty local payload; callback payload max length was not found in inspected official docs. |
| link button | ✅ Supported | `max_barbershop_bot/max_api/models.py` | Requires `url`; validates the documented 2048-character URL limit. |
| message button | ✅ Shape supported | `max_barbershop_bot/max_api/models.py` | Official docs list `message`; no extra local assumptions beyond `type`, `text`, and optional payload. |
| request_contact button | ✅ Supported | `max_barbershop_bot/max_api/models.py`, `max_barbershop_bot/ui/buttons.py` | Sent as a button with `type=request_contact`; incoming contact is expected as a `contact` attachment. |
| incoming contact attachment | ✅ Supported | `max_barbershop_bot/max_api/models.py`, `max_barbershop_bot/core/events.py`, `max_barbershop_bot/services/registration.py` | Preserves raw attachment payload, including `payload.vcf_info`, `payload.max_info`, and `payload.hash`; phone extraction reads `vcf_info`. |
| send photo/media | ✅ Supported for already uploaded media payloads | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py`, `max_barbershop_bot/max_api/models.py` | Use `create_upload_url(upload_type="image")`, upload outside this helper, then send the returned upload payload/token as an `image` attachment. |
| send photo + text | ✅ Supported | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py` | `send_photo`/`send_photo_to_user`/`send_photo_to_chat` pass text plus an `image` attachment to `POST /messages`. |
| send photo + keyboard | ✅ Supported by body shape | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py` | Sends media attachment and `inline_keyboard` attachment in one message body; if a live MAX response rejects it, future flows must fall back to photo first, text + keyboard second. |
| attachments | ✅ Supported | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/models.py` | Existing attachments and keyboard attachment are combined safely into one `attachments` array. |
| error handling/rate limits | ✅ Supported | `max_barbershop_bot/max_api/client.py`, `max_barbershop_bot/max_api/sender.py` | Maps 401 and 429 to specific errors, treats 429/503/5xx/network errors as retryable, keeps 400/401/404/405 non-retryable except documented `attachment.not.ready`. |

## Confirmed MAX limitations

* Authorization token must be sent in the `Authorization` header; query-token auth is not supported.
* `GET /updates` Long Polling is for development/testing and is not suitable for production; production should use Webhook.
* `GET /updates` supports `limit` from 1 to 1000 and `timeout` from 0 to 90 seconds.
* `POST /messages` text is nullable but limited to 4000 characters when present.
* `POST /messages` accepts `format` only as `markdown` or `html`.
* Inline keyboard limit is 210 buttons total, 30 rows, 7 buttons per row, and up to 3 buttons per row for `link`, `open_app`, `request_geo_location`, or `request_contact` rows.
* Link button URL length is limited to 2048 characters.
* Upload `type=photo` is no longer supported; use `type=image`.
* Upload supports one file at a time; maximum file size is 4 GB.
* Uploaded attachments can be temporarily not ready; `attachment.not.ready` must be retried later/backed off.

## Transport rules for future PRs

* Do not invent MAX API behavior; inspect `https://dev.max.ru/docs-api` for every new transport shape.
* Use this transport layer instead of building raw MAX requests inside flows.
* Use official MAX docs for new button/media types and add local validation only for documented limits.
* If photo + keyboard cannot be delivered in the same live MAX message, send photo first and text + keyboard second.
* Callbacks must always be answered when MAX requires it; callback answer failures must not crash the polling loop.
* Do not put secrets, personal data, or large state in callback payloads.
