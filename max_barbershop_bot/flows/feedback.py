"""MAX handlers for post-visit feedback."""
from __future__ import annotations

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.services.feedback import (
    NEGATIVE_COMMENT_PROMPT,
    NEGATIVE_THANKS_TEXT,
    NON_TEXT_COMMENT_TEXT,
    POSITIVE_TEXT,
    COMMENT_TOO_LONG_TEXT,
    COMMENT_TOO_SHORT_TEXT,
    INVALID_RATING_TEXT,
    MAX_COMMENT_LENGTH,
    MIN_COMMENT_LENGTH,
    RATING_MISSING_TEXT,
    STALE_TEXT,
    feedback_review_links_keyboard,
    notify_negative_feedback,
    save_negative_comment,
    save_rating,
)

FEEDBACK_COMMENT_SCREEN = "feedback_negative_comment"
_DATABASE_PATH: str | None = None


def configure_feedback_flow(database_path: str) -> None:
    global _DATABASE_PATH
    _DATABASE_PATH = database_path


def register_feedback_routes(router: Router) -> None:
    for rating in range(1, 6):
        router.on_callback(f"fb:rate:{rating}", _handle_rating)
        router.on_callback(f"feedback:rate:{rating}", _handle_rating)
    router.on_screen_text(FEEDBACK_COMMENT_SCREEN, _handle_comment)


async def _handle_rating(context: RouterContext) -> None:
    payload = context.event.callback_payload or ""
    try:
        rating = int(payload.rsplit(":", 1)[-1])
    except ValueError:
        await context.answer_callback()
        await context.send_text(INVALID_RATING_TEXT)
        return
    if rating < 1 or rating > 5:
        await context.answer_callback()
        await context.send_text(INVALID_RATING_TEXT)
        return
    database_path = _database_path_from_context()
    response, negative = save_rating(database_path, platform_user_id=context.event.platform_user_id or "", rating=rating)
    if response is None:
        await context.answer_callback()
        await context.send_text(STALE_TEXT)
        return
    if negative:
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, FEEDBACK_COMMENT_SCREEN)
        await context.send_text(NEGATIVE_COMMENT_PROMPT)
    else:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(POSITIVE_TEXT, keyboard=feedback_review_links_keyboard())
    await context.answer_callback()


async def _handle_comment(context: RouterContext) -> None:
    database_path = _database_path_from_context()
    text = (context.event.text or "").strip()
    if not text:
        await context.send_text(NON_TEXT_COMMENT_TEXT)
        return
    if len(text) < MIN_COMMENT_LENGTH:
        await context.send_text(COMMENT_TOO_SHORT_TEXT)
        return
    if len(text) > MAX_COMMENT_LENGTH:
        await context.send_text(COMMENT_TOO_LONG_TEXT)
        return
    response = save_negative_comment(database_path, platform_user_id=context.event.platform_user_id or "", comment=text)
    if response is None:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(RATING_MISSING_TEXT)
        return
    await context.send_text(NEGATIVE_THANKS_TEXT)
    await notify_negative_feedback(context.sender, database_path=database_path, response=response)
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)


def _database_path_from_context() -> str:
    if _DATABASE_PATH is None:
        raise RuntimeError("Путь к базе данных не настроен для feedback flow")
    return _DATABASE_PATH
