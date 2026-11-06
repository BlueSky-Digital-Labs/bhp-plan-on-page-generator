"""The names the page shows. Public (the sign-in page needs them) and not secret."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter()


@router.get("/branding")
def branding(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    """App name, header text and footer text from configuration: shown by the page as plain text,
    so changing them needs a restart and no rebuild."""
    return {
        "app_name": settings.app_name,
        "header_text": settings.header_text,
        "footer_text": settings.footer_text,
    }
