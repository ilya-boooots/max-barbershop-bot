"""MAX handlers for post-visit feedback."""
from __future__ import annotations

from max_barbershop_bot.core import state
from max_barbershop_bot.core.router import Router, RouterContext
from max_barbershop_bot.services.feedback import (
    FEEDBACK_ADMIN_NO_ACCESS_TEXT,
    FEEDBACK_ADMIN_REPLY_CANCELLED_TEXT,
    FEEDBACK_ADMIN_REPLY_CLIENT_TEXT,
    FEEDBACK_ADMIN_REPLY_CONFIRM_TEXT,
    FEEDBACK_ADMIN_REPLY_PROMPT,
    FEEDBACK_ADMIN_REPLY_SUCCESS_TEXT,
    FEEDBACK_ADMIN_STALE_TEXT,
    NEGATIVE_COMMENT_PROMPT,
    NEGATIVE_THANKS_TEXT,
    NON_TEXT_COMMENT_TEXT,
    POSITIVE_TEXT,
    STALE_TEXT,
    client_recipient,
    feedback_admin_reply_confirm_keyboard,
    get_feedback_response,
    is_feedback_admin,
    notify_negative_feedback,
    save_feedback_admin_reply,
    save_negative_comment,
    save_rating,
)

FEEDBACK_COMMENT_SCREEN = "feedback_negative_comment"
FEEDBACK_ADMIN_REPLY_SCREEN = "feedback_admin_reply"
FEEDBACK_ADMIN_REPLY_CONFIRM_SCREEN = "feedback_admin_reply_confirm"
_DATABASE_PATH: str | None = None


def configure_feedback_flow(database_path: str) -> None:
    global _DATABASE_PATH
    _DATABASE_PATH = database_path


def register_feedback_routes(router: Router) -> None:
    for rating in range(1, 6):
        router.on_callback(f"feedback:rate:{rating}", _handle_rating)
    router.on_callback_prefix("feedback_admin_reply:", _handle_admin_reply_start)
    router.on_callback_prefix("feedback_admin_reply_confirm:", _handle_admin_reply_confirm)
    router.on_screen_text(FEEDBACK_COMMENT_SCREEN, _handle_comment)
    router.on_screen_text(FEEDBACK_ADMIN_REPLY_SCREEN, _handle_admin_reply_text)


async def _handle_rating(context: RouterContext) -> None:
    payload = context.event.callback_payload or ""
    try:
        rating = int(payload.rsplit(":", 1)[-1])
    except ValueError:
        await context.answer_callback()
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
        await context.send_text(POSITIVE_TEXT)
    await context.answer_callback()


async def _handle_comment(context: RouterContext) -> None:
    database_path = _database_path_from_context()
    text = (context.event.text or "").strip()
    if not text:
        await context.send_text(NON_TEXT_COMMENT_TEXT)
        return
    response = save_negative_comment(database_path, platform_user_id=context.event.platform_user_id or "", comment=text)
    if response is None:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text("⚠️ Эта оценка уже обработана или устарела.")
        return
    await context.send_text(NEGATIVE_THANKS_TEXT)
    await notify_negative_feedback(context.sender, database_path=database_path, response=response)
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)


async def _handle_admin_reply_start(context: RouterContext) -> None:
    database_path = _database_path_from_context()
    if not is_feedback_admin(database_path, context.event.platform_user_id):
        await context.send_text(FEEDBACK_ADMIN_NO_ACCESS_TEXT)
        await context.answer_callback()
        return
    response_id = _response_id_from_payload(context.event.callback_payload, "feedback_admin_reply:")
    if response_id is None:
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        await context.answer_callback()
        return
    response = get_feedback_response(database_path, response_id)
    if response is None:
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        await context.answer_callback()
        return
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, FEEDBACK_ADMIN_REPLY_SCREEN)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_response_id", response_id)
    await context.send_text(FEEDBACK_ADMIN_REPLY_PROMPT)
    await context.answer_callback()


async def _handle_admin_reply_text(context: RouterContext) -> None:
    database_path = _database_path_from_context()
    response_id = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_response_id")
    if not isinstance(response_id, int):
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        return
    if not is_feedback_admin(database_path, context.event.platform_user_id):
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_NO_ACCESS_TEXT)
        return
    response = get_feedback_response(database_path, response_id)
    text = (context.event.text or "").strip()
    if response is None or not text:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        return
    state.set_current_screen(context.event.platform_user_id, context.event.chat_id, FEEDBACK_ADMIN_REPLY_CONFIRM_SCREEN)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_response_id", response_id)
    state.set_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_text", text)
    await context.send_text(FEEDBACK_ADMIN_REPLY_CONFIRM_TEXT.format(text=text), keyboard=feedback_admin_reply_confirm_keyboard())


async def _handle_admin_reply_confirm(context: RouterContext) -> None:
    database_path = _database_path_from_context()
    action = (context.event.callback_payload or "").rsplit(":", 1)[-1]
    response_id = state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_response_id")
    reply_text = str(state.get_state_data_value(context.event.platform_user_id, context.event.chat_id, "admin_reply_text") or "").strip()
    if action == "edit":
        state.set_current_screen(context.event.platform_user_id, context.event.chat_id, FEEDBACK_ADMIN_REPLY_SCREEN)
        await context.send_text(FEEDBACK_ADMIN_REPLY_PROMPT)
        await context.answer_callback()
        return
    if action == "back":
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_REPLY_CANCELLED_TEXT)
        await context.answer_callback()
        return
    if action != "send" or not isinstance(response_id, int) or not reply_text:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        await context.answer_callback()
        return
    if not is_feedback_admin(database_path, context.event.platform_user_id):
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_NO_ACCESS_TEXT)
        await context.answer_callback()
        return
    response = get_feedback_response(database_path, response_id)
    if response is None:
        state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
        await context.send_text(FEEDBACK_ADMIN_STALE_TEXT)
        await context.answer_callback()
        return
    recipient_type, recipient_id = client_recipient(database_path, response)
    if recipient_id is not None:
        if recipient_type == "chat":
            await context.sender.send_to_chat(recipient_id, FEEDBACK_ADMIN_REPLY_CLIENT_TEXT.format(text=reply_text))
        else:
            await context.sender.send_to_user(recipient_id, FEEDBACK_ADMIN_REPLY_CLIENT_TEXT.format(text=reply_text))
    save_feedback_admin_reply(database_path, response_id=response_id, admin_platform_user_id=context.event.platform_user_id or "", text=reply_text)
    state.reset_to_home(context.event.platform_user_id, context.event.chat_id)
    await context.send_text(FEEDBACK_ADMIN_REPLY_SUCCESS_TEXT)
    await context.answer_callback()


def _response_id_from_payload(payload: str | None, prefix: str) -> int | None:
    if not payload or not payload.startswith(prefix):
        return None
    raw = payload[len(prefix):]
    if not raw.isdigit():
        return None
    return int(raw)


def _database_path_from_context() -> str:
    if _DATABASE_PATH is None:
        raise RuntimeError("Путь к базе данных не настроен для feedback flow")
    return _DATABASE_PATH
