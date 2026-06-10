"""Flow registration for the MAX bot."""

from __future__ import annotations

from max_barbershop_bot.core.router import Router
from max_barbershop_bot.flows.fallback import handle_unknown_callback, handle_unknown_text
from max_barbershop_bot.flows.booking import register_booking_routes
from max_barbershop_bot.flows.broadcasts import register_broadcast_routes
from max_barbershop_bot.flows.client_segments import register_client_segment_routes
from max_barbershop_bot.flows.contacts import register_contacts_routes
from max_barbershop_bot.flows.lost_clients import register_lost_clients_routes
from max_barbershop_bot.flows.menu import register_menu_routes
from max_barbershop_bot.flows.my_bookings import register_my_bookings_routes
from max_barbershop_bot.flows.notification_history import register_notification_history_routes
from max_barbershop_bot.flows.registration import register_registration_routes
from max_barbershop_bot.flows.support import register_support_routes
from max_barbershop_bot.flows.yclients_settings import register_yclients_settings_routes
from max_barbershop_bot.flows.staff import register_staff_routes
from max_barbershop_bot.flows.statistics import register_statistics_routes
from max_barbershop_bot.flows.start import handle_bot_started, handle_start


def create_router() -> Router:
    """Create and configure the application router."""

    router = Router()
    router.on_update("bot_started", handle_bot_started)
    router.on_text("/start", handle_start)
    register_menu_routes(router)
    register_broadcast_routes(router)
    register_client_segment_routes(router)
    register_lost_clients_routes(router)
    register_my_bookings_routes(router)
    register_booking_routes(router)
    register_contacts_routes(router)
    register_support_routes(router)
    register_staff_routes(router)
    register_notification_history_routes(router)
    register_statistics_routes(router)
    register_yclients_settings_routes(router)
    register_registration_routes(router)
    router.on_unknown_text(handle_unknown_text)
    router.on_unknown_callback(handle_unknown_callback)
    return router
