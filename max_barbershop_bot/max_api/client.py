"""Thin asynchronous client for the official MAX Bot API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import aiohttp

from max_barbershop_bot.core.config import Config, load_config
from max_barbershop_bot.max_api.models import MaxInlineKeyboard, MaxMessage, MaxUpdate, build_media_attachment

logger = logging.getLogger(__name__)

MAX_API_BASE_URL = "https://platform-api.max.ru"


class MaxApiError(RuntimeError):
    """Base exception for MAX API HTTP errors."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class MaxApiAuthError(MaxApiError):
    """Raised when MAX API rejects the configured token."""


class MaxApiRateLimitError(MaxApiError):
    """Raised when MAX API rate limit is exceeded."""


class MaxApiNetworkError(MaxApiError):
    """Raised when a network error prevents MAX API request completion."""


class MaxApiClient:
    """Small MAX API transport client without barbershop business logic."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        base_url: str = MAX_API_BASE_URL,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> None:
        self._config = config or load_config()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or aiohttp.ClientTimeout(total=120)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Create the underlying HTTP session."""

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self._base_url,
                headers={"Authorization": self._config.max_bot_token},
                timeout=self._timeout,
            )

    async def close(self) -> None:
        """Close the underlying HTTP session."""

        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "MaxApiClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def get_me(self) -> dict[str, Any]:
        """Return information about the bot identified by the configured token."""

        response = await self._request("GET", "/me")
        return response if isinstance(response, dict) else {}

    async def send_message(
        self,
        *,
        text: str | None,
        user_id: int | str | None = None,
        chat_id: int | str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        disable_link_preview: bool | None = None,
        notify: bool | None = None,
        text_format: str | None = None,
    ) -> MaxMessage | None:
        """Send a MAX message using documented /messages query recipient and body."""

        params, body = self.build_send_message_payload(
            text=text,
            user_id=user_id,
            chat_id=chat_id,
            keyboard=keyboard,
            attachments=attachments,
            disable_link_preview=disable_link_preview,
            notify=notify,
            text_format=text_format,
        )
        response = await self._request("POST", "/messages", params=params, json=body)
        if isinstance(response, dict):
            message_payload = (
                response.get("message")
                if isinstance(response.get("message"), dict)
                else response
            )
            return MaxMessage.from_payload(message_payload)
        return None

    def build_send_message_payload(
        self,
        *,
        text: str | None,
        user_id: int | str | None = None,
        chat_id: int | str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        disable_link_preview: bool | None = None,
        notify: bool | None = None,
        text_format: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the documented MAX /messages query params and JSON body."""

        if user_id is None and chat_id is None:
            raise ValueError("Укажите user_id или chat_id для отправки сообщения MAX.")
        if user_id is not None and chat_id is not None:
            raise ValueError("Укажите только один адресат MAX: user_id или chat_id.")
        if text is not None and len(text) > 4000:
            raise ValueError("Текст сообщения MAX длиннее 4000 символов.")
        if text_format is not None and text_format not in {"markdown", "html"}:
            raise ValueError("MAX поддерживает только формат markdown или html.")

        params: dict[str, Any] = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        if disable_link_preview is not None:
            params["disable_link_preview"] = disable_link_preview

        body: dict[str, Any] = {"text": text}
        message_attachments: list[dict[str, Any]] = []
        if attachments is not None:
            message_attachments.extend(dict(item) for item in attachments)
        if keyboard is not None:
            message_attachments.append(keyboard.to_attachment())
        if message_attachments:
            body["attachments"] = message_attachments
        if notify is not None:
            body["notify"] = notify
        if text_format is not None:
            body["format"] = text_format
        return params, body

    async def send_media(
        self,
        *,
        media_type: str,
        media_payload: Mapping[str, Any],
        text: str | None = None,
        user_id: int | str | None = None,
        chat_id: int | str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
        notify: bool | None = None,
        text_format: str | None = None,
    ) -> MaxMessage | None:
        """Send an already-uploaded MAX media attachment by upload payload/token."""

        attachment = build_media_attachment(media_type, media_payload)  # type: ignore[arg-type]
        return await self.send_message(
            text=text,
            user_id=user_id,
            chat_id=chat_id,
            keyboard=keyboard,
            attachments=[attachment],
            notify=notify,
            text_format=text_format,
        )

    async def send_photo(
        self,
        *,
        image_payload: Mapping[str, Any],
        text: str | None = None,
        user_id: int | str | None = None,
        chat_id: int | str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
        notify: bool | None = None,
        text_format: str | None = None,
    ) -> MaxMessage | None:
        """Send an already-uploaded MAX image attachment with optional text/keyboard."""

        return await self.send_media(
            media_type="image",
            media_payload=image_payload,
            text=text,
            user_id=user_id,
            chat_id=chat_id,
            keyboard=keyboard,
            notify=notify,
            text_format=text_format,
        )

    async def create_upload_url(self, *, upload_type: str) -> dict[str, Any]:
        """Return MAX upload URL metadata for image/video/audio/file uploads."""

        if upload_type == "photo":
            raise ValueError("MAX API больше не поддерживает type=photo; используйте type=image.")
        if upload_type not in {"image", "video", "audio", "file"}:
            raise ValueError("MAX upload_type должен быть image, video, audio или file.")
        response = await self._request("POST", "/uploads", params={"type": upload_type})
        return response if isinstance(response, dict) else {}

    async def answer_callback(
        self,
        *,
        callback_id: str,
        notification: str | None = None,
        text: str | None = None,
        keyboard: MaxInlineKeyboard | None = None,
    ) -> dict[str, Any]:
        """Answer a MAX callback with an optional notification and/or updated message."""

        body: dict[str, Any] = {}
        if notification is not None:
            body["notification"] = notification
        if text is not None or keyboard is not None:
            message: dict[str, Any] = {"text": text}
            if keyboard is not None:
                message["attachments"] = [keyboard.to_attachment()]
            body["message"] = message

        response = await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json=body,
        )
        return response if isinstance(response, dict) else {}

    async def get_updates(
        self,
        *,
        limit: int | None = None,
        timeout: int | None = None,
        marker: int | None = None,
        types: Sequence[str] | None = None,
    ) -> tuple[list[MaxUpdate], int | None]:
        """Get MAX updates via Long Polling for development and tests only."""

        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if timeout is not None:
            params["timeout"] = timeout
        if marker is not None:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)

        response = await self._request("GET", "/updates", params=params)
        if not isinstance(response, dict):
            return [], None

        raw_updates = response.get("updates")
        updates = (
            [MaxUpdate.from_payload(item) for item in raw_updates if isinstance(item, dict)]
            if isinstance(raw_updates, list)
            else []
        )
        next_marker = response.get("marker")
        return updates, next_marker if isinstance(next_marker, int) else None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        await self.start()
        if self._session is None:
            raise MaxApiNetworkError("HTTP-сессия MAX API не создана")

        safe_params = {
            key: value
            for key, value in (params or {}).items()
            if value is not None
        }
        try:
            async with self._session.request(
                method,
                path,
                params=safe_params or None,
                json=json,
            ) as response:
                payload = await self._read_json(response)
                if response.status >= 400:
                    self._raise_for_status(response.status, payload)
                return payload
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("Сетевая ошибка MAX API: method=%s path=%s", method, path)
            raise MaxApiNetworkError("Не удалось выполнить запрос к MAX API") from error

    async def _read_json(self, response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            text = await response.text()
            return {"message": text} if text else {}

    def _raise_for_status(self, status: int, payload: Any) -> None:
        error_message, error_code = self._extract_error(payload)
        message = error_message or f"MAX API вернул HTTP {status}"
        if status == 401:
            raise MaxApiAuthError(message, status=status, code=error_code)
        if status == 429:
            raise MaxApiRateLimitError(message, status=status, code=error_code)
        raise MaxApiError(message, status=status, code=error_code)

    def _extract_error(self, payload: Any) -> tuple[str | None, str | None]:
        if not isinstance(payload, dict):
            return None, None

        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("description")
            code = error.get("code") or error.get("error_code")
            return (
                str(message) if message is not None else None,
                str(code) if code is not None else None,
            )
        if isinstance(error, str):
            return error, None

        message = payload.get("message") or payload.get("description")
        code = payload.get("code") or payload.get("error_code")
        return (
            str(message) if message is not None else None,
            str(code) if code is not None else None,
        )
