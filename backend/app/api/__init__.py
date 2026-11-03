from fastapi import APIRouter

from app.api import access, health, status

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(status.router)
api_router.include_router(access.router)
