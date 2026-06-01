from aiogram import Bot

from app.core.config import get_settings


async def notify_dev(text: str) -> None:
    settings = get_settings()
    try:
        bot = Bot(token=settings.bot_token)
        await bot.send_message(settings.protected_dev_tg_id, text[:3500])
        await bot.session.close()
    except Exception:
        pass
