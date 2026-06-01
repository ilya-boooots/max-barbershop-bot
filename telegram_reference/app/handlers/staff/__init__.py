from aiogram import Router

from .messaging import router as messaging_router
from .panel import router as panel_router
from .personnel import router as personnel_router
from .role_onboarding import router as role_onboarding_router

router = Router()
router.include_router(panel_router)
router.include_router(personnel_router)
router.include_router(messaging_router)
router.include_router(role_onboarding_router)

__all__ = ["router"]
