from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

WHITE_NOTIFICATION_TYPES = {
    "booking_confirmation",
    "booking_reminder",
    "booking_confirmation_2d",
    "booking_reminder_2h",
    "booking_cancellation_confirmation",
    "booking_reschedule_confirmation",
    "support_reply",
    "admin_direct_message",
    "critical_service",
}

GREEN_NOTIFICATION_TYPES = {
    "manual_broadcast",
    "post_visit_rating",
    "cancellation_recovery",
    "lost_client",
    "birthday",
    "repeat_visit",
    "promotion",
    "holiday_campaign",
}


def get_notification_delivery_type(notification_type: str) -> str:
    if notification_type in WHITE_NOTIFICATION_TYPES:
        return "white"
    if notification_type in GREEN_NOTIFICATION_TYPES:
        return "green"
    logger.warning("unknown_notification_type_classified_green notification_type=%s", notification_type)
    return "green"


def is_white_notification(notification_type: str) -> bool:
    return get_notification_delivery_type(notification_type) == "white"
