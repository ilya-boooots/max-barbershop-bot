from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import get_settings


def feedback_stars_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="fb:rate:5")],
            [InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="fb:rate:4")],
            [InlineKeyboardButton(text="⭐⭐⭐", callback_data="fb:rate:3")],
            [InlineKeyboardButton(text="⭐⭐", callback_data="fb:rate:2")],
            [InlineKeyboardButton(text="⭐", callback_data="fb:rate:1")],
        ]
    )


def feedback_public_review_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Яндекс Карты", url=get_settings().yandex_review_url)],
            [InlineKeyboardButton(text="2GIS", url=get_settings().two_gis_review_url)],
        ]
    )


def feedback_admin_actions_kb(feedback_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Ответить гостю", callback_data=f"fb:reply:{feedback_id}")],
            [InlineKeyboardButton(text="✅ Закрыть", callback_data=f"fb:close:{feedback_id}")],
        ]
    )
