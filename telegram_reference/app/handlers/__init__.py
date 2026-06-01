from aiogram import Router

from .admin_bookings import router as admin_bookings_router
from .booking_flow import router as booking_flow_router
from .booking_reminders import router as booking_reminders_router
from .clients_directory import router as clients_directory_router
from .dev import router as dev_router
from .fallback import router as fallback_router
from .navigation import router as navigation_router
from .my_bookings import router as my_bookings_router
from .master_photos_settings import router as master_photos_settings_router
from .loyalty_mvp import router as loyalty_mvp_router
from .notifications import router as notifications_router
from .sections import router as sections_router
from .staff import router as staff_router
from .start import router as start_router
from .statistics import router as statistics_router
from .system import router as system_router
from .yclients_setup import router as yclients_setup_router

router = Router()
router.include_router(system_router)
router.include_router(navigation_router)
router.include_router(start_router)
router.include_router(statistics_router)
router.include_router(booking_flow_router)
router.include_router(booking_reminders_router)
router.include_router(admin_bookings_router)
router.include_router(my_bookings_router)
router.include_router(loyalty_mvp_router)
router.include_router(notifications_router)
router.include_router(clients_directory_router)
router.include_router(sections_router)
router.include_router(staff_router)
router.include_router(master_photos_settings_router)
router.include_router(yclients_setup_router)
router.include_router(dev_router)
router.include_router(fallback_router)

__all__ = ["router"]
