"""The appendix: the full detail behind the one-page canvas, as PDF, Word or JSON.

Built on demand from the stored conversation and interview state, never stored, and never shortened
by a model. See docs/appendix.md.
"""

import logging
from typing import Literal

from app.appendix.docx_out import render_docx
from app.appendix.json_out import render_json
from app.appendix.model import AppendixError, AppendixInput, ExportSnapshot, build_input
from app.appendix.pdf import render_pdf

logger = logging.getLogger("app.appendix")

Format = Literal["pdf", "docx", "json"]
MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json",
}
_RENDERERS = {"pdf": render_pdf, "docx": render_docx, "json": render_json}


def render(fmt: Format, appendix: AppendixInput) -> bytes:
    """The file's bytes, or AppendixError('render_failed') (the cause is logged without content)."""
    try:
        return _RENDERERS[fmt](appendix)
    except Exception as exc:  # noqa: BLE001 - never leak renderer detail or user text
        logger.error("appendix_render_failed", extra={"exception_type": type(exc).__name__})
        raise AppendixError("render_failed") from None


__all__ = [
    "MEDIA_TYPES",
    "AppendixError",
    "AppendixInput",
    "ExportSnapshot",
    "Format",
    "build_input",
    "render",
]
