from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter()


class StatusResponse(BaseModel):
    status: str
    environment: str


@router.get("/status", response_model=StatusResponse)
def service_status(settings: Annotated[Settings, Depends(get_settings)]) -> StatusResponse:
    """Non-feature-specific endpoint the foundation page calls to prove the round trip."""
    return StatusResponse(status="ok", environment=settings.app_env.value)
