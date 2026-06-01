import traceback
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.utils.alerts import notify_dev


class ErrorNotifyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        try:
            return await handler(event, data)
        except Exception:
            err = traceback.format_exc()
            await notify_dev("🔥 Ошибка в апдейте:\n\n" + err)
            raise

