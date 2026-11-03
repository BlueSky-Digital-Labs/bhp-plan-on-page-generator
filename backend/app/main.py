import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.access.errors import DependencyUnavailable
from app.access.service import AccessService
from app.access.wiring import build_access_service
from app.api import api_router
from app.api.access import dependency_unavailable
from app.config import Settings, get_settings
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware


def create_app(
    settings: Settings | None = None, *, access_service: AccessService | None = None
) -> FastAPI:
    """Build the app (run with `uvicorn --factory app.main:create_app`).

    Raises ConfigError before serving if required configuration is missing or invalid.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Plan on a Page",
        # Interactive docs and the schema are for local work only.
        docs_url=None if settings.is_deployed else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_deployed else "/api/openapi.json",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.add_exception_handler(DependencyUnavailable, dependency_unavailable)
    app.state.access = access_service or build_access_service(settings)
    app.include_router(api_router)

    # Same-origin serving of the compiled Vite build. Mounted last so /api wins.
    if (settings.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    else:
        logging.getLogger("app").info("frontend_build_absent")
    return app
