from fastapi import APIRouter

from app.api import (
    access,
    appendix,
    attachments,
    canvas,
    conversation,
    downloads,
    health,
    mail,
    status,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(status.router)
api_router.include_router(access.router)
api_router.include_router(conversation.router)
api_router.include_router(appendix.router)
api_router.include_router(canvas.router)
api_router.include_router(downloads.router)
api_router.include_router(attachments.router)
api_router.include_router(mail.router)
