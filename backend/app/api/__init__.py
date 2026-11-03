from fastapi import APIRouter

from app.api import health, status

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(status.router)
