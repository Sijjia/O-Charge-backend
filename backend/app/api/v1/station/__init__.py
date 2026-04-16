from fastapi import APIRouter
from . import status
from . import tariff

router = APIRouter()
router.include_router(status.router)
router.include_router(tariff.router)