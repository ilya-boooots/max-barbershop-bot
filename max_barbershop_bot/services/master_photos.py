"""Master photo helpers for MAX booking and settings flows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from max_barbershop_bot.integrations.yclients.service import YClientsServiceLayer
from max_barbershop_bot.repositories.master_photos import MasterPhoto, MasterPhotosRepository
from max_barbershop_bot.repositories.yclients_settings import YClientsSettingsRepository
from max_barbershop_bot.services.yclients_context import build_yclients_client_from_active_settings, has_required_yclients_credentials

YCLIENTS_NOT_CONFIGURED_TEXT = """YClients пока не настроен 🙏

Сначала добавьте данные подключения."""
MASTER_PHOTOS_LOAD_ERROR_TEXT = """Не удалось загрузить мастеров 🙏

Пожалуйста, попробуйте позже."""
MASTER_PHOTOS_ROOT_TEXT = "🖼️ Выберите мастера для редактирования фото 😊"
MASTER_PHOTOS_EMPTY_TEXT = "😕 Не удалось найти активных мастеров."
MASTER_PHOTO_NON_PHOTO_TEXT = "📸 Пожалуйста, отправьте именно фотографию 🙂"


@dataclass(frozen=True)
class MasterPhotoStaff:
    """YClients master row for photo settings."""

    yclients_staff_id: str
    name: str
    specialization: str | None = None
    has_photo: bool = False


class MasterPhotosService:
    """Resolve, validate and format MAX-compatible master photo data."""

    def __init__(
        self,
        photo_repository: MasterPhotosRepository,
        settings_repository: YClientsSettingsRepository,
    ) -> None:
        self._photo_repository = photo_repository
        self._settings_repository = settings_repository

    def get_photo(self, yclients_staff_id: str | None) -> MasterPhoto | None:
        """Return an active photo for a YClients staff id, if configured."""

        staff_id = _clean_text(yclients_staff_id)
        if not staff_id:
            return None
        return self._photo_repository.get_by_staff_id(staff_id)

    def photo_attachment(self, yclients_staff_id: str | None) -> dict[str, Any] | None:
        """Build a safe MAX image attachment for a stored master photo."""

        return self.prepare_photo_attachment(self.get_photo(yclients_staff_id))

    def prepare_photo_attachment(self, photo: MasterPhoto | None) -> dict[str, Any] | None:
        """Convert a stored photo row into a MAX AttachmentRequest."""

        if photo is None:
            return None
        stored = self._attachment_from_json(photo.photo_attachment_json)
        if stored is not None:
            return stored
        if photo.photo_file_id:
            return {"type": "image", "payload": {"token": photo.photo_file_id}}
        if photo.photo_url:
            return {"type": "image", "payload": {"url": photo.photo_url}}
        return None

    def _attachment_from_json(self, value: str | None) -> dict[str, Any] | None:
        raw = _clean_text(value)
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return _normalize_image_attachment(parsed)

    def extract_photo_reference(self, attachments: list[Any]) -> tuple[str | None, str | None, str | None]:
        """Extract reusable MAX image token/url and a compact attachment JSON from incoming media."""

        for attachment in attachments:
            normalized = _normalize_image_attachment(attachment)
            if normalized is None:
                continue
            payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
            token = _clean_text(payload.get("token") or payload.get("file_id") or normalized.get("token")) or None
            url = _clean_text(payload.get("url") or normalized.get("url")) or None
            return token, url, json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return None, None, None

    def validate_photo_input(self, attachments: list[Any]) -> bool:
        """Return whether the incoming MAX message contains a reusable image attachment."""

        token, url, attachment_json = self.extract_photo_reference(attachments)
        return bool(token or url or attachment_json)

    async def list_yclients_masters(self) -> list[MasterPhotoStaff]:
        """Load active masters from YClients and merge local photo status."""

        settings = self._settings_repository.get_active()
        if not has_required_yclients_credentials(settings):
            raise MasterPhotosNotConfiguredError(YCLIENTS_NOT_CONFIGURED_TEXT)
        try:
            async with build_yclients_client_from_active_settings(settings) as client:
                yclients = YClientsServiceLayer(client, company_id=settings.company_id)
                staff = await yclients.get_available_masters(company_id=settings.company_id, bookable_only=False)
        except MasterPhotosNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep transport details out of admin UX.
            raise MasterPhotosLoadError(MASTER_PHOTOS_LOAD_ERROR_TEXT) from exc

        masters = [
            MasterPhotoStaff(
                yclients_staff_id=item.id,
                name=item.name or "—",
                specialization=item.specialization,
                has_photo=self._photo_repository.has_photo(item.id),
            )
            for item in staff
            if item.id and item.name and item.bookable is not False
        ]
        return sorted(masters, key=lambda item: item.name.lower())

    def format_master_card_text(self, master_name: str, *, has_photo: bool) -> str:
        """Format master photo detail text exactly like the reference flow allows in MAX."""

        if has_photo:
            return f"🖼️ Фото мастера: {master_name}\nМожно заменить или удалить фото"
        return f"🖼️ Для мастера {master_name} фото пока не загружено"


def _normalize_image_attachment(attachment: Any) -> dict[str, Any] | None:
    if not isinstance(attachment, Mapping):
        return None
    attachment_type = _clean_text(attachment.get("type")).lower()
    payload = attachment.get("payload") if isinstance(attachment.get("payload"), Mapping) else {}
    if attachment_type not in {"image", "photo"}:
        return None

    normalized_payload: dict[str, Any] = {}
    token = _clean_text(payload.get("token") or payload.get("file_id") or attachment.get("token"))
    url = _clean_text(payload.get("url") or attachment.get("url"))
    if token:
        normalized_payload["token"] = token
    if url:
        normalized_payload["url"] = url
    if not normalized_payload:
        return None
    return {"type": "image", "payload": normalized_payload}


def _clean_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


class MasterPhotosError(Exception):
    """Base error with a safe user-facing message."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class MasterPhotosNotConfiguredError(MasterPhotosError):
    """YClients settings are missing."""


class MasterPhotosLoadError(MasterPhotosError):
    """YClients staff loading failed."""
