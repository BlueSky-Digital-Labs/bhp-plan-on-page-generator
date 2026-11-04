import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.access.errors import DependencyUnavailable
from app.access.service import AccessService
from app.access.wiring import build_access_service
from app.ai import ConverseStreamAdapter, build_bedrock_runtime_client, get_validated_bedrock_config
from app.ai.policy import Capability
from app.api import api_router
from app.api.access import dependency_unavailable
from app.appendix.summarizer import BedrockSummarizer
from app.config import Settings, get_settings
from app.conversation.producers import ScriptedTestProducer, UnavailableProducer
from app.conversation.service import ConversationService
from app.conversation.store import InMemoryConversationStore, UnavailableConversationStore
from app.errors import register_exception_handlers
from app.interview.engine import Interviewer
from app.interview.extractors import BedrockExtractor
from app.interview.producer import InterviewerProducer
from app.interview.store import InMemoryInterviewStore
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware


class SpaStaticFiles(StaticFiles):
    """Serve the compiled app, falling back to index.html for client-side routes.

    Direct navigation or refresh on /start must load the React app (which then asks the backend
    whether access is allowed). Only extension-less, non-API paths fall back: a missing asset or
    an unknown /api path still returns the JSON 404 envelope. Protection is enforced by the API,
    not by which HTML is served.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            leaf = path.rsplit("/", 1)[-1]
            if exc.status_code == 404 and not path.startswith("api") and "." not in leaf:
                return await super().get_response("index.html", scope)
            raise


def _build_conversation(settings: Settings) -> ConversationService:
    # Deployed: no durable turn store exists yet, so fail closed (503) instead of keeping
    # transcripts in per-process memory behind a load balancer.
    store = UnavailableConversationStore() if settings.is_deployed else InMemoryConversationStore()
    choice = settings.conversation_producer
    if choice == "interviewer_bedrock":
        # Fails at startup (ConfigError) unless an approved Bedrock selection is configured.
        validated = get_validated_bedrock_config()
        adapter = ConverseStreamAdapter(build_bedrock_runtime_client(validated.config), validated)
        extractor = BedrockExtractor(
            adapter, force_tool=validated.supports(Capability.FORCED_TOOL_CHOICE)
        )
        interview_store = InMemoryInterviewStore()
        producer = InterviewerProducer(Interviewer(extractor), interview_store)
        return ConversationService(
            store, producer, interview=interview_store, summarizer=BedrockSummarizer(adapter)
        )
    producer = (
        ScriptedTestProducer(delay_seconds=0.05) if choice == "test" else UnavailableProducer()
    )
    return ConversationService(store, producer)


def create_app(
    settings: Settings | None = None,
    *,
    access_service: AccessService | None = None,
    conversation_service: ConversationService | None = None,
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
    app.state.conversation = conversation_service or _build_conversation(settings)
    app.include_router(api_router)

    # Same-origin serving of the compiled Vite build. Mounted last so /api wins.
    if (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="frontend")
    else:
        logging.getLogger("app").info("frontend_build_absent")
    return app
