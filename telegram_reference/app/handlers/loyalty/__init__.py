from aiogram import Router

from .operations import router as operations_router
from .history import router as history_router

router = Router()
router.include_router(operations_router)
router.include_router(history_router)

__all__ = ["router"]
