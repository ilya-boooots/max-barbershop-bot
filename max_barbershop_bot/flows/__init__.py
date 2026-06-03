"""Flow registration for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import Router
from max_barbershop_bot.flows.fallback import handle_unknown_callback, handle_unknown_text
from max_barbershop_bot.flows.menu import register_menu_routes
from max_barbershop_bot.flows.start import handle_bot_started, handle_start


def create_router() -> Router:
    """Create and configure the application router."""

    router = Router()
    router.on_update("bot_started", handle_bot_started)
    router.on_text("/start", handle_start)
    register_menu_routes(router)
    router.on_unknown_text(handle_unknown_text)
    router.on_unknown_callback(handle_unknown_callback)
    return router
